import uuid
from datetime import date
from decimal import Decimal
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException, status, UploadFile, File, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user, require_permission, get_request_redis
from app.models.user import User
from app.schemas.inventory_workflow import (
    DailyStockRecordCreate,
    DailyStockRecountCreate,
    DailyStockReviewCreate,
    DailyStockOut,
    DailyStockBulkRowPreview,
    DailyStockBulkPreviewResponse,
    DailyStockBulkCommitRequest,
    DailyStockBulkCommitResponse,
)
from app.services.daily_stock_service import (
    record_daily_stock,
    submit_recount,
    review_reconciliation,
    get_daily_stock,
    list_daily_stock,
    check_idempotency,
    log_idempotency,
    generate_daily_stock_template,
    parse_and_validate_daily_stock_file,
    store_daily_stock_token,
    retrieve_and_consume_daily_stock_token,
)
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/daily-stock", tags=["Daily Stock Reconciliation"])


def _enforce_company_isolation(current_user: User, target_company_id: Optional[int]) -> int:
    """Area F: Strict Tenant Isolation Gatekeeper."""
    if getattr(current_user, "is_super_admin", False):
        return target_company_id or current_user.company_id or 1
    if target_company_id is not None and current_user.company_id != target_company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cross-company access is strictly prohibited",
        )
    return current_user.company_id


@router.post("/record", response_model=DailyStockOut, status_code=status.HTTP_201_CREATED)
async def record_stock_endpoint(
    payload: DailyStockRecordCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.create")),
):
    """
    Records daily physical stock count and transitions state machine.
    Idempotent replay executed before version check.
    """
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    if payload.idempotency_key:
        cached = await check_idempotency(db, eff_comp, payload.idempotency_key)
        if cached:
            return cached

    dsr = await record_daily_stock(
        db=db,
        company_id=eff_comp,
        user_id=current_user.id,
        project_id=payload.project_id,
        material_id=payload.material_id,
        physical_count=payload.physical_count,
        reconciliation_date=payload.reconciliation_date,
        remarks=payload.remarks,
        idempotency_key=payload.idempotency_key,
    )

    if payload.idempotency_key:
        out_dict = DailyStockOut.model_validate(dsr).model_dump()
        await log_idempotency(
            db,
            eff_comp,
            payload.idempotency_key,
            "/daily-stock/record",
            out_dict,
        )

    return dsr


@router.post("/{dsr_id}/recount", response_model=DailyStockOut)
async def recount_stock_endpoint(
    dsr_id: int,
    payload: DailyStockRecountCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.create")),
):
    """Submits a recount, increments version, and archives recount history."""
    dsr = await get_daily_stock(db=db, dsr_id=dsr_id)
    if not dsr:
        raise NotFoundError(f"Daily stock reconciliation {dsr_id} not found")
    _enforce_company_isolation(current_user, dsr.company_id)

    return await submit_recount(
        db=db,
        dsr_id=dsr_id,
        user_id=current_user.id,
        new_physical_count=payload.new_physical_count,
        recount_notes=payload.recount_notes,
    )


@router.post("/{dsr_id}/review", response_model=DailyStockOut)
async def review_stock_endpoint(
    dsr_id: int,
    payload: DailyStockReviewCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.reconcile")),
):
    """Maker-checker review step: accept variance, audit, or approve inventory adjustment."""
    dsr = await get_daily_stock(db=db, dsr_id=dsr_id)
    if not dsr:
        raise NotFoundError(f"Daily stock reconciliation {dsr_id} not found")
    _enforce_company_isolation(current_user, dsr.company_id)

    return await review_reconciliation(
        db=db,
        dsr_id=dsr_id,
        reviewer_id=current_user.id,
        review_outcome=payload.review_outcome,
        review_notes=payload.review_notes,
    )


@router.get("/list", response_model=List[DailyStockOut])
async def list_daily_stock_endpoint(
    company_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    reconciliation_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.view")),
):
    """Lists reconciliation records with filters."""
    eff_comp = _enforce_company_isolation(current_user, company_id)
    return await list_daily_stock(
        db=db,
        company_id=eff_comp,
        project_id=project_id,
        status=status,
        reconciliation_date=reconciliation_date,
        skip=skip,
        limit=limit,
    )


@router.get("/{dsr_id}", response_model=DailyStockOut)
async def get_daily_stock_endpoint(
    dsr_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.view")),
):
    """Retrieves a single reconciliation record."""
    dsr = await get_daily_stock(db=db, dsr_id=dsr_id)
    if not dsr:
        raise NotFoundError(f"Daily stock reconciliation {dsr_id} not found")
    _enforce_company_isolation(current_user, dsr.company_id)
    return dsr


# ================= DAILY STOCK BULK UPLOAD ENDPOINTS =================

@router.get("/bulk-upload/template")
async def get_daily_stock_template_endpoint(
    current_user: User = Depends(require_permission("daily_stock.create")),
):
    """Returns downloadable Excel template (.xlsx) for bulk daily stock reconciliation."""
    file_stream = generate_daily_stock_template()
    filename = "daily_stock_upload_template.xlsx"
    return Response(
        content=file_stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/bulk-upload/preview", response_model=DailyStockBulkPreviewResponse)
async def preview_daily_stock_bulk_endpoint(
    file: UploadFile = File(...),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.create")),
    redis = Depends(get_request_redis),
):
    """Validates bulk daily stock file and returns preview with a single-use commit token."""
    eff_comp = _enforce_company_isolation(current_user, company_id)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds maximum limit of 5 MB.")

    valid_items, validation_errors = await parse_and_validate_daily_stock_file(
        db=db,
        file_bytes=content,
        filename=file.filename or "upload.xlsx",
        company_id=eff_comp,
    )

    import_token = None
    if valid_items and not validation_errors:
        import_token = f"DSR-IMPORT-{uuid.uuid4().hex}"
        await store_daily_stock_token(
            token=import_token,
            items=valid_items,
            redis=redis,
            user_id=current_user.id,
            company_id=eff_comp,
        )

    preview_rows = [
        DailyStockBulkRowPreview(
            row_number=item["row_number"],
            project_id=item["project_id"],
            material_id=item["material_id"],
            material_name=item["material_name"],
            physical_count=item["physical_count"],
            reconciliation_date=item["reconciliation_date"],
            remarks=item["remarks"],
            is_valid=True,
            errors=[],
        )
        for item in valid_items
    ]

    return DailyStockBulkPreviewResponse(
        total_rows=len(valid_items) + len(validation_errors),
        valid_rows=len(valid_items),
        invalid_rows=len(validation_errors),
        import_token=import_token,
        preview_rows=preview_rows,
        errors=validation_errors,
    )


@router.post("/bulk-upload/commit", response_model=DailyStockBulkCommitResponse)
async def commit_daily_stock_bulk_endpoint(
    payload: DailyStockBulkCommitRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("daily_stock.create")),
    redis = Depends(get_request_redis),
):
    """Commits validated bulk daily stock entries using the single-use import token."""
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    items = await retrieve_and_consume_daily_stock_token(
        token=payload.import_token,
        redis=redis,
        user_id=current_user.id,
        company_id=eff_comp,
    )
    if items is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid, expired, or already consumed import token. Please preview the file again.",
        )

    committed_recons = []
    for item in items:
        dsr = await record_daily_stock(
            db=db,
            company_id=eff_comp,
            user_id=current_user.id,
            project_id=item["project_id"],
            material_id=item["material_id"],
            physical_count=Decimal(str(item["physical_count"])),
            reconciliation_date=item["reconciliation_date"],
            remarks=item["remarks"],
        )
        committed_recons.append(dsr.reconciliation_number)

    return DailyStockBulkCommitResponse(
        success=True,
        message=f"Successfully committed {len(committed_recons)} daily stock reconciliation records.",
        committed_count=len(committed_recons),
        reconciliation_numbers=committed_recons,
    )

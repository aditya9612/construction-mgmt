from typing import List, Optional
import uuid
from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user, require_permission, get_request_redis
from app.models.user import User
from app.schemas.inventory_workflow import (
    MaterialIssueCreate,
    MaterialIssueOut,
    BulkOutwardRowPreview,
    BulkOutwardPreviewResponse,
    BulkOutwardCommitRequest,
    BulkOutwardCommitResponse,
)
from app.services.outward_service import (
    create_material_issue,
    get_material_issue,
    list_material_issues,
    generate_bulk_outward_template,
    parse_and_validate_bulk_outward_file,
    store_bulk_outward_token,
    retrieve_and_consume_bulk_outward_token,
)
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/outward", tags=["Outward / Material Issue Note"])


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


@router.post("/issue/create", response_model=MaterialIssueOut, status_code=status.HTTP_201_CREATED)
async def create_issue_endpoint(
    payload: MaterialIssueCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("outward.create")),
):
    """
    Creates an atomic outward Material Issue (MIN) with FIFO batch depletion
    and Ind AS 2 Moving Weighted Average financial valuation.
    """
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    return await create_material_issue(
        db=db,
        company_id=eff_comp,
        user_id=current_user.id,
        project_id=payload.project_id,
        material_id=payload.material_id,
        quantity=payload.quantity,
        issue_type=payload.issue_type,
        issued_to=payload.issued_to,
        contractor_id=payload.contractor_id,
        boq_item_id=payload.boq_item_id,
        idempotency_key=payload.idempotency_key,
        remarks=payload.remarks,
    )


@router.get("/issue/list", response_model=List[MaterialIssueOut])
async def list_issue_endpoint(
    company_id: Optional[int] = Query(None),
    project_id: Optional[int] = Query(None),
    material_id: Optional[int] = Query(None),
    issue_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("outward.view")),
):
    """Lists Material Issues with filters."""
    eff_comp = _enforce_company_isolation(current_user, company_id)
    return await list_material_issues(
        db=db,
        company_id=eff_comp,
        project_id=project_id,
        material_id=material_id,
        issue_type=issue_type,
        skip=skip,
        limit=limit,
    )


@router.get("/bulk-upload/template")
async def get_bulk_outward_template_endpoint(
    current_user: User = Depends(require_permission("outward.create")),
):
    """Returns downloadable Excel template (.xlsx) for bulk outward issue upload."""
    file_stream = generate_bulk_outward_template()
    return Response(
        content=file_stream.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="bulk_outward_template.xlsx"'},
    )


@router.post("/bulk-upload/preview", response_model=BulkOutwardPreviewResponse)
async def preview_bulk_outward_endpoint(
    file: UploadFile = File(...),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("outward.create")),
    redis=Depends(get_request_redis),
):
    """Validates bulk outward issue file and returns preview with a single-use commit token."""
    eff_comp = _enforce_company_isolation(current_user, company_id)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds maximum limit of 5 MB.")

    valid_items, validation_errors = await parse_and_validate_bulk_outward_file(
        db=db,
        file_bytes=content,
        filename=file.filename or "outward.xlsx",
        company_id=eff_comp,
    )

    import_token = None
    if valid_items and not validation_errors:
        import_token = f"OUT-IMPORT-{uuid.uuid4().hex}"
        await store_bulk_outward_token(
            token=import_token,
            items=valid_items,
            redis=redis,
            user_id=current_user.id,
            company_id=eff_comp,
        )

    preview_rows = [
        BulkOutwardRowPreview(
            row_number=item["row_number"],
            project_id=item["project_id"],
            material_id=item["material_id"],
            material_name=item["material_name"],
            quantity=item["quantity"],
            issue_type=item["issue_type"],
            issued_to=item["issued_to"],
            contractor_id=item["contractor_id"],
            boq_item_id=item["boq_item_id"],
            remarks=item["remarks"],
            is_valid=True,
            errors=[],
        )
        for item in valid_items
    ]
    return BulkOutwardPreviewResponse(
        total_rows=len(valid_items) + len(validation_errors),
        valid_rows=len(valid_items),
        invalid_rows=len(validation_errors),
        import_token=import_token,
        preview_rows=preview_rows,
        errors=validation_errors,
    )


@router.post("/bulk-upload/commit", response_model=BulkOutwardCommitResponse)
async def commit_bulk_outward_endpoint(
    payload: BulkOutwardCommitRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("outward.create")),
    redis=Depends(get_request_redis),
):
    """Commits validated bulk outward issues using the single-use import token."""
    eff_comp = _enforce_company_isolation(current_user, payload.company_id)
    items = await retrieve_and_consume_bulk_outward_token(
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

    issue_numbers = []
    for idx, item in enumerate(items, start=1):
        issue = await create_material_issue(
            db=db,
            company_id=eff_comp,
            user_id=current_user.id,
            project_id=item["project_id"],
            material_id=item["material_id"],
            quantity=item["quantity"],
            issue_type=item["issue_type"],
            issued_to=item["issued_to"],
            contractor_id=item["contractor_id"],
            boq_item_id=item["boq_item_id"],
            idempotency_key=f"bulk-out:{payload.import_token}:{idx}",
            remarks=item["remarks"],
        )
        issue_numbers.append(issue.issue_number)

    return BulkOutwardCommitResponse(
        success=True,
        message=f"Successfully committed {len(issue_numbers)} outward material issues.",
        committed_count=len(issue_numbers),
        issue_numbers=issue_numbers,
    )


@router.get("/issue/{issue_id}", response_model=MaterialIssueOut)
async def get_issue_endpoint(
    issue_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("outward.view")),
):
    """Retrieves a single Material Issue Note with its allocations."""
    issue = await get_material_issue(db=db, issue_id=issue_id)
    if not issue:
        raise NotFoundError(f"Material Issue {issue_id} not found")
    _enforce_company_isolation(current_user, issue.company_id)
    return issue

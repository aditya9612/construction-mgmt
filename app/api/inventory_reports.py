from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, status, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.core.dependencies import (
    get_current_active_user,
    require_permission,
    check_user_has_permission,
)
from app.models.user import User
from app.services.inventory_report_service import (
    get_stock_summary,
    get_low_stock_alerts,
    get_batch_inventory,
    get_po_fulfillment_report,
    get_grn_register,
    get_issue_register,
    get_daily_stock_variance_report,
    get_supplier_wise_stock,
    get_daily_movement_summary,
    get_material_movement_register,
    get_combined_material_movement_report,
    get_inventory_audit_trail_report,
    export_report_to_excel,
    export_report_to_pdf,
)

router = APIRouter(prefix="/inventory/reports", tags=["Inventory Reports"])


def _check_tenant(current_user: User, company_id: Optional[int]) -> Optional[int]:
    """Enforces tenant isolation for reporting endpoints (Area F)."""
    if getattr(current_user, "is_super_admin", False):
        return company_id
    if company_id is not None and current_user.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Cross-company access is strictly prohibited",
        )
    return current_user.company_id


@router.get("/stock-summary")
async def stock_summary_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns aggregated stock summary per project and material with valuation."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_stock_summary(
        db=db,
        project_id=project_id,
        company_id=eff_comp,
        include_price=can_view_price,
    )


@router.get("/low-stock-alerts")
async def low_stock_alerts_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    threshold: Decimal = Query(Decimal("10.000")),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns list of materials below the stock threshold (tenant-isolated)."""
    eff_comp = _check_tenant(current_user, company_id)
    return await get_low_stock_alerts(
        db=db,
        project_id=project_id,
        threshold=threshold,
        company_id=eff_comp,
    )


@router.get("/batch-inventory")
async def batch_inventory_endpoint(
    project_id: int = Query(...),
    material_master_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns active batches with balances and purchase rates for FIFO auditing (tenant-isolated)."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_batch_inventory(
        db=db,
        project_id=project_id,
        material_master_id=material_master_id,
        include_price=can_view_price,
        company_id=eff_comp,
    )


@router.get("/po-fulfillment")
async def po_fulfillment_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns PO tracking and item-level fulfillment stats."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_po_fulfillment_report(
        db=db,
        project_id=project_id,
        company_id=eff_comp,
        include_price=can_view_price,
    )


@router.get("/grn-register")
async def grn_register_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns register of all GRN inward entries with QC results and actor details."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_grn_register(
        db=db,
        project_id=project_id,
        company_id=eff_comp,
        include_price=can_view_price,
    )


@router.get("/issue-register")
async def issue_register_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns register of all Material Issues (MIN) with allocations and actor details."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_issue_register(
        db=db,
        project_id=project_id,
        company_id=eff_comp,
        include_price=can_view_price,
    )


@router.get("/daily-stock-variance")
async def daily_stock_variance_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Returns Daily Stock reconciliations focusing on variances and reviews."""
    eff_comp = _check_tenant(current_user, company_id)
    return await get_daily_stock_variance_report(db=db, project_id=project_id, company_id=eff_comp)


@router.get("/supplier-stock")
async def supplier_stock_endpoint(
    project_id: Optional[int] = Query(None),
    material_master_id: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Area B: Supplier-wise active stock report alongside site-consolidated balance."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_supplier_wise_stock(
        db=db,
        project_id=project_id,
        material_master_id=material_master_id,
        supplier_id=supplier_id,
        company_id=eff_comp,
        include_price=can_view_price,
    )


@router.get("/daily-movement")
async def daily_movement_endpoint(
    project_id: Optional[int] = Query(None),
    movement_date: Optional[date] = Query(None),
    company_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Area E: Daily received and issued summary for a project/date."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_daily_movement_summary(
        db=db,
        project_id=project_id,
        movement_date=movement_date,
        company_id=eff_comp,
        include_price=can_view_price,
    )


@router.get("/movement-register")
async def movement_register_endpoint(
    material_id: int = Query(...),
    company_id: Optional[int] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Area E: Individual material movement register with chronological running balances."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_material_movement_register(
        db=db,
        material_id=material_id,
        start_date=start_date,
        end_date=end_date,
        include_price=can_view_price,
        company_id=eff_comp,
    )


@router.get("/combined-movement")
async def combined_movement_endpoint(
    project_id: Optional[int] = Query(None),
    material_master_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """Combined material movement summary (opening, inward, outward, closing stock, MWA valuation)."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    return await get_combined_material_movement_report(
        db=db,
        company_id=eff_comp,
        project_id=project_id,
        material_master_id=material_master_id,
        start_date=start_date,
        end_date=end_date,
        include_price=can_view_price,
    )


@router.get("/audit-trail")
async def inventory_audit_trail_endpoint(
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.view")),
):
    """User activity and inventory audit trail across PO, GRN, MIN, DSR, and OTP cancellations."""
    eff_comp = _check_tenant(current_user, company_id)
    return await get_inventory_audit_trail_report(
        db=db,
        company_id=eff_comp,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )


# ================= REPORT EXPORT ENDPOINTS =================

async def _fetch_report_data(
    report_name: str,
    db: AsyncSession,
    eff_comp: Optional[int],
    project_id: Optional[int],
    material_id: Optional[int],
    material_master_id: Optional[int],
    supplier_id: Optional[int],
    threshold: Decimal,
    movement_date: Optional[date],
    start_date: Optional[datetime],
    end_date: Optional[datetime],
    can_view_price: bool,
) -> Any:
    """Internal helper to dispatch report generation with filters and price masking."""
    name = report_name.lower().strip()
    if name in ("stock_summary", "stock-summary"):
        return await get_stock_summary(db=db, project_id=project_id, company_id=eff_comp, include_price=can_view_price)
    elif name in ("low_stock_alerts", "low-stock-alerts"):
        return await get_low_stock_alerts(db=db, project_id=project_id, threshold=threshold, company_id=eff_comp)
    elif name in ("batch_inventory", "batch-inventory"):
        if not project_id:
            raise HTTPException(status_code=400, detail="project_id is required for batch inventory export")
        return await get_batch_inventory(db=db, project_id=project_id, material_master_id=material_master_id, include_price=can_view_price, company_id=eff_comp)
    elif name in ("po_fulfillment", "po-fulfillment"):
        return await get_po_fulfillment_report(db=db, project_id=project_id, company_id=eff_comp, include_price=can_view_price)
    elif name in ("grn_register", "grn-register"):
        return await get_grn_register(db=db, project_id=project_id, company_id=eff_comp, include_price=can_view_price)
    elif name in ("issue_register", "issue-register"):
        return await get_issue_register(db=db, project_id=project_id, company_id=eff_comp, include_price=can_view_price)
    elif name in ("daily_stock_variance", "daily-stock-variance"):
        return await get_daily_stock_variance_report(db=db, project_id=project_id, company_id=eff_comp)
    elif name in ("supplier_stock", "supplier-stock"):
        return await get_supplier_wise_stock(db=db, project_id=project_id, material_master_id=material_master_id, supplier_id=supplier_id, company_id=eff_comp, include_price=can_view_price)
    elif name in ("daily_movement", "daily-movement"):
        return await get_daily_movement_summary(db=db, project_id=project_id, movement_date=movement_date, company_id=eff_comp, include_price=can_view_price)
    elif name in ("movement_register", "movement-register"):
        if not material_id:
            raise HTTPException(status_code=400, detail="material_id is required for movement register export")
        return await get_material_movement_register(db=db, material_id=material_id, start_date=start_date, end_date=end_date, include_price=can_view_price, company_id=eff_comp)
    elif name in ("combined_movement", "combined-movement"):
        return await get_combined_material_movement_report(db=db, company_id=eff_comp, project_id=project_id, material_master_id=material_master_id, start_date=start_date, end_date=end_date, include_price=can_view_price)
    elif name in ("audit_trail", "audit-trail"):
        return await get_inventory_audit_trail_report(db=db, company_id=eff_comp, project_id=project_id, start_date=start_date, end_date=end_date)
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported report name '{report_name}'.")


@router.get("/export/excel")
async def export_inventory_report_excel_endpoint(
    report_name: str = Query(..., description="Report name identifier"),
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    material_id: Optional[int] = Query(None),
    material_master_id: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    threshold: Decimal = Query(Decimal("10.000")),
    movement_date: Optional[date] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.export")),
):
    """Exports any inventory report directly to formatted Excel (.xlsx)."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    data = await _fetch_report_data(
        report_name=report_name,
        db=db,
        eff_comp=eff_comp,
        project_id=project_id,
        material_id=material_id,
        material_master_id=material_master_id,
        supplier_id=supplier_id,
        threshold=threshold,
        movement_date=movement_date,
        start_date=start_date,
        end_date=end_date,
        can_view_price=can_view_price,
    )
    buf = export_report_to_excel(report_name, data)
    filename = f"{report_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return Response(
        content=buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/pdf")
async def export_inventory_report_pdf_endpoint(
    report_name: str = Query(..., description="Report name identifier"),
    project_id: Optional[int] = Query(None),
    company_id: Optional[int] = Query(None),
    material_id: Optional[int] = Query(None),
    material_master_id: Optional[int] = Query(None),
    supplier_id: Optional[int] = Query(None),
    threshold: Decimal = Query(Decimal("10.000")),
    movement_date: Optional[date] = Query(None),
    start_date: Optional[datetime] = Query(None),
    end_date: Optional[datetime] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("report.export")),
):
    """Exports any inventory report directly to landscape PDF."""
    eff_comp = _check_tenant(current_user, company_id)
    can_view_price = await check_user_has_permission(db, current_user, "report.view_price")
    data = await _fetch_report_data(
        report_name=report_name,
        db=db,
        eff_comp=eff_comp,
        project_id=project_id,
        material_id=material_id,
        material_master_id=material_master_id,
        supplier_id=supplier_id,
        threshold=threshold,
        movement_date=movement_date,
        start_date=start_date,
        end_date=end_date,
        can_view_price=can_view_price,
    )
    buf = export_report_to_pdf(report_name, data)
    filename = f"{report_name}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf"
    return Response(
        content=buf.getvalue(),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

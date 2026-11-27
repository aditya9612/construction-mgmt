import io
from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Any, Tuple

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

from sqlalchemy import select, and_, func, or_, case
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.material import (
    Material,
    PurchaseOrder,
    MaterialTransaction,
    MaterialLedger,
    Supplier,
)
from app.models.purchase_order import PurchaseOrderItem
from app.models.master_data import MaterialMaster
from app.models.project import Project
from app.models.user import User, ActivityLog
from app.models.cancellation import OTPCancellationRequest
from app.models.inventory import (
    StockBatch,
    ProjectMaterialValuation,
    GoodsReceiptNote,
    GoodsReceiptNoteItem,
    MaterialIssue,
    MaterialIssueAllocation,
)
from app.models.daily_stock import (
    DailyStockReconciliation,
    DailyStockRecountHistory,
)
from app.utils.helpers import AppError


async def get_stock_summary(
    db: AsyncSession,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
    include_price: bool = True,
) -> List[Dict[str, Any]]:
    """Returns aggregated stock summary per project and material."""
    stmt = (
        select(
            Material.id,
            Material.material_code,
            Material.material_name,
            Material.project_id,
            Material.remaining_stock,
            Material.quantity_purchased,
            Material.quantity_used,
            Material.purchase_rate,
            ProjectMaterialValuation.current_moving_weighted_average_rate,
        )
        .join(Project, Project.id == Material.project_id)
        .outerjoin(
            ProjectMaterialValuation,
            and_(
                ProjectMaterialValuation.project_id == Material.project_id,
                ProjectMaterialValuation.material_master_id == Material.material_master_id,
            ),
        )
        .where(Material.is_deleted == False)
    )
    if company_id is not None:
        stmt = stmt.where(Project.company_id == company_id)
    if project_id:
        stmt = stmt.where(Material.project_id == project_id)

    results = (await db.execute(stmt)).all()
    report = []
    for row in results:
        mwa = row.current_moving_weighted_average_rate or row.purchase_rate or Decimal("0.00")
        stock = row.remaining_stock or Decimal("0.000")
        val = (stock * mwa).quantize(Decimal("0.01"))
        report.append(
            {
                "material_id": row.id,
                "material_code": row.material_code,
                "material_name": row.material_name,
                "project_id": row.project_id,
                "remaining_stock": stock,
                "quantity_purchased": row.quantity_purchased,
                "quantity_used": row.quantity_used,
                "unit_rate": row.purchase_rate if include_price else None,
                "moving_average_rate": mwa if include_price else None,
                "total_inventory_value": val if include_price else None,
            }
        )
    return report


async def get_low_stock_alerts(
    db: AsyncSession,
    project_id: Optional[int] = None,
    threshold: Optional[Decimal] = None,
    company_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Returns list of materials below the stock threshold.
    Authoritative Policy:
    1. Evaluates each material's explicit minimum_stock_level if configured.
    2. Falls back to documented standard threshold (10.000) or query override if NULL.
    """
    fallback_threshold = threshold if threshold is not None else Decimal("10.000")
    effective_threshold = case(
        (
            and_(
                Material.minimum_stock_level.isnot(None),
                Material.minimum_stock_level > Decimal("0.000"),
            ),
            Material.minimum_stock_level,
        ),
        else_=fallback_threshold,
    )

    stmt = (
        select(Material)
        .join(Project, Project.id == Material.project_id)
        .where(
            and_(
                Material.is_deleted == False,
                Material.remaining_stock <= effective_threshold,
            )
        )
    )
    if company_id is not None:
        stmt = stmt.where(Project.company_id == company_id)
    if project_id:
        stmt = stmt.where(Material.project_id == project_id)

    materials = (await db.execute(stmt)).scalars().all()
    return [
        {
            "material_id": m.id,
            "material_code": m.material_code,
            "material_name": m.material_name,
            "project_id": m.project_id,
            "remaining_stock": m.remaining_stock,
            "threshold": (
                m.minimum_stock_level
                if (m.minimum_stock_level is not None and m.minimum_stock_level > Decimal("0.000"))
                else fallback_threshold
            ),
            "status": "CRITICAL" if m.remaining_stock <= Decimal("0.000") else "LOW",
        }
        for m in materials
    ]


async def get_batch_inventory(
    db: AsyncSession,
    project_id: int,
    material_master_id: Optional[int] = None,
    include_price: bool = True,
    company_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Returns active batches with balances and purchase rates for FIFO auditing."""
    stmt = (
        select(StockBatch)
        .join(Project, Project.id == StockBatch.project_id)
        .where(
            and_(
                StockBatch.project_id == project_id,
                StockBatch.current_balance > Decimal("0.000"),
                StockBatch.status == "ACTIVE",
            )
        )
        .order_by(StockBatch.receipt_date.asc(), StockBatch.id.asc())
    )
    if company_id is not None:
        stmt = stmt.where(Project.company_id == company_id)
    if material_master_id:
        stmt = stmt.where(StockBatch.material_master_id == material_master_id)

    batches = (await db.execute(stmt)).scalars().all()
    return [
        {
            "batch_id": b.id,
            "batch_number": b.batch_number,
            "material_master_id": b.material_master_id,
            "initial_quantity": b.initial_quantity,
            "current_balance": b.current_balance,
            "purchase_rate": b.purchase_rate if include_price else None,
            "receipt_date": b.receipt_date.isoformat() if b.receipt_date else None,
            "batch_source": b.batch_source,
            "batch_value": (b.current_balance * b.purchase_rate).quantize(Decimal("0.01")) if include_price else None,
        }
        for b in batches
    ]


async def get_po_fulfillment_report(
    db: AsyncSession,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
    include_price: bool = True,
) -> List[Dict[str, Any]]:
    """Returns PO tracking and item-level fulfillment stats including creator/approver metadata."""
    stmt = (
        select(PurchaseOrder)
        .options(
            selectinload(PurchaseOrder.items),
            selectinload(PurchaseOrder.supplier),
        )
        .where(PurchaseOrder.is_deleted == False)
    )
    if project_id:
        stmt = stmt.where(PurchaseOrder.project_id == project_id)
    if company_id:
        stmt = stmt.where(PurchaseOrder.company_id == company_id)

    orders = (await db.execute(stmt)).scalars().all()
    po_ids = [po.id for po in orders]
    creator_by_po: Dict[int, int] = {}
    if po_ids:
        act_stmt = (
            select(ActivityLog.entity_id, ActivityLog.performed_by)
            .where(
                and_(
                    ActivityLog.entity == "PurchaseOrder",
                    ActivityLog.entity_id.in_(po_ids),
                    ActivityLog.action.in_(["CREATE_PO", "CREATE"]),
                )
            )
            .order_by(ActivityLog.id.asc())
        )
        for ent_id, actor_id in (await db.execute(act_stmt)).all():
            if ent_id is not None and actor_id is not None and ent_id not in creator_by_po:
                creator_by_po[ent_id] = actor_id

    user_ids = {po.approved_by_id for po in orders if po.approved_by_id} | set(creator_by_po.values())
    user_names: Dict[int, str] = {}
    if user_ids:
        u_stmt = select(User.id, User.full_name, User.email).where(User.id.in_(user_ids))
        for uid, fname, email in (await db.execute(u_stmt)).all():
            user_names[uid] = fname or email or f"User {uid}"

    report = []
    for po in orders:
        items_data = []
        for it in po.items:
            items_data.append(
                {
                    "item_id": it.id,
                    "material_name": it.material_name,
                    "ordered_quantity": it.quantity,
                    "received_quantity": it.received_quantity,
                    "pending_quantity": it.quantity - it.received_quantity,
                    "rate": it.rate if include_price else None,
                    "total_amount": it.total_amount if include_price else None,
                    "status": it.status,
                }
            )
        creator_id = creator_by_po.get(po.id)
        report.append(
            {
                "po_id": po.id,
                "po_number": po.po_number,
                "supplier_name": po.supplier.supplier_name if po.supplier else "N/A",
                "status": po.status,
                "total_quantity": po.quantity,
                "total_amount": po.total_amount if include_price else None,
                "created_by_id": creator_id,
                "created_by_name": user_names.get(creator_id) if creator_id else None,
                "approved_by_id": po.approved_by_id,
                "approved_by_name": user_names.get(po.approved_by_id) if po.approved_by_id else None,
                "approved_at": po.approved_at.isoformat() if po.approved_at else None,
                "items": items_data,
            }
        )
    return report


async def get_grn_register(
    db: AsyncSession,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
    include_price: bool = True,
) -> List[Dict[str, Any]]:
    """Returns register of all GRN inward entries with QC results and actor details."""
    stmt = (
        select(GoodsReceiptNote)
        .options(
            selectinload(GoodsReceiptNote.items),
            selectinload(GoodsReceiptNote.supplier),
            selectinload(GoodsReceiptNote.created_by),
            selectinload(GoodsReceiptNote.verified_by),
        )
    )
    if project_id:
        stmt = stmt.where(GoodsReceiptNote.project_id == project_id)
    if company_id:
        stmt = stmt.where(GoodsReceiptNote.company_id == company_id)

    grns = (await db.execute(stmt)).scalars().all()
    report = []
    for g in grns:
        items = [
            {
                "item_id": item.id,
                "material_master_id": item.material_master_id,
                "received_quantity": item.received_quantity,
                "accepted_quantity": item.accepted_quantity,
                "rejected_quantity": item.rejected_quantity,
                "unit_rate": item.unit_rate if include_price else None,
                "total_amount": item.total_amount if include_price else None,
                "qc_remarks": item.qc_remarks,
            }
            for item in g.items
        ]
        created_by_name = (
            g.created_by.full_name or g.created_by.email if g.created_by else f"User {g.created_by_id}"
        )
        verified_by_name = (
            g.verified_by.full_name or g.verified_by.email
            if g.verified_by
            else (f"User {g.verified_by_id}" if g.verified_by_id else None)
        )
        report.append(
            {
                "grn_id": g.id,
                "grn_number": g.grn_number,
                "supplier_name": g.supplier.supplier_name if g.supplier else "N/A",
                "status": g.status,
                "challan_number": g.challan_number,
                "created_by_id": g.created_by_id,
                "created_by_name": created_by_name,
                "verified_by_id": g.verified_by_id,
                "verified_by_name": verified_by_name,
                "created_at": g.created_at.isoformat() if g.created_at else None,
                "verified_at": g.verified_at.isoformat() if g.verified_at else None,
                "items": items,
            }
        )
    return report


async def get_issue_register(
    db: AsyncSession,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
    include_price: bool = True,
) -> List[Dict[str, Any]]:
    """Returns register of all Material Issues (MIN) with allocations and actor details."""
    stmt = (
        select(MaterialIssue)
        .options(
            selectinload(MaterialIssue.allocations),
            selectinload(MaterialIssue.material),
            selectinload(MaterialIssue.created_by),
        )
    )
    if project_id:
        stmt = stmt.where(MaterialIssue.project_id == project_id)
    if company_id:
        stmt = stmt.where(MaterialIssue.company_id == company_id)

    issues = (await db.execute(stmt)).scalars().all()
    return [
        {
            "issue_id": iss.id,
            "issue_number": iss.issue_number,
            "material_name": iss.material.material_name if iss.material else "N/A",
            "quantity": iss.quantity,
            "issue_type": iss.issue_type,
            "issued_to": iss.issued_to,
            "status": iss.status,
            "created_by_id": iss.created_by_id,
            "created_by_name": (
                iss.created_by.full_name or iss.created_by.email
                if iss.created_by
                else f"User {iss.created_by_id}"
            ),
            "created_at": iss.created_at.isoformat() if iss.created_at else None,
            "allocations": [
                {
                    "batch_id": a.batch_id,
                    "allocated_quantity": a.allocated_quantity,
                    "unit_rate": a.unit_rate if include_price else None,
                    "total_amount": a.total_amount if include_price else None,
                }
                for a in iss.allocations
            ],
        }
        for iss in issues
    ]


async def get_supplier_wise_stock(
    db: AsyncSession,
    project_id: Optional[int] = None,
    material_master_id: Optional[int] = None,
    supplier_id: Optional[int] = None,
    company_id: Optional[int] = None,
    include_price: bool = True,
) -> List[Dict[str, Any]]:
    """
    Area B: Independent supplier-level stock aggregation across active StockBatches.
    Enables supplier-specific balance tracking without mutating the consolidated materials table.
    """
    stmt = (
        select(
            StockBatch.project_id,
            StockBatch.material_master_id,
            StockBatch.supplier_id,
            Supplier.supplier_name,
            MaterialMaster.name.label("material_name"),
            MaterialMaster.unique_code.label("material_code"),
            func.sum(StockBatch.initial_quantity).label("total_received_quantity"),
            func.sum(StockBatch.current_balance).label("current_stock_balance"),
            func.sum(StockBatch.current_balance * StockBatch.purchase_rate).label("total_stock_value"),
            func.count(StockBatch.id).label("active_batches_count"),
        )
        .join(Project, Project.id == StockBatch.project_id)
        .outerjoin(Supplier, Supplier.id == StockBatch.supplier_id)
        .outerjoin(MaterialMaster, MaterialMaster.id == StockBatch.material_master_id)
        .where(
            and_(
                StockBatch.status == "ACTIVE",
                StockBatch.current_balance > Decimal("0.000"),
            )
        )
        .group_by(
            StockBatch.project_id,
            StockBatch.material_master_id,
            StockBatch.supplier_id,
            Supplier.supplier_name,
            MaterialMaster.name,
            MaterialMaster.unique_code,
        )
    )
    if company_id is not None:
        stmt = stmt.where(Project.company_id == company_id)
    if project_id:
        stmt = stmt.where(StockBatch.project_id == project_id)
    if material_master_id:
        stmt = stmt.where(StockBatch.material_master_id == material_master_id)
    if supplier_id:
        stmt = stmt.where(StockBatch.supplier_id == supplier_id)

    rows = (await db.execute(stmt)).all()
    results = []
    for r in rows:
        # Also query consolidated remaining_stock for this (project_id, material_master_id)
        stmt_mat = select(func.sum(Material.remaining_stock)).where(
            and_(
                Material.project_id == r.project_id,
                Material.material_master_id == r.material_master_id,
                Material.is_deleted == False,
            )
        )
        site_total_stock = (await db.execute(stmt_mat)).scalar() or Decimal("0.000")

        results.append(
            {
                "project_id": r.project_id,
                "material_master_id": r.material_master_id,
                "material_name": r.material_name or f"Material {r.material_master_id}",
                "material_code": r.material_code,
                "supplier_id": r.supplier_id,
                "supplier_name": r.supplier_name or "Opening Balance / Unknown",
                "total_received_quantity": r.total_received_quantity,
                "supplier_stock_balance": r.current_stock_balance,
                "supplier_stock_value": (r.total_stock_value or Decimal("0.00")).quantize(Decimal("0.01")) if include_price else None,
                "active_batches_count": r.active_batches_count,
                "site_consolidated_stock": site_total_stock,
            }
        )
    return results


async def get_daily_movement_summary(
    db: AsyncSession,
    project_id: Optional[int] = None,
    movement_date: Optional[date] = None,
    company_id: Optional[int] = None,
    include_price: bool = True,
) -> Dict[str, Any]:
    """
    Area E: Daily received and issued summary for a project/date.
    """
    target_date = movement_date or date.today()

    # Query GRNs posted on target_date
    stmt_grn = (
        select(GoodsReceiptNote)
        .options(selectinload(GoodsReceiptNote.items))
        .where(
            and_(
                GoodsReceiptNote.status == "POSTED",
                func.date(GoodsReceiptNote.created_at) == target_date,
            )
        )
    )
    if project_id:
        stmt_grn = stmt_grn.where(GoodsReceiptNote.project_id == project_id)
    if company_id:
        stmt_grn = stmt_grn.where(GoodsReceiptNote.company_id == company_id)

    grns = (await db.execute(stmt_grn)).scalars().all()
    total_inward_qty = Decimal("0.000")
    total_inward_val = Decimal("0.00")
    grn_list = []
    for g in grns:
        for it in g.items:
            total_inward_qty += it.accepted_quantity
            total_inward_val += (it.accepted_quantity * it.unit_rate).quantize(Decimal("0.01"))
        grn_list.append(
            {
                "grn_id": g.id,
                "grn_number": g.grn_number,
                "items_count": len(g.items),
            }
        )

    # Query MINs issued on target_date
    stmt_min = (
        select(MaterialIssue)
        .options(selectinload(MaterialIssue.allocations), selectinload(MaterialIssue.material))
        .where(
            and_(
                MaterialIssue.status == "ISSUED",
                func.date(MaterialIssue.created_at) == target_date,
            )
        )
    )
    if project_id:
        stmt_min = stmt_min.where(MaterialIssue.project_id == project_id)
    if company_id:
        stmt_min = stmt_min.where(MaterialIssue.company_id == company_id)

    issues = (await db.execute(stmt_min)).scalars().all()
    total_outward_qty = Decimal("0.000")
    total_outward_val = Decimal("0.00")
    min_list = []
    for iss in issues:
        total_outward_qty += iss.quantity
        alloc_val = sum((a.total_amount for a in iss.allocations), Decimal("0.00"))
        total_outward_val += alloc_val
        min_list.append(
            {
                "issue_id": iss.id,
                "issue_number": iss.issue_number,
                "material_name": iss.material.material_name if iss.material else "N/A",
                "quantity": iss.quantity,
                "total_amount": alloc_val if include_price else None,
            }
        )

    return {
        "date": target_date.isoformat(),
        "project_id": project_id,
        "total_inward_receipts": len(grns),
        "total_inward_quantity": total_inward_qty,
        "total_inward_value": total_inward_val if include_price else None,
        "total_outward_dispatches": len(issues),
        "total_outward_quantity": total_outward_qty,
        "total_outward_value": total_outward_val if include_price else None,
        "net_quantity_change": total_inward_qty - total_outward_qty,
        "net_value_change": (total_inward_val - total_outward_val) if include_price else None,
        "inward_receipts": grn_list,
        "outward_dispatches": min_list,
    }


async def get_material_movement_register(
    db: AsyncSession,
    material_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    include_price: bool = True,
    company_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Area E: Individual material movement register with chronological running balances.
    """
    material = await db.get(Material, material_id)
    if not material:
        return {"error": f"Material {material_id} not found"}

    if company_id is not None:
        proj = await db.get(Project, material.project_id) if material.project_id else None
        if not proj or proj.company_id != company_id:
            raise AppError(
                status_code=403,
                message="Forbidden: Cross-company access is strictly prohibited",
            )

    stmt = select(MaterialLedger).where(MaterialLedger.material_id == material_id)
    if start_date:
        stmt = stmt.where(MaterialLedger.created_at >= start_date)
    if end_date:
        stmt = stmt.where(MaterialLedger.created_at <= end_date)

    stmt = stmt.order_by(MaterialLedger.created_at.asc(), MaterialLedger.id.asc())
    entries = (await db.execute(stmt)).scalars().all()

    running_balance = Decimal("0.000")
    movements = []
    for e in entries:
        qty = e.quantity
        if e.type == "PURCHASE" or e.type.value == "PURCHASE":
            running_balance += qty
        elif e.type == "USAGE" or e.type.value == "USAGE":
            running_balance -= qty
        elif e.type == "ADJUSTMENT" or e.type.value == "ADJUSTMENT":
            running_balance += qty  # can be positive or negative

        movements.append(
            {
                "ledger_id": e.id,
                "transaction_type": e.type.value if hasattr(e.type, "value") else str(e.type),
                "quantity": qty,
                "rate": e.rate if include_price else None,
                "total_amount": e.total_amount if include_price else None,
                "running_balance": running_balance,
                "timestamp": e.created_at.isoformat() if e.created_at else None,
            }
        )

    return {
        "material_id": material.id,
        "material_code": material.material_code,
        "material_name": material.material_name,
        "project_id": material.project_id,
        "current_remaining_stock": material.remaining_stock,
        "total_movements": len(movements),
        "movements": movements,
    }


async def get_combined_material_movement_report(
    db: AsyncSession,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    material_master_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    include_price: bool = True,
) -> List[Dict[str, Any]]:
    """
    Combined Material Movement Summary (opening balance + period inward/outward/adjustment + closing stock & MWA valuation).
    """
    mat_stmt = (
        select(Material, ProjectMaterialValuation.current_moving_weighted_average_rate)
        .join(Project, Project.id == Material.project_id)
        .outerjoin(
            ProjectMaterialValuation,
            and_(
                ProjectMaterialValuation.project_id == Material.project_id,
                ProjectMaterialValuation.material_master_id == Material.material_master_id,
            ),
        )
        .where(Material.is_deleted == False)
    )
    if company_id is not None:
        mat_stmt = mat_stmt.where(Project.company_id == company_id)
    if project_id:
        mat_stmt = mat_stmt.where(Material.project_id == project_id)
    if material_master_id:
        mat_stmt = mat_stmt.where(Material.material_master_id == material_master_id)

    mat_rows = (await db.execute(mat_stmt)).all()
    if not mat_rows:
        return []

    material_ids = [m.id for m, _ in mat_rows]
    led_stmt = (
        select(MaterialLedger)
        .where(MaterialLedger.material_id.in_(material_ids))
        .order_by(MaterialLedger.created_at.asc(), MaterialLedger.id.asc())
    )
    all_ledgers = (await db.execute(led_stmt)).scalars().all()
    ledgers_by_mat: Dict[int, List[MaterialLedger]] = {}
    for led in all_ledgers:
        ledgers_by_mat.setdefault(led.material_id, []).append(led)

    summary_rows: List[Dict[str, Any]] = []
    for mat, mwa_rate in mat_rows:
        opening = Decimal("0.000")
        inward_qty = Decimal("0.000")
        outward_qty = Decimal("0.000")
        adjustment_qty = Decimal("0.000")

        for led in ledgers_by_mat.get(mat.id, []):
            ltype = led.type.value if hasattr(led.type, "value") else str(led.type)
            qty = Decimal(str(led.quantity or "0.000"))
            delta = -qty if ltype == "USAGE" else qty
            ts = led.created_at
            if start_date and ts and ts < start_date:
                opening += delta
                continue
            if end_date and ts and ts > end_date:
                continue
            if ltype == "PURCHASE":
                inward_qty += qty
            elif ltype == "USAGE":
                outward_qty += qty
            else:
                adjustment_qty += qty

        closing = (
            mat.remaining_stock
            if (start_date is None and end_date is None)
            else (opening + inward_qty - outward_qty + adjustment_qty)
        )
        mwa = mwa_rate or mat.purchase_rate or Decimal("0.00")
        val = (closing * mwa).quantize(Decimal("0.01"))
        summary_rows.append(
            {
                "material_id": mat.id,
                "material_code": mat.material_code,
                "material_name": mat.material_name,
                "project_id": mat.project_id,
                "opening_stock": opening,
                "total_inward": inward_qty,
                "total_outward": outward_qty,
                "net_adjustments": adjustment_qty,
                "closing_stock": closing,
                "moving_average_rate": mwa if include_price else None,
                "closing_valuation": val if include_price else None,
            }
        )
    return summary_rows


async def get_inventory_audit_trail_report(
    db: AsyncSession,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """
    Returns chronological user activity and audit trail across PO, GRN, MIN, Daily Stock, and OTP Cancellations.
    """
    events: List[Dict[str, Any]] = []

    po_stmt = select(PurchaseOrder).where(PurchaseOrder.is_deleted == False)
    grn_stmt = select(GoodsReceiptNote)
    min_stmt = select(MaterialIssue)
    dsr_stmt = select(DailyStockReconciliation)
    canc_stmt = select(OTPCancellationRequest)

    if company_id is not None:
        po_stmt = po_stmt.where(PurchaseOrder.company_id == company_id)
        grn_stmt = grn_stmt.where(GoodsReceiptNote.company_id == company_id)
        min_stmt = min_stmt.where(MaterialIssue.company_id == company_id)
        dsr_stmt = dsr_stmt.where(DailyStockReconciliation.company_id == company_id)
        canc_stmt = canc_stmt.where(OTPCancellationRequest.company_id == company_id)

    if project_id is not None:
        po_stmt = po_stmt.where(PurchaseOrder.project_id == project_id)
        grn_stmt = grn_stmt.where(GoodsReceiptNote.project_id == project_id)
        min_stmt = min_stmt.where(MaterialIssue.project_id == project_id)
        dsr_stmt = dsr_stmt.where(DailyStockReconciliation.project_id == project_id)

    pos = (await db.execute(po_stmt)).scalars().all()
    grns = (await db.execute(grn_stmt)).scalars().all()
    mins = (await db.execute(min_stmt)).scalars().all()
    dsrs = (await db.execute(dsr_stmt)).scalars().all()
    cancs = (await db.execute(canc_stmt)).scalars().all()

    user_ids = set()
    for p in pos:
        if p.approved_by_id:
            user_ids.add(p.approved_by_id)
    for g in grns:
        user_ids.add(g.created_by_id)
        if g.verified_by_id:
            user_ids.add(g.verified_by_id)
    for m in mins:
        user_ids.add(m.created_by_id)
    for d in dsrs:
        user_ids.add(d.recorded_by_id)
        if d.reviewed_by_id:
            user_ids.add(d.reviewed_by_id)
    for c in cancs:
        user_ids.add(c.requested_by_id)
        if c.verified_by_id:
            user_ids.add(c.verified_by_id)

    user_names: Dict[int, str] = {}
    if user_ids:
        u_rows = (await db.execute(select(User.id, User.full_name, User.email).where(User.id.in_(user_ids)))).all()
        for uid, fname, email in u_rows:
            user_names[uid] = fname or email or f"User {uid}"

    def _in_window(ts: Optional[datetime]) -> bool:
        if ts is None:
            return start_date is None and end_date is None
        if start_date and ts < start_date:
            return False
        if end_date and ts > end_date:
            return False
        return True

    for p in pos:
        if _in_window(p.created_at):
            events.append({
                "event_time": p.created_at.isoformat() if p.created_at else None,
                "module": "PURCHASE_ORDER",
                "action": "PO_CREATED",
                "document_number": p.po_number or f"PO-{p.id}",
                "document_id": p.id,
                "project_id": p.project_id,
                "status": p.status,
                "actor_id": None,
                "actor_name": None,
                "details": f"PO total quantity={p.quantity}",
            })
        if p.approved_at and _in_window(p.approved_at):
            events.append({
                "event_time": p.approved_at.isoformat(),
                "module": "PURCHASE_ORDER",
                "action": "PO_APPROVED",
                "document_number": p.po_number or f"PO-{p.id}",
                "document_id": p.id,
                "project_id": p.project_id,
                "status": p.status,
                "actor_id": p.approved_by_id,
                "actor_name": user_names.get(p.approved_by_id) if p.approved_by_id else None,
                "details": "Purchase order approved",
            })

    for g in grns:
        if _in_window(g.created_at):
            events.append({
                "event_time": g.created_at.isoformat() if g.created_at else None,
                "module": "INWARD_GRN",
                "action": "GRN_CREATED",
                "document_number": g.grn_number,
                "document_id": g.id,
                "project_id": g.project_id,
                "status": g.status,
                "actor_id": g.created_by_id,
                "actor_name": user_names.get(g.created_by_id),
                "details": f"Challan={g.challan_number or 'N/A'}",
            })
        if g.verified_at and _in_window(g.verified_at):
            events.append({
                "event_time": g.verified_at.isoformat(),
                "module": "INWARD_GRN",
                "action": "GRN_QC_POSTED",
                "document_number": g.grn_number,
                "document_id": g.id,
                "project_id": g.project_id,
                "status": g.status,
                "actor_id": g.verified_by_id,
                "actor_name": user_names.get(g.verified_by_id) if g.verified_by_id else None,
                "details": "GRN QC inspection posted",
            })

    for m in mins:
        if _in_window(m.created_at):
            events.append({
                "event_time": m.created_at.isoformat() if m.created_at else None,
                "module": "OUTWARD_MIN",
                "action": "MIN_ISSUED",
                "document_number": m.issue_number,
                "document_id": m.id,
                "project_id": m.project_id,
                "status": m.status,
                "actor_id": m.created_by_id,
                "actor_name": user_names.get(m.created_by_id),
                "details": f"Qty={m.quantity}, Type={m.issue_type}",
            })

    for d in dsrs:
        if _in_window(d.created_at):
            events.append({
                "event_time": d.created_at.isoformat() if d.created_at else None,
                "module": "DAILY_STOCK",
                "action": "DSR_RECORDED",
                "document_number": d.reconciliation_number,
                "document_id": d.id,
                "project_id": d.project_id,
                "status": d.status,
                "actor_id": d.recorded_by_id,
                "actor_name": user_names.get(d.recorded_by_id),
                "details": f"Variance={d.variance}",
            })
        if d.reviewed_at and _in_window(d.reviewed_at):
            events.append({
                "event_time": d.reviewed_at.isoformat(),
                "module": "DAILY_STOCK",
                "action": "DSR_REVIEWED",
                "document_number": d.reconciliation_number,
                "document_id": d.id,
                "project_id": d.project_id,
                "status": d.status,
                "actor_id": d.reviewed_by_id,
                "actor_name": user_names.get(d.reviewed_by_id) if d.reviewed_by_id else None,
                "details": f"Outcome={d.review_outcome}",
            })

    for c in cancs:
        if _in_window(c.created_at):
            events.append({
                "event_time": c.created_at.isoformat() if c.created_at else None,
                "module": "CANCELLATION",
                "action": f"OTP_REQUESTED_{c.document_type}",
                "document_number": c.document_number,
                "document_id": c.document_id,
                "project_id": None,
                "status": c.status,
                "actor_id": c.requested_by_id,
                "actor_name": user_names.get(c.requested_by_id),
                "details": c.reason,
            })
        if c.verified_at and _in_window(c.verified_at):
            events.append({
                "event_time": c.verified_at.isoformat(),
                "module": "CANCELLATION",
                "action": f"OTP_VERIFIED_{c.document_type}",
                "document_number": c.document_number,
                "document_id": c.document_id,
                "project_id": None,
                "status": c.status,
                "actor_id": c.verified_by_id,
                "actor_name": user_names.get(c.verified_by_id) if c.verified_by_id else None,
                "details": c.reason,
            })

    events.sort(key=lambda x: x["event_time"] or "", reverse=True)
    return events[:limit]


async def get_daily_stock_variance_report(
    db: AsyncSession,
    project_id: Optional[int] = None,
    company_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Returns Daily Stock reconciliations focusing on variances and reviews."""
    stmt = (
        select(DailyStockReconciliation)
        .options(
            selectinload(DailyStockReconciliation.material),
            selectinload(DailyStockReconciliation.recounts),
        )
    )
    if project_id:
        stmt = stmt.where(DailyStockReconciliation.project_id == project_id)
    if company_id:
        stmt = stmt.where(DailyStockReconciliation.company_id == company_id)

    records = (await db.execute(stmt)).scalars().all()
    return [
        {
            "reconciliation_id": r.id,
            "reconciliation_number": r.reconciliation_number,
            "material_name": r.material.material_name if r.material else "N/A",
            "date": r.reconciliation_date.isoformat(),
            "system_book_stock": r.system_book_stock,
            "physical_count": r.physical_count,
            "variance": r.variance,
            "status": r.status,
            "version": r.version,
            "review_outcome": r.review_outcome,
            "recounts_count": len(r.recounts),
        }
        for r in records
    ]


# ================= REPORT EXPORT UTILITIES =================

def _flatten_report_rows(data: Any) -> Tuple[List[str], List[List[str]], Optional[List[Tuple[str, str]]]]:
    """
    Flattens report data for tabular export (Excel and PDF).
    Returns (headers, rows, summary_metadata).
    Preserves parent context and all nested child line-item details (e.g. GRN items,
    PO fulfillment items, issue allocations, movement ledger lines).
    Respects upstream price masking (None values render as empty cells).
    """
    if isinstance(data, dict):
        summary_metadata = [
            (k.replace("_", " ").title(), str(v) if v is not None else "")
            for k, v in data.items()
            if not isinstance(v, (list, dict))
        ]
        # Look for nested child array (e.g. "movements" in stock movement ledger)
        nested_candidates = [
            (k, v) for k, v in data.items()
            if isinstance(v, list) and v and isinstance(v[0], dict)
        ]
        if nested_candidates:
            _, child_list = nested_candidates[0]
            first_c = child_list[0]
            c_keys = [k for k, v in first_c.items() if not isinstance(v, (list, dict))]
            headers = [k.replace("_", " ").title() for k in c_keys]
            rows = [
                [str(item.get(k, "")) if item.get(k) is not None else "" for k in c_keys]
                for item in child_list
            ]
            return headers, rows, summary_metadata
        else:
            return ["Metric / Field", "Value"], [[k, v] for k, v in summary_metadata], None

    elif isinstance(data, list) and data:
        first_item = data[0]
        if not isinstance(first_item, dict):
            return ["Value"], [[str(x)] for x in data], None

        # Check for nested list-of-dicts (e.g. "items" in GRN register / PO fulfillment, "allocations" in issue register)
        nested_candidates = [
            k for k, v in first_item.items()
            if isinstance(v, list) and v and isinstance(v[0], dict)
        ]

        if not nested_candidates:
            # Standard flat report rows
            keys = [k for k, v in first_item.items() if not isinstance(v, (list, dict))]
            headers = [k.replace("_", " ").title() for k in keys]
            rows = [
                [str(item.get(k, "")) if item.get(k) is not None else "" for k in keys]
                for item in data
            ]
            return headers, rows, None
        else:
            # Multi-item document report with nested child items
            nested_key = nested_candidates[0]
            parent_keys = [k for k, v in first_item.items() if not isinstance(v, (list, dict))]
            first_child = first_item[nested_key][0]
            child_keys = [k for k, v in first_child.items() if not isinstance(v, (list, dict))]

            headers = [k.replace("_", " ").title() for k in parent_keys] + [
                f"Item {k.replace('_', ' ').title()}" for k in child_keys
            ]
            rows = []
            for parent in data:
                children = parent.get(nested_key, [])
                if children and isinstance(children, list):
                    for child in children:
                        if isinstance(child, dict):
                            r = [str(parent.get(k, "")) if parent.get(k) is not None else "" for k in parent_keys] + [
                                str(child.get(k, "")) if child.get(k) is not None else "" for k in child_keys
                            ]
                            rows.append(r)
                else:
                    r = [str(parent.get(k, "")) if parent.get(k) is not None else "" for k in parent_keys] + [
                        "" for _ in child_keys
                    ]
                    rows.append(r)
            return headers, rows, None

    return ["Info"], [["No records available for export."]], None


def export_report_to_excel(report_name: str, data: Any) -> io.BytesIO:
    """Exports structured report dictionary or list of records to Excel (.xlsx) with nested item support."""
    wb = Workbook()
    ws = wb.active
    ws.title = report_name[:31].replace("_", " ").title()

    headers, rows, summary_meta = _flatten_report_rows(data)

    header_row_idx = 1
    if summary_meta:
        ws.append(["Summary Metric", "Value"])
        header_cell = ws.cell(row=1, column=1)
        header_cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_cell.fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
        header_cell_v = ws.cell(row=1, column=2)
        header_cell_v.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        header_cell_v.fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")

        for k, v in summary_meta:
            ws.append([k, v])
        ws.append([])  # Spacer row
        header_row_idx = ws.max_row + 1

    ws.append(headers)
    for r in rows:
        ws.append(r)

    # Style table header row
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    for cell in ws[header_row_idx]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = col[0].column_letter
        ws.column_dimensions[col_letter].width = max(max_len + 3, 14)

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_report_to_pdf(report_name: str, data: Any) -> io.BytesIO:
    """Exports structured report dictionary or list of records to landscape PDF with nested item support."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(letter),
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    styles = getSampleStyleSheet()
    title_style = styles["Heading1"]
    title_style.textColor = colors.HexColor("#1F497D")

    elements = [
        Paragraph(f"Inventory Report: {report_name.replace('_', ' ').title()}", title_style),
        Paragraph(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC", styles["Normal"]),
        Spacer(1, 12),
    ]

    headers, rows, summary_meta = _flatten_report_rows(data)

    if summary_meta:
        meta_table_data = [["Summary Metric", "Value"]] + [[k, v] for k, v in summary_meta]
        t_meta = Table(meta_table_data, colWidths=[200, 300])
        t_meta.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F497D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D5DD")),
            ])
        )
        elements.append(t_meta)
        elements.append(Spacer(1, 12))

    table_data = [headers] + rows[:100]  # Limit to 100 rows for PDF layout stability

    if table_data:
        t = Table(table_data, repeatRows=1)
        t.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F497D")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
                ("TOPPADDING", (0, 0), (-1, 0), 5),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F4F8")]),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#D0D5DD")),
            ])
        )
        elements.append(t)

    doc.build(elements)
    buf.seek(0)
    return buf

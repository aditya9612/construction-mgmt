from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.inventory import (
    GoodsReceiptNote,
    GoodsReceiptNoteItem,
    StockBatch,
    ProjectMaterialValuation,
)
from app.models.material import (
    Material,
    MaterialTransaction,
    MaterialLedger,
    PurchaseOrder,
    Supplier,
)
from app.models.purchase_order import PurchaseOrderItem
from app.models.project import Project
from app.models.master_data import MaterialMaster
from app.core.enums import TransactionType, RateType
from app.services.sequence_service import allocate_material_code
from app.services.notification_service import create_notification, notify_company_stakeholders
from app.utils.helpers import AppError, NotFoundError, resolve_min_stock_threshold


async def generate_grn_number(db: AsyncSession) -> str:
    """Generates an atomic unique GRN number."""
    return await allocate_material_code(db, prefix="GRN", padding=5)


async def create_goods_receipt_note(
    db: AsyncSession,
    company_id: int,
    user_id: int,
    project_id: int,
    supplier_id: int,
    items: List[Dict[str, Any]],
    purchase_order_id: Optional[int] = None,
    challan_number: Optional[str] = None,
    challan_date: Optional[date] = None,
    vehicle_number: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    remarks: Optional[str] = None,
) -> GoodsReceiptNote:
    """
    Step 1: Creates a Goods Receipt Note in DRAFT status.
    Guarantees idempotency via (company_id, idempotency_key).
    """
    # 1. Idempotency Check
    if idempotency_key:
        stmt = (
            select(GoodsReceiptNote)
            .options(selectinload(GoodsReceiptNote.items))
            .where(
                and_(
                    GoodsReceiptNote.company_id == company_id,
                    GoodsReceiptNote.idempotency_key == idempotency_key,
                )
            )
        )
        existing_grn = (await db.execute(stmt)).scalar_one_or_none()
        if existing_grn:
            return existing_grn

    # 2. Validation
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError(f"Project {project_id} not found")
    if project.company_id != company_id:
        raise AppError(status_code=403, message="Forbidden: Project belongs to a different company")

    supplier = await db.get(Supplier, supplier_id)
    if not supplier:
        raise NotFoundError(f"Supplier {supplier_id} not found")
    if supplier.company_id is not None and supplier.company_id != company_id:
        raise AppError(status_code=403, message="Forbidden: Supplier belongs to a different company")

    if not items:
        raise AppError(status_code=400, message="GRN must contain at least one line item")

    if challan_number and challan_number.strip():
        clean_challan = challan_number.strip()
        dup_stmt = (
            select(GoodsReceiptNote.id)
            .where(
                and_(
                    GoodsReceiptNote.company_id == company_id,
                    GoodsReceiptNote.supplier_id == supplier_id,
                    GoodsReceiptNote.challan_number == clean_challan,
                    GoodsReceiptNote.status != "CANCELLED",
                )
            )
            .limit(1)
        )
        if (await db.execute(dup_stmt)).scalar_one_or_none() is not None:
            raise AppError(
                status_code=400,
                message=f"Duplicate challan number '{clean_challan}' for supplier {supplier_id}.",
            )
        challan_number = clean_challan

    po_items_by_id: Dict[int, PurchaseOrderItem] = {}
    if purchase_order_id is not None:
        po_stmt = (
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.items))
            .where(PurchaseOrder.id == purchase_order_id)
        )
        po = (await db.execute(po_stmt)).scalar_one_or_none()
        if not po or po.is_deleted:
            raise NotFoundError(f"Purchase Order {purchase_order_id} not found")
        if po.company_id is not None and po.company_id != company_id:
            raise AppError(status_code=403, message="Forbidden: Purchase Order belongs to a different company")
        if po.status in ("CANCELLED", "REJECTED", "RECEIVED"):
            raise AppError(
                status_code=400,
                message=f"Cannot create GRN for Purchase Order in status '{po.status}'.",
            )
        if not po.is_company_wide and po.project_id is not None and po.project_id != project_id:
            raise AppError(
                status_code=400,
                message=(
                    f"Site-wise Purchase Order {po.po_number or po.id} is locked to Project {po.project_id} "
                    f"and cannot receive goods for Project {project_id}."
                ),
            )
        po_items_by_id = {it.id: it for it in (po.items or []) if not it.is_deleted}
    elif not remarks or not remarks.strip():
        raise AppError(
            status_code=400,
            message="Direct Inward (GRN without Purchase Order) requires mandatory justification remarks.",
        )

    for item_data in items:
        qty = Decimal(str(item_data["received_quantity"]))
        po_item_id = item_data.get("po_item_id")
        if po_item_id is not None:
            if purchase_order_id is None or po_item_id not in po_items_by_id:
                raise AppError(
                    status_code=400,
                    message=f"PO item {po_item_id} does not belong to Purchase Order {purchase_order_id}.",
                )
            po_item = po_items_by_id[po_item_id]
            remaining_allowed = (po_item.quantity or Decimal("0.000")) - (po_item.received_quantity or Decimal("0.000"))
            if qty > remaining_allowed:
                raise AppError(
                    status_code=400,
                    message=(
                        f"Over-receipt rejected for '{po_item.material_name}': received {qty} "
                        f"exceeds remaining pending PO quantity {remaining_allowed}."
                    ),
                )

    grn_number = await generate_grn_number(db)

    grn = GoodsReceiptNote(
        grn_number=grn_number,
        company_id=company_id,
        project_id=project_id,
        supplier_id=supplier_id,
        purchase_order_id=purchase_order_id,
        challan_number=challan_number,
        challan_date=challan_date,
        vehicle_number=vehicle_number,
        idempotency_key=idempotency_key,
        status="DRAFT",
        created_by_id=user_id,
        remarks=remarks,
    )
    db.add(grn)
    await db.flush()

    for item_data in items:
        mat_master_id = item_data["material_master_id"]
        qty = Decimal(str(item_data["received_quantity"]))
        unit_rate = Decimal(str(item_data["unit_rate"]))
        total_amt = (qty * unit_rate).quantize(Decimal("0.01"))

        grn_item = GoodsReceiptNoteItem(
            grn_id=grn.id,
            po_item_id=item_data.get("po_item_id"),
            material_master_id=mat_master_id,
            material_id=item_data.get("material_id"),
            received_quantity=qty,
            accepted_quantity=Decimal("0.000"),
            rejected_quantity=Decimal("0.000"),
            unit_rate=unit_rate,
            total_amount=total_amt,
            batch_number=item_data.get("batch_number"),
            qc_remarks=item_data.get("qc_remarks"),
        )
        db.add(grn_item)

    await db.commit()
    await db.refresh(grn)
    return grn


async def inspect_and_post_grn(
    db: AsyncSession,
    grn_id: int,
    user_id: int,
    qc_items: List[Dict[str, Any]],
) -> GoodsReceiptNote:
    """
    Step 2: Quality Inspection and Posting.
    Atomically updates site stock, creates StockBatches, recalculates MWA valuation,
    updates PurchaseOrderItem received_quantity, and creates financial ledger entries.
    """
    stmt = (
        select(GoodsReceiptNote)
        .options(
            selectinload(GoodsReceiptNote.items),
            selectinload(GoodsReceiptNote.purchase_order),
        )
        .where(GoodsReceiptNote.id == grn_id)
        .with_for_update()
    )
    grn = (await db.execute(stmt)).scalar_one_or_none()

    if not grn:
        raise NotFoundError(f"GRN {grn_id} not found")

    if grn.status == "POSTED":
        raise AppError(status_code=400, message="GRN is already posted and inventory has been updated")
    if grn.status == "CANCELLED":
        raise AppError(status_code=400, message="Cannot post QC inspection for a cancelled GRN")

    # Map QC items by ID
    qc_map = {item["item_id"]: item for item in qc_items}
    resolved_alerts: List[tuple] = []

    for grn_item in grn.items:
        if grn_item.id not in qc_map:
            raise AppError(status_code=400, message=f"Missing QC inspection data for item {grn_item.id}")

        qc_data = qc_map[grn_item.id]
        accepted_qty = Decimal(str(qc_data["accepted_quantity"]))
        rejected_qty = Decimal(str(qc_data["rejected_quantity"]))

        if (accepted_qty + rejected_qty) != grn_item.received_quantity:
            raise AppError(
                status_code=400,
                message=(
                    f"Accepted quantity ({accepted_qty}) + rejected quantity ({rejected_qty}) "
                    f"must equal received quantity ({grn_item.received_quantity}) for item {grn_item.id}"
                ),
            )

        grn_item.accepted_quantity = accepted_qty
        grn_item.rejected_quantity = rejected_qty
        grn_item.qc_remarks = qc_data.get("qc_remarks", grn_item.qc_remarks)

        if accepted_qty <= Decimal("0.000"):
            continue

        # 1. Ensure site Material record exists for this (project_id, material_master_id)
        stmt_mat = (
            select(Material)
            .where(
                and_(
                    Material.project_id == grn.project_id,
                    Material.material_master_id == grn_item.material_master_id,
                    Material.is_deleted == False,
                )
            )
            .with_for_update()
        )
        material = (await db.execute(stmt_mat)).scalar_one_or_none()

        if not material:
            # Look up material master details
            mat_master = await db.get(MaterialMaster, grn_item.material_master_id)
            mat_name = mat_master.name if mat_master else f"Material {grn_item.material_master_id}"
            cat = mat_master.category if mat_master and hasattr(mat_master, "category") and mat_master.category else "General"
            unit_id = mat_master.unit_id if mat_master and hasattr(mat_master, "unit_id") and mat_master.unit_id else 1

            material_code = await allocate_material_code(db, prefix="MAT", padding=4)
            material = Material(
                material_code=material_code,
                project_id=grn.project_id,
                material_master_id=grn_item.material_master_id,
                material_name=mat_name,
                category=cat,
                unit_id=unit_id,
                supplier_id=grn.supplier_id,
                rate_type=RateType.FIXED,
                purchase_rate=grn_item.unit_rate,
                quantity_purchased=accepted_qty,
                quantity_used=Decimal("0.000"),
                remaining_stock=accepted_qty,
            )
            db.add(material)
            await db.flush()
        else:
            prev_stock = material.remaining_stock or Decimal("0.000")
            material.remaining_stock += accepted_qty
            material.quantity_purchased += accepted_qty
            min_threshold = resolve_min_stock_threshold(material)
            if prev_stock <= min_threshold < material.remaining_stock:
                resolved_alerts.append((material.id, material.material_name, material.remaining_stock, min_threshold))

        grn_item.material_id = material.id

        # 2. Create StockBatch
        batch_no = grn_item.batch_number or f"BATCH-{grn.grn_number}-{grn_item.id}"
        batch = StockBatch(
            batch_number=batch_no,
            project_id=grn.project_id,
            material_master_id=grn_item.material_master_id,
            supplier_id=grn.supplier_id,
            grn_id=grn.id,
            initial_quantity=accepted_qty,
            current_balance=accepted_qty,
            purchase_rate=grn_item.unit_rate,
            receipt_date=datetime.utcnow(),
            batch_source="GRN",
            status="ACTIVE",
        )
        db.add(batch)

        # 3. Recalculate ProjectMaterialValuation (Moving Weighted Average)
        stmt_val = (
            select(ProjectMaterialValuation)
            .where(
                and_(
                    ProjectMaterialValuation.project_id == grn.project_id,
                    ProjectMaterialValuation.material_master_id == grn_item.material_master_id,
                )
            )
            .with_for_update()
        )
        val = (await db.execute(stmt_val)).scalar_one_or_none()

        accepted_amount = (accepted_qty * grn_item.unit_rate).quantize(Decimal("0.01"))
        if not val:
            val = ProjectMaterialValuation(
                project_id=grn.project_id,
                material_master_id=grn_item.material_master_id,
                total_purchased_quantity=accepted_qty,
                total_purchased_amount=accepted_amount,
                current_moving_weighted_average_rate=grn_item.unit_rate,
            )
            db.add(val)
        else:
            new_qty = val.total_purchased_quantity + accepted_qty
            new_amt = val.total_purchased_amount + accepted_amount
            new_mwa = (new_amt / new_qty).quantize(Decimal("0.01")) if new_qty > 0 else grn_item.unit_rate
            val.total_purchased_quantity = new_qty
            val.total_purchased_amount = new_amt
            val.current_moving_weighted_average_rate = new_mwa
            val.last_recalculated_at = datetime.utcnow()

        # 4. Update linked PurchaseOrderItem
        if grn_item.po_item_id:
            po_item = await db.get(PurchaseOrderItem, grn_item.po_item_id)
            if po_item:
                po_item.received_quantity += accepted_qty
                if po_item.received_quantity >= po_item.quantity:
                    po_item.status = "RECEIVED"
                else:
                    po_item.status = "PARTIALLY_RECEIVED"

        # 5. Financial Audit Ledger and MaterialTransaction
        mat_tx = MaterialTransaction(
            material_id=material.id,
            type=TransactionType.PURCHASE,
            quantity=accepted_qty,
            rate=grn_item.unit_rate,
            total_amount=accepted_amount,
        )
        db.add(mat_tx)

        ledger_entry = MaterialLedger(
            material_id=material.id,
            type=TransactionType.PURCHASE,
            quantity=accepted_qty,
            rate=grn_item.unit_rate,
            total_amount=accepted_amount,
        )
        db.add(ledger_entry)

    # 6. Check PurchaseOrder status if linked
    if grn.purchase_order_id:
        po_stmt = (
            select(PurchaseOrder)
            .options(selectinload(PurchaseOrder.items))
            .where(PurchaseOrder.id == grn.purchase_order_id)
            .with_for_update()
        )
        po = (await db.execute(po_stmt)).scalar_one_or_none()
        if po and po.items:
            all_received = all(item.received_quantity >= item.quantity for item in po.items)
            po.status = "RECEIVED" if all_received else "PARTIALLY_RECEIVED"

    grn.status = "POSTED"
    grn.verified_by_id = user_id
    grn.verified_at = datetime.utcnow()

    # 7. Notification on Posting (Area B & E)
    await notify_company_stakeholders(
        db=db,
        company_id=grn.company_id,
        project_id=grn.project_id,
        actor_id=user_id,
        title=f"GRN Posted: {grn.grn_number}",
        message=f"GRN {grn.grn_number} with {len(grn.items)} item(s) posted by User #{user_id}.",
        type="success",
        link=f"/inventory/grn/{grn.id}",
    )

    for mat_id, mat_name, new_stock, threshold in resolved_alerts:
        await notify_company_stakeholders(
            db=db,
            company_id=grn.company_id,
            project_id=grn.project_id,
            actor_id=user_id,
            title=f"Low Stock Alert Resolved: {mat_name}",
            message=f"Material '{mat_name}' stock replenished to {new_stock} (above threshold {threshold}) via {grn.grn_number}.",
            type="info",
            link=f"/inventory/materials/{mat_id}",
        )

    await db.commit()
    await db.refresh(grn)
    return grn


async def get_goods_receipt_note(
    db: AsyncSession,
    grn_id: int,
) -> Optional[GoodsReceiptNote]:
    """Retrieves a single GRN with line items and relations."""
    stmt = (
        select(GoodsReceiptNote)
        .options(
            selectinload(GoodsReceiptNote.items),
            selectinload(GoodsReceiptNote.supplier),
            selectinload(GoodsReceiptNote.project),
            selectinload(GoodsReceiptNote.created_by),
            selectinload(GoodsReceiptNote.verified_by),
        )
        .where(GoodsReceiptNote.id == grn_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_goods_receipt_notes(
    db: AsyncSession,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[GoodsReceiptNote]:
    """Lists GRNs with filters."""
    stmt = (
        select(GoodsReceiptNote)
        .options(
            selectinload(GoodsReceiptNote.items),
            selectinload(GoodsReceiptNote.supplier),
            selectinload(GoodsReceiptNote.project),
        )
    )
    conditions = []
    if company_id:
        conditions.append(GoodsReceiptNote.company_id == company_id)
    if project_id:
        conditions.append(GoodsReceiptNote.project_id == project_id)
    if status:
        conditions.append(GoodsReceiptNote.status == status)

    if conditions:
        stmt = stmt.where(and_(*conditions))

    stmt = stmt.order_by(GoodsReceiptNote.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Dict, Any

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.material import PurchaseOrder, Material, Supplier
from app.models.purchase_order import PurchaseOrderItem
from app.models.project import Project
from app.services.sequence_service import allocate_material_code
from app.services.notification_service import create_notification, notify_company_stakeholders
from app.utils.helpers import AppError, NotFoundError


async def generate_po_number(db: AsyncSession) -> str:
    """Generates an atomic unique PO number using BusinessSequence."""
    return await allocate_material_code(db, prefix="PO", padding=5)


async def create_purchase_order(
    db: AsyncSession,
    company_id: int,
    user_id: int,
    supplier_id: int,
    items: List[Dict[str, Any]],
    project_id: Optional[int] = None,
    is_company_wide: bool = False,
    boq_item_id: Optional[int] = None,
    po_number: Optional[str] = None,
) -> PurchaseOrder:
    """
    Creates a multi-item Purchase Order with 100% backward compatibility
    for single-item legacy queries and VendorBill foreign keys.
    """
    # 1. Validation
    supplier = await db.get(Supplier, supplier_id)
    if not supplier:
        raise NotFoundError(f"Supplier with ID {supplier_id} not found")

    if not is_company_wide:
        if not project_id:
            raise AppError(status_code=400, message="Project ID is required for site-specific Purchase Orders")
        project = await db.get(Project, project_id)
        if not project:
            raise NotFoundError(f"Project with ID {project_id} not found")

    if not items:
        raise AppError(status_code=400, message="Purchase Order must contain at least one line item")

    # 2. Sequence generation if po_number not explicitly passed
    if not po_number:
        po_number = await generate_po_number(db)

    # 3. Calculate totals & single-item backward compatibility fields
    total_po_amount = Decimal("0.00")
    total_po_quantity = Decimal("0.000")

    first_item = items[0]
    first_material_id = first_item.get("material_id")
    first_material_name = first_item.get("material_name", "")
    first_rate = Decimal(str(first_item.get("rate", "0.00")))

    po = PurchaseOrder(
        company_id=company_id,
        po_number=po_number,
        is_company_wide=is_company_wide,
        supplier_id=supplier_id,
        project_id=project_id,
        boq_item_id=boq_item_id,
        status="CREATED",
        material_id=first_material_id,
        material_name=first_material_name,
        rate=first_rate,
        quantity=Decimal("0.000"),
        total_amount=Decimal("0.00"),
    )
    db.add(po)
    await db.flush()

    for item_data in items:
        qty = Decimal(str(item_data.get("quantity", "0.000")))
        rate = Decimal(str(item_data.get("rate", "0.00")))
        item_total = (qty * rate).quantize(Decimal("0.01"))

        total_po_quantity += qty
        total_po_amount += item_total

        po_item = PurchaseOrderItem(
            purchase_order_id=po.id,
            material_master_id=item_data.get("material_master_id"),
            material_id=item_data.get("material_id"),
            material_name=item_data.get("material_name", "Material"),
            quantity=qty,
            received_quantity=Decimal("0.000"),
            rate=rate,
            total_amount=item_total,
            unit=item_data.get("unit"),
            status="PENDING",
        )
        db.add(po_item)

    po.quantity = total_po_quantity
    po.total_amount = total_po_amount

    await notify_company_stakeholders(
        db=db,
        company_id=po.company_id,
        project_id=po.project_id,
        actor_id=user_id,
        title=f"Purchase Order Created: {po.po_number}",
        message=f"PO {po.po_number} with {len(items)} item(s) created by User #{user_id}.",
        type="info",
        link=f"/procurement/po/{po.id}",
    )

    await db.flush()
    await db.commit()
    await db.refresh(po)
    return po


async def approve_purchase_order(
    db: AsyncSession,
    po_id: int,
    user_id: int,
) -> PurchaseOrder:
    """Approves a created Purchase Order."""
    stmt = (
        select(PurchaseOrder)
        .options(selectinload(PurchaseOrder.items))
        .where(PurchaseOrder.id == po_id)
        .with_for_update()
    )
    result = await db.execute(stmt)
    po = result.scalar_one_or_none()

    if not po:
        raise NotFoundError(f"Purchase Order {po_id} not found")

    if po.status not in ("CREATED", "PENDING"):
        raise AppError(status_code=400, message=f"Cannot approve Purchase Order in '{po.status}' status")

    po.status = "APPROVED"
    po.approved_by_id = user_id
    po.approved_at = datetime.utcnow()

    await notify_company_stakeholders(
        db=db,
        company_id=po.company_id,
        project_id=po.project_id,
        actor_id=user_id,
        title=f"Purchase Order Approved: {po.po_number}",
        message=f"PO {po.po_number} has been approved by User #{user_id}.",
        type="success",
        link=f"/procurement/po/{po.id}",
    )

    await db.commit()
    await db.refresh(po)
    return po


async def get_purchase_order(
    db: AsyncSession,
    po_id: int,
) -> Optional[PurchaseOrder]:
    """Retrieves a single Purchase Order with relationships."""
    stmt = (
        select(PurchaseOrder)
        .options(
            selectinload(PurchaseOrder.items),
            selectinload(PurchaseOrder.supplier),
            selectinload(PurchaseOrder.project),
        )
        .where(PurchaseOrder.id == po_id)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_purchase_orders(
    db: AsyncSession,
    company_id: Optional[int] = None,
    project_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[PurchaseOrder]:
    """Lists purchase orders with filters."""
    stmt = (
        select(PurchaseOrder)
        .options(
            selectinload(PurchaseOrder.items),
            selectinload(PurchaseOrder.supplier),
            selectinload(PurchaseOrder.project),
        )
    )
    conditions = [PurchaseOrder.is_deleted == False]
    if company_id:
        conditions.append(PurchaseOrder.company_id == company_id)
    if project_id:
        conditions.append(PurchaseOrder.project_id == project_id)
    if status:
        conditions.append(PurchaseOrder.status == status)

    stmt = stmt.where(and_(*conditions)).order_by(PurchaseOrder.id.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    return result.scalars().all()

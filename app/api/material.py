from datetime import date
from typing import Optional, List
from decimal import Decimal
import uuid
import os
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from reportlab.platypus import SimpleDocTemplate, Table
from openpyxl import Workbook
from app.models.user import User, UserRole
from app.schemas.material import TransferMaterial, TransferProject
from app.cache.redis import bump_cache_version
from app.core.dependencies import get_request_redis, require_roles
from app.db.session import get_db_session
from app.schemas.material import MaterialReport
from app.schemas.material import PriceHistoryOut
import tempfile
from app.core.enums import IssueType, TransactionType, TransferStatus
from app.utils.common import generate_business_id
from sqlalchemy.orm import selectinload
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
import tempfile
from openpyxl import Workbook
import os
from app.models.material import (
    Material,
    MaterialLedger,
    MaterialTransaction,
    Supplier,
    PurchaseOrder,
    MaterialTransfer,
)
from starlette.background import BackgroundTask
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import tempfile
from app.models.material import MaterialUsage
from app.schemas.material import InventoryAdjustRequest
from app.models.project import Project
from sqlalchemy.orm import aliased
from app.core.enums import TransactionType as DBTransactionType
from app.schemas.material import TransactionType as SchemaTransactionType
from app.schemas.material import (
    MaterialLogOut,
    MaterialCreate,
    MaterialOut,
    MaterialUpdate,
    SummaryOut,
    PurchaseMaterial,
    UsageMaterial,
    SupplierCreate,
    SupplierOut,
    PurchaseOrderCreate,
    PurchaseOrderOut,
    TransferCreate,
    TransferOut,
)

MATERIAL_READ_ROLES = [r.value for r in [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
    UserRole.SITE_ENGINEER,
    UserRole.ACCOUNTANT,
    UserRole.CLIENT,
]]

MATERIAL_WRITE_ROLES = [r.value for r in [
    UserRole.ADMIN,
    UserRole.PROJECT_MANAGER,
    UserRole.SITE_ENGINEER,
]]

router = APIRouter(prefix="/materials", tags=["materials"])
VERSION_KEY = "cache_version:materials"

def safe_delete(file_path: str):
    try:
        os.remove(file_path)
    except OSError:
        pass

# ================= CENTRAL CALCULATION =================

def build_material_response(obj, supplier_name: str | None):
    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    remaining = float(obj.remaining_stock or 0)
    min_level = float(obj.minimum_stock_level or 0)

    #  alert logic
    if remaining == 0:
        alert = "OUT_OF_STOCK"
    elif remaining <= min_level:
        alert = "LOW_STOCK"
    else:
        alert = "IN_STOCK"

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,
        project_id=obj.project_id,
        material_name=(obj.material_name or "").strip().title(),
        category=obj.category,
        unit=obj.unit,

        supplier_id=obj.supplier_id,
        supplier_name=supplier_name or "N/A",

        purchase_rate=round(float(obj.purchase_rate or 0), 2),
        rate_type=obj.rate_type,

        quantity_purchased=round(float(obj.quantity_purchased or 0), 2),
        quantity_used=round(float(obj.quantity_used or 0), 2),

        remaining_stock=round(remaining, 2),

        total_amount=round(total_amount, 2),
        payment_given=round(payment_given, 2),
        payment_pending=round(payment_pending, 2),
        extra_paid=round(extra_paid, 2),

        minimum_stock_level=round(min_level, 2),
        alert_type=alert,
    )

def update_material_fields(obj: Material):
    qty_purchased = obj.quantity_purchased or Decimal("0")
    qty_used = obj.quantity_used or Decimal("0")
    payment_given = obj.payment_given or Decimal("0")
    total_amount = obj.total_amount or Decimal("0")

    obj.remaining_stock = max(qty_purchased - qty_used, Decimal("0"))
    obj.payment_pending = max(total_amount - payment_given, Decimal("0"))
    obj.advance_amount = max(payment_given - total_amount, Decimal("0"))

def build_po_response(po: PurchaseOrder) -> PurchaseOrderOut:
    return PurchaseOrderOut(
        id=po.id,
        supplier_id=po.supplier_id,
        project_id=po.project_id,
        material_id=po.material_id,
        material_name=(po.material_name or "").strip().title(),
        quantity=round(float(po.quantity or 0), 2),
        rate=round(float(po.rate or 0), 2),
        total_amount=round(float(po.total_amount or 0), 2),
        status=po.status,
    )

def get_signed_quantity(tx: MaterialTransaction) -> float:
    if tx.type in {DBTransactionType.USAGE, DBTransactionType.TRANSFER_OUT}:
        return -abs(float(tx.quantity or 0))
    return abs(float(tx.quantity or 0))

def build_transfer_response(obj, material, from_project, to_project):
    return TransferOut(
        id=obj.id,
        material=TransferMaterial(
            id=material.id, 
            name=(material.material_name or "").title()
        ),
        from_project=TransferProject(
            id=from_project.id, 
            name=from_project.project_name
        ),
        to_project=TransferProject(
            id=to_project.id, 
            name=to_project.project_name
        ),
        quantity=obj.quantity,
        status=obj.status,
        created_at=obj.created_at,
    )

def get_alert_type(obj):
    from decimal import Decimal

    remaining = obj.remaining_stock or Decimal("0")
    min_level = obj.minimum_stock_level or Decimal("0")

    if remaining == 0:
        return "OUT_OF_STOCK"
    elif remaining <= min_level:
        return "LOW_STOCK"
    else:
        return "IN_STOCK"

def calculate_fields(obj):
    from decimal import Decimal

    total_amount = obj.total_amount or Decimal("0")
    payment_given = obj.payment_given or Decimal("0")

    payment_pending = max(total_amount - payment_given, Decimal("0"))
    extra_paid = max(payment_given - total_amount, Decimal("0"))

    return (
        float(total_amount),
        float(payment_given),
        float(payment_pending),
        float(extra_paid),
    )
    
# ================= SUMMARY =================

@router.get("/summary", response_model=SummaryOut)
async def material_summary(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):

    total_materials = await db.scalar(
        select(func.count(Material.id)).where(Material.is_deleted == False)
    )

    total_stock = await db.scalar(
        select(func.sum(Material.remaining_stock * Material.purchase_rate)).where(
            Material.is_deleted == False
        )
    )

    total_pending = await db.scalar(
        select(func.sum(Material.payment_pending)).where(Material.is_deleted == False)
    )

    return {
        "total_materials": total_materials or 0,
        "total_stock_value": round(float(total_stock or 0), 2),
        "total_pending_payments": round(float(total_pending or 0), 2),
    }

# ================= SUPPLIERS =================


@router.get("/suppliers", response_model=List[SupplierOut])
async def list_suppliers(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):

    rows = (
        (
            await db.execute(
                select(Supplier)
                .where(Supplier.is_deleted == False)
                .order_by(Supplier.id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    return [SupplierOut.model_validate(r) for r in rows]


@router.get("/suppliers/{id}", response_model=SupplierOut)
async def get_supplier(id: int, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):

    obj = await db.scalar(
        select(Supplier).where(Supplier.id == id, Supplier.is_deleted == False)
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Supplier not found")

    return SupplierOut.model_validate(obj)


@router.post("/suppliers", response_model=SupplierOut, status_code=201)
async def create_supplier(
    payload: SupplierCreate,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    name = payload.name.strip()
    contact = payload.contact.strip() if payload.contact else None

    import re

    if contact and not re.match(r"^[6-9]\d{9}$", contact):
        raise HTTPException(400, "Invalid contact number")

    existing = await db.scalar(
        select(Supplier).where(
            func.lower(Supplier.name) == name.lower(),
            Supplier.contact == contact,
            Supplier.is_deleted == False,
        )
    )

    if existing:
        raise HTTPException(400, "Supplier already exists")

    supplier = Supplier(name=name, contact=contact)

    try:
        db.add(supplier)
        await db.commit()
        await db.refresh(supplier)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Supplier already exists")

    return SupplierOut.model_validate(supplier)


@router.put("/suppliers/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: int,
    payload: SupplierCreate,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    supplier = await db.get(Supplier, supplier_id)

    if not supplier or supplier.is_deleted:
        raise HTTPException(404, "Supplier not found")

    new_name = payload.name.strip().title()
    new_contact = payload.contact.strip() if payload.contact else None

    if new_contact and (not new_contact.isdigit() or len(new_contact) != 10):
        raise HTTPException(400, "Invalid contact number")

    # no-op
    if supplier.name == new_name and supplier.contact == new_contact:
        return SupplierOut.model_validate(supplier)

    existing = await db.scalar(
        select(Supplier).where(
            Supplier.contact == new_contact,
            Supplier.id != supplier_id,
            Supplier.is_deleted == False,
        )
    )

    if existing:
        raise HTTPException(400, "Contact already used")

    try:
        supplier.name = new_name
        supplier.contact = new_contact

        await db.commit()
        await db.refresh(supplier)

    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Duplicate supplier")

    return SupplierOut.model_validate(supplier)


@router.delete("/suppliers/{id}")
async def delete_supplier(id: int, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),):
    obj = await db.get(Supplier, id)

    if not obj or obj.is_deleted:
        raise HTTPException(404, "Supplier not found")

    in_use = await db.scalar(
        select(func.count()).where(Material.supplier_id == id)
    )

    if in_use > 0:
        raise HTTPException(400, "Supplier is used in materials")

    obj.is_deleted = True
    await db.commit()

    return {"message": "Deleted successfully"}


# ================= supplier materials =================
@router.get("/suppliers/{supplier_id}/materials", response_model=list[MaterialOut])
async def get_supplier_materials(
    supplier_id: int,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(Material, Supplier.name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.supplier_id == supplier_id, Material.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )

    rows = (await db.execute(query)).all()

    return [
        build_material_response(m, supplier_name)
        for m, supplier_name in rows
    ]


# ================= material_alerts =================

@router.get("/alerts", response_model=list[MaterialOut])
async def get_material_alerts(
    threshold: float | None = None,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(Material, Supplier.name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.is_deleted == False)
    )

    if threshold is not None:
        query = query.where(Material.remaining_stock <= threshold)
    else:
        query = query.where(Material.remaining_stock <= Material.minimum_stock_level)

    query = query.order_by(Material.remaining_stock.asc())

    result = await db.execute(query)
    rows = result.all()

    data = []

    for obj, supplier_name in rows:
        response = build_material_response(obj, supplier_name)

        # ✅ ONLY override alert for NEAR_LOW case
        if (
            threshold is not None
            and response.alert_type == "IN_STOCK"
            and response.remaining_stock <= threshold
        ):
            response.alert_type = "NEAR_LOW"

        data.append(response)

    return data


# ================= PURCHASE ORDERS =================

@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=201)
async def create_po(payload: PurchaseOrderCreate, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES))):

    if payload.quantity <= 0 or payload.rate <= 0:
        raise HTTPException(400, "Quantity and rate must be greater than 0")

    material = await db.get(Material, payload.material_id)
    if not material:
        raise HTTPException(404, "Material not found")

    if material.supplier_id != payload.supplier_id:
        raise HTTPException(400, "Material does not belong to supplier")

    if material.project_id != payload.project_id:
        raise HTTPException(400, "Material does not belong to project")

    total_amount = payload.quantity * payload.rate

    po = PurchaseOrder(
        supplier_id=payload.supplier_id,
        project_id=payload.project_id,
        material_id=payload.material_id,
        material_name=material.material_name,
        quantity=payload.quantity,
        rate=payload.rate,
        total_amount=total_amount,
        status="CREATED",
    )

    db.add(po)
    await db.commit()
    await db.refresh(po)

    return build_po_response(po)


@router.get("/purchase-orders/{id}", response_model=PurchaseOrderOut)
async def get_po(id: int, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):

    po = await db.get(PurchaseOrder, id)

    if not po or po.is_deleted:
        raise HTTPException(404, "PO not found")

    return build_po_response(po)


@router.get("/purchase-orders", response_model=List[PurchaseOrderOut])
async def list_po(skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):

    limit = min(max(limit, 1), 100)

    rows = (
        await db.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.is_deleted == False)
            .order_by(PurchaseOrder.id.desc())
            .offset(skip)
            .limit(limit)
        )
    ).scalars().all()

    return [build_po_response(r) for r in rows]


@router.put("/purchase-orders/{id}", response_model=PurchaseOrderOut)
async def update_po(
    id: int,
    payload: PurchaseOrderCreate,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(PurchaseOrder, id)

    if not obj or obj.is_deleted:
        raise HTTPException(404, "PO not found")

    if payload.quantity <= 0 or payload.rate <= 0:
        raise HTTPException(400, "Invalid quantity or rate")

    material = await db.get(Material, payload.material_id)
    if not material:
        raise HTTPException(404, "Material not found")

    if material.supplier_id != payload.supplier_id:
        raise HTTPException(400, "Material does not belong to supplier")

    if material.project_id != payload.project_id:
        raise HTTPException(400, "Material does not belong to project")

    obj.supplier_id = payload.supplier_id
    obj.project_id = payload.project_id
    obj.material_id = payload.material_id
    obj.material_name = material.material_name
    obj.quantity = payload.quantity
    obj.rate = payload.rate
    obj.total_amount = payload.quantity * payload.rate

    await db.commit()
    await db.refresh(obj)

    return build_po_response(obj)


@router.delete("/purchase-orders/{id}")
async def delete_po(id: int, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),):

    obj = await db.get(PurchaseOrder, id)

    if not obj or obj.is_deleted:
        raise HTTPException(404, "PO not found")

    obj.is_deleted = True

    await db.commit()

    return {"message": "Purchase order deleted successfully"}

# =========================project_transactions=============================

@router.get("/projects/{project_id}/transactions")
async def project_transactions(
    project_id: int,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    if (
        current_user.role != UserRole.ADMIN.value
        and project_id not in (current_user.allowed_projects or [])
    ):
        raise HTTPException(403, "Access denied")

    query = (
        select(MaterialTransaction, Material.material_name, Supplier.name)
        .join(Material, Material.id == MaterialTransaction.material_id)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(MaterialTransaction.project_id == project_id)
        .order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = (await db.execute(query)).all()

    return [
        {
            "id": tx.id,
            "type": tx.type.value,
            "material_id": tx.material_id,
            "material_name": material_name,
            "supplier_name": supplier_name or "N/A",
            "quantity": get_signed_quantity(tx),
            "total_amount": round(float(tx.total_amount or 0), 2),
            "project_id": tx.project_id,
            "created_at": tx.created_at,
        }
        for tx, material_name, supplier_name in rows
    ]


# ================= material_transactions =================


@router.get("/{material_id}/transactions", response_model=List[MaterialLogOut])
async def get_material_transactions(
    material_id: int,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)

    # ✅ validation
    material = await db.get(Material, material_id)
    if not material or material.is_deleted:
        raise HTTPException(404, "Material not found")

    result = await db.execute(
        select(MaterialTransaction)
        .where(MaterialTransaction.material_id == material_id)
        .order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = result.scalars().all()

    data = []

    for r in rows:
        quantity = float(r.quantity or 0)
        total_amount = float(r.total_amount or 0)

        avg_rate = abs(total_amount / quantity) if quantity != 0 else 0

        data.append(
            MaterialLogOut(
                id=r.id,
                material_id=r.material_id,
                type=r.type,
                quantity=round(quantity, 3),
                rate=round(float(r.rate or 0), 2),
                avg_rate=round(avg_rate, 2),
                total_amount=round(total_amount, 2),
                amount_paid=float(r.amount_paid or 0),
                payment_pending=float(r.payment_pending or 0),
                issue_type=r.issue_type,
                project_id=r.project_id,
                created_at=r.created_at,
            )
        )

    return data


# ================= TRANSFERS =================

@router.post("/transfers", response_model=TransferOut)
async def create_transfer(
    payload: TransferCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    redis=Depends(get_request_redis),
):
    try:
        if payload.quantity <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        if payload.from_project_id == payload.to_project_id:
            raise HTTPException(400, "Cannot transfer to same project")

        from_project = await db.get(Project, payload.from_project_id)
        to_project = await db.get(Project, payload.to_project_id)

        if not from_project or not to_project:
            raise HTTPException(404, "Project not found")

        reference = f"TRF-{uuid.uuid4().hex[:8]}"

        #  LOCK SOURCE
        material = await db.scalar(
            select(Material)
            .where(Material.id == payload.material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not material:
            raise HTTPException(404, "Material not found")

        if payload.quantity > material.remaining_stock:
            raise HTTPException(400, "Not enough stock")

        # UPDATE SOURCE
        material.quantity_used += payload.quantity
        update_material_fields(material)

        #  LOCK DESTINATION
        existing_material = await db.scalar(
            select(Material)
            .where(
                Material.project_id == payload.to_project_id,
                func.lower(Material.material_name) == material.material_name.lower(),
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        if existing_material:
            existing_material.quantity_purchased += payload.quantity
            update_material_fields(existing_material)
        else:
            material_code = await generate_business_id(
                db=db,
                model=Material,
                column_name="material_code",
                prefix="MAT",
            )

            existing_material = Material(
                material_code=material_code,
                project_id=payload.to_project_id,
                material_name=material.material_name,
                category=material.category,
                unit=material.unit,
                supplier_id=material.supplier_id,
                purchase_rate=material.purchase_rate,
                rate_type=material.rate_type,
                quantity_purchased=payload.quantity,
                quantity_used=Decimal("0"),
                payment_given=Decimal("0"),
                minimum_stock_level=material.minimum_stock_level,
            )

            db.add(existing_material)
            await db.flush()

        total = payload.quantity * material.purchase_rate

        def create_entry(mat_id, type_, project_id):
            db.add(MaterialTransaction(
                material_id=mat_id,
                type=type_,
                project_id=project_id,
                quantity=payload.quantity,
                rate=material.purchase_rate,
                total_amount=total,
                issue_type=IssueType.TRANSFER,
                reference_id=reference,
            ))

            db.add(MaterialLedger(
                material_id=mat_id,
                type=type_,
                project_id=project_id,
                quantity=payload.quantity,
                rate=material.purchase_rate,
                total_amount=total,
                reference_id=reference,
            ))

        create_entry(material.id, DBTransactionType.TRANSFER_OUT, payload.from_project_id)
        create_entry(existing_material.id, DBTransactionType.TRANSFER_IN, payload.to_project_id)

        obj = MaterialTransfer(
            **payload.model_dump(),
            status="COMPLETED",
            reference_id=reference,
        )

        db.add(obj)

        #  COMMIT HERE
        await db.commit()
        await db.refresh(obj)

    except Exception as e:
        await db.rollback()
        raise e

    await bump_cache_version(redis, VERSION_KEY)

    return build_transfer_response(obj, material, from_project, to_project)


# ================= LIST TRANSFERS =================

@router.get("/transfers")
async def list_transfers(skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):

    skip = max(skip, 0)
    limit = min(max(limit, 1), 100)

    FromProject = aliased(Project)
    ToProject = aliased(Project)

    total = await db.scalar(select(func.count()).select_from(MaterialTransfer))

    rows = (
        await db.execute(
            select(
                MaterialTransfer,
                Material.material_name,
                FromProject.project_name,
                ToProject.project_name,
            )
            .select_from(MaterialTransfer)   
            .join(Material)
            .join(FromProject, FromProject.id == MaterialTransfer.from_project_id)
            .join(ToProject, ToProject.id == MaterialTransfer.to_project_id)
            .order_by(MaterialTransfer.id.desc())
            .offset(skip)
            .limit(limit)
        )
    ).all()

    data = [
        {
            "id": t.id,
            "material": {"id": t.material_id, "name": material_name},
            "from_project": {"id": t.from_project_id, "name": from_name},
            "to_project": {"id": t.to_project_id, "name": to_name},
            "quantity": float(t.quantity),
            "status": t.status,
        }
        for t, material_name, from_name, to_name in rows
    ]

    return {"total": total or 0, "skip": skip, "limit": limit, "data": data}

# ================= GET SINGLE TRANSFER =================

@router.get("/transfers/{id}", response_model=TransferOut)
async def get_transfer(id: int, db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):

    FromProject = aliased(Project)
    ToProject = aliased(Project)

    result = await db.execute(
        select(
            MaterialTransfer,
            Material,
            FromProject,
            ToProject,
        )
        .join(Material, Material.id == MaterialTransfer.material_id)
        .join(FromProject, FromProject.id == MaterialTransfer.from_project_id)
        .join(ToProject, ToProject.id == MaterialTransfer.to_project_id)
        .where(MaterialTransfer.id == id)
    )

    row = result.first()

    if not row:
        raise HTTPException(404, "Transfer not found")

    obj, material, from_project, to_project = row

    return build_transfer_response(obj, material, from_project, to_project)

#=================update_transfer_status=========


VALID_STATUS = {"PENDING", "COMPLETED", "CANCELLED"}

@router.put("/transfers/{id}", response_model=TransferOut)
async def update_transfer_status(
    id: int,
    status: str,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    status = status.upper().strip()

    if status not in VALID_STATUS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Allowed: {', '.join(VALID_STATUS)}",
        )

    obj = await db.get(MaterialTransfer, id)

    if not obj:
        raise HTTPException(404, "Transfer not found")

    obj.status = status

    await db.commit()
    await db.refresh(obj)

    material = await db.get(Material, obj.material_id)
    from_project = await db.get(Project, obj.from_project_id)
    to_project = await db.get(Project, obj.to_project_id)

    return TransferOut(
        id=obj.id,
        material=TransferMaterial(
            id=material.id, 
            name=material.material_name
        ) if material else None,

        from_project=TransferProject(
            id=from_project.id, 
            name=from_project.project_name
        ) if from_project else None,

        to_project=TransferProject(
            id=to_project.id, 
            name=to_project.project_name
        ) if to_project else None,

        quantity=obj.quantity,
        status=obj.status,
        created_at=obj.created_at,
    )


# ================= USAGE =================
@router.post("/{material_id}/usage", response_model=MaterialOut)
async def usage(
    material_id: int,
    data: UsageMaterial,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    import uuid
    from decimal import Decimal
    from datetime import datetime

    obj = await db.scalar(
        select(Material)
        .options(selectinload(Material.supplier))
        .where(Material.id == material_id, Material.is_deleted == False)
        .with_for_update()
    )

    if not obj:
        raise HTTPException(404, "Material not found")

    qty = Decimal(str(data.quantity))

    if qty <= 0:
        raise HTTPException(400, "Quantity must be > 0")

    # ===== STOCK CHECK =====
    purchased = obj.quantity_purchased or Decimal("0")
    used = obj.quantity_used or Decimal("0")

    current_stock = purchased - used

    if current_stock <= 0:
        raise HTTPException(400, "Stock exhausted")

    if qty > current_stock:
        raise HTTPException(400, "Not enough stock")

    total_amount_current = obj.total_amount or Decimal("0")

    # ===== WAC CALC =====
    avg_rate = (
        total_amount_current / purchased if purchased > 0 else Decimal("0")
    )

    used_value = qty * avg_rate

    reference = f"USE-{uuid.uuid4().hex[:8]}"
    issue_type = data.issue_type or IssueType.SYSTEM

    try:
        # ===== TRANSACTION =====
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.USAGE,
                quantity=qty,
                rate=avg_rate,
                total_amount=used_value,
                amount_paid=0,
                payment_pending=0,
                issue_type=issue_type,
                project_id=data.project_id,
                remarks="Material used",
                reference_id=reference,
            )
        )

        # ===== LEDGER =====
        db.add(
            MaterialLedger(
                material_id=obj.id,
                type=DBTransactionType.USAGE,
                quantity=qty,
                rate=avg_rate,
                total_amount=used_value,
                amount_paid=0,
                payment_pending=0,
                issue_type=issue_type,
                project_id=data.project_id,
                remarks="Material used",
                reference_id=reference,
            )
        )

        # ===== ✅ FIX: USAGE TABLE INSERT =====
        db.add(
            MaterialUsage(
                material_id=obj.id,
                project_id=data.project_id,
                quantity_used=qty,
                usage_date=datetime.utcnow(),
            )
        )

        # ===== UPDATE MATERIAL =====
        obj.quantity_used = used + qty

        update_material_fields(obj)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier

    # ===== RESPONSE CALC =====
    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    # ===== ALERT =====
    if obj.remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,
        project_id=obj.project_id,
        material_name=obj.material_name.strip().title(),
        category=obj.category,
        unit=obj.unit,
        supplier_id=obj.supplier_id,
        supplier_name=supplier.name if supplier else None,
        purchase_rate=float(obj.purchase_rate),
        rate_type=obj.rate_type,
        quantity_purchased=float(obj.quantity_purchased),
        quantity_used=float(obj.quantity_used),
        remaining_stock=float(obj.remaining_stock),
        total_amount=round(total_amount, 2),
        payment_given=round(payment_given, 2),
        payment_pending=round(payment_pending, 2),
        extra_paid=round(extra_paid, 2),
        minimum_stock_level=float(obj.minimum_stock_level or 0),
        alert_type=alert_type,
    )

# ================= PURCHASE =================

@router.post("/{material_id}/purchase", response_model=MaterialOut)
async def purchase(
    material_id: int,
    data: PurchaseMaterial,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    import uuid
    from decimal import Decimal

    try:
        obj = await db.scalar(
            select(Material)
            .options(selectinload(Material.supplier))
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not obj:
            raise HTTPException(404, "Material not found")

        qty = Decimal(str(data.quantity))
        paid = Decimal(str(data.amount_paid or 0))

        if qty <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        if paid < 0:
            raise HTTPException(400, "Payment cannot be negative")

        total = qty * obj.purchase_rate

        # ✅ SAFE PAYMENT CALC
        payment_pending = max(0, total - paid)
        extra_paid = max(0, paid - total)

        reference = f"PUR-{uuid.uuid4().hex[:8]}"

        # ===== TRANSACTION =====
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=qty,
                rate=obj.purchase_rate,
                total_amount=total,
                amount_paid=paid,
                payment_pending=payment_pending,
                issue_type=IssueType.PURCHASE,
                project_id=data.project_id,
                remarks="Material purchased",
                reference_id=reference,
            )
        )

        # ===== LEDGER =====
        db.add(
            MaterialLedger(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=qty,
                rate=obj.purchase_rate,
                total_amount=total,
                amount_paid=paid,
                payment_pending=payment_pending,
                issue_type=IssueType.PURCHASE,
                project_id=data.project_id,
                remarks="Material purchased",
                reference_id=reference,
            )
        )

        # ===== UPDATE MATERIAL =====
        obj.quantity_purchased = (obj.quantity_purchased or Decimal("0")) + qty
        obj.payment_given = (obj.payment_given or Decimal("0")) + paid
        obj.total_amount = (obj.total_amount or Decimal("0")) + total

        update_material_fields(obj)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier

    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    # ✅ FIXED ALERT
    if obj.remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,
        project_id=obj.project_id,
        material_name=obj.material_name.strip().title(),  # ✅ normalize
        category=obj.category,
        unit=obj.unit,
        supplier_id=obj.supplier_id,
        supplier_name=supplier.name if supplier else None,
        purchase_rate=float(obj.purchase_rate),
        rate_type=obj.rate_type,
        quantity_purchased=float(obj.quantity_purchased),
        quantity_used=float(obj.quantity_used),
        remaining_stock=float(obj.remaining_stock),
        total_amount=round(total_amount, 2),           # ✅ rounding
        payment_given=round(payment_given, 2),
        payment_pending=round(payment_pending, 2),
        extra_paid=round(extra_paid, 2),
        minimum_stock_level=float(obj.minimum_stock_level or 0),
        alert_type=alert_type,
    )

# ================= ADD INVENTORY =================

@router.post("/inventory")
async def adjust_inventory(
    payload: InventoryAdjustRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    redis=Depends(get_request_redis),
):
    material_id = payload.material_id
    reason = (payload.reason or "").strip()

    try:
        new_stock = Decimal(str(payload.new_stock))
    except:
        raise HTTPException(400, "Invalid stock value")

    if new_stock < 0:
        raise HTTPException(400, "Stock cannot be negative")

    if not reason:
        raise HTTPException(400, "Reason is required")

    try:
        material = await db.scalar(
            select(Material)
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not material:
            raise HTTPException(404, "Material not found")

        old_stock = material.remaining_stock or Decimal("0")
        diff = new_stock - old_stock

        if diff == 0:
            return {
                "material_id": material_id,
                "old_stock": float(old_stock),
                "new_stock": float(old_stock),
                "difference": 0,
                "reason": reason,
            }

        reference = f"ADJ-{uuid.uuid4().hex[:8]}"

        qty_purchased = material.quantity_purchased or Decimal("0")
        total_amt = material.total_amount or Decimal("0")

        avg_rate = total_amt / qty_purchased if qty_purchased > 0 else Decimal("0")

        adjustment_value = abs(diff) * avg_rate

        # ONLY STOCK CHANGE (NO COST CHANGE)
        if diff > 0:
            material.quantity_purchased += diff
        else:
            material.quantity_used += abs(diff)

        update_material_fields(material)

        audit_remark = f"Stock adjusted: {old_stock} → {new_stock} | {reason}"

        db.add(
            MaterialTransaction(
                material_id=material.id,
                type=DBTransactionType.ADJUSTMENT,
                quantity=diff,
                rate=avg_rate,
                total_amount=adjustment_value,
                amount_paid=0,
                payment_pending=0,
                issue_type=IssueType.SYSTEM,
                project_id=material.project_id,
                remarks=audit_remark,
                reference_id=reference,
            )
        )

        db.add(
            MaterialLedger(
                material_id=material.id,
                type=DBTransactionType.ADJUSTMENT,
                quantity=diff,
                rate=avg_rate,
                total_amount=adjustment_value,
                amount_paid=0,
                payment_pending=0,
                project_id=material.project_id,
                remarks=audit_remark,
                reference_id=reference,
            )
        )

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await bump_cache_version(redis, VERSION_KEY)

    return {
        "material_id": material_id,
        "old_stock": float(old_stock),
        "new_stock": float(new_stock),
        "difference": float(diff),
        "reason": reason,
        "reference_id": reference,
    }

#===============get_all_inventory===========================

@router.get("/inventory")
async def get_all_inventory(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES))):
    result = await db.execute(
        select(
            Material.id,
            Material.material_name,
            Material.remaining_stock,
            Material.unit,
            Material.project_id,
            Material.total_amount,
            Material.quantity_purchased,
        ).where(Material.is_deleted == False)
    )

    rows = result.all()

    data = []

    for r in rows:
        qty_purchased = r.quantity_purchased or Decimal("0")
        remaining = r.remaining_stock or Decimal("0")
        total_amount = r.total_amount or Decimal("0")

        avg_rate = (
            total_amount / qty_purchased
            if qty_purchased > 0 else Decimal("0")
        )

        total_value = remaining * avg_rate

        # rounding at final stage
        avg_rate = avg_rate.quantize(Decimal("0.01"))
        total_value = total_value.quantize(Decimal("0.01"))

        data.append(
            {
                "material_id": r.id,
                "material_name": (r.material_name or "").strip().title(),
                "remaining_stock": float(remaining),
                "unit": r.unit,
                "avg_rate": float(avg_rate),
                "total_value": float(total_value),
                "project_id": r.project_id,
            }
        )

    return data

#==================get_inventory_valuation=======================

@router.get("/inventory/valuation")
async def get_inventory_valuation(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    result = await db.execute(
        select(
            Material.quantity_purchased,
            Material.remaining_stock,
            Material.total_amount,
        ).where(Material.is_deleted == False)
    )

    rows = result.all()

    total_value = Decimal("0")

    for r in rows:
        purchased = r.quantity_purchased or Decimal("0")
        remaining = r.remaining_stock or Decimal("0")
        total_amount = r.total_amount or Decimal("0")

        avg_rate = (
            total_amount / purchased
            if purchased > 0 else Decimal("0")
        )

        total_value += remaining * avg_rate

    return {
        "total_value": float(total_value.quantize(Decimal("0.01")))
    }

#======================================================
@router.get("/inventory/{project_id}")
async def get_project_inventory(
    project_id: int,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(
            Material.id,
            Material.material_name,
            Material.remaining_stock,
            Material.total_amount,
            Material.quantity_purchased,
        )
        .where(Material.project_id == project_id, Material.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )

    rows = result.all()

    data = []

    for r in rows:
        purchased = r.quantity_purchased or Decimal("0")
        remaining = r.remaining_stock or Decimal("0")
        total_amt = r.total_amount or Decimal("0")

        avg_rate = (
            total_amt / purchased
            if purchased > 0 else Decimal("0")
        )

        total_value = remaining * avg_rate

        data.append(
            {
                "material_id": r.id,
                "material_name": (r.material_name or "").strip().title(),
                "remaining_stock": float(remaining),
                "avg_rate": float(avg_rate.quantize(Decimal("0.01"))),
                "total_value": float(total_value.quantize(Decimal("0.01"))),
            }
        )

    return data


# ================= FILTERED LOGS =================

@router.get("/logs", response_model=List[MaterialLogOut])
async def logs(
    limit: int = 50,
    offset: int = 0,
    material_id: Optional[int] = None,
    project_id: Optional[int] = None,
    type: Optional[SchemaTransactionType] = None,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)

    query = select(MaterialTransaction)

    if material_id is not None:
        query = query.where(MaterialTransaction.material_id == material_id)

    if project_id is not None:
        query = query.where(MaterialTransaction.project_id == project_id)

    if type is not None:
        query = query.where(MaterialTransaction.type == type.value)

    query = (
        query.order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.scalars().all()

    logs = []

    for r in rows:
        quantity = r.quantity or Decimal("0")
        total_amount = r.total_amount or Decimal("0")
        rate = r.rate or Decimal("0")

        logs.append(
            MaterialLogOut(
                id=r.id,
                material_id=r.material_id,
                type=r.type.value,
                quantity=float(round(quantity, 3)),
                rate=float(round(rate, 2)),
                avg_rate=float(round(rate, 2)),  # use rate directly
                total_amount=float(round(total_amount, 2)),
                amount_paid=float(r.amount_paid or 0),
                payment_pending=float(r.payment_pending or 0),
                issue_type=r.issue_type,
                project_id=r.project_id,
                created_at=r.created_at,
            )
        )

    return logs


# ================= REPORTS =================

@router.get("/reports", response_model=List[MaterialReport])
async def material_report(
    project_id: Optional[int] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)

    query = select(Material).where(Material.is_deleted == False)

    if project_id is not None:
        query = query.where(Material.project_id == project_id)

    if category is not None:
        query = query.where(func.lower(Material.category) == category.lower())

    query = query.order_by(Material.id.desc()).offset(skip).limit(limit)

    rows = (await db.execute(query)).scalars().all()

    data = []

    for r in rows:
        purchased = r.quantity_purchased or Decimal("0")
        used = r.quantity_used or Decimal("0")
        remaining = r.remaining_stock or Decimal("0")
        total_amt = r.total_amount or Decimal("0")

        avg_rate = (
            total_amt / purchased
            if purchased > 0 else Decimal("0")
        ).quantize(Decimal("0.01"))

        total_cost = (remaining * avg_rate).quantize(Decimal("0.01"))

        data.append(
            MaterialReport(
                material_id=r.id,
                material_name=(r.material_name or "").strip().title(),
                total_purchased=float(purchased),
                total_used=float(used),
                remaining_stock=float(remaining),
                total_cost=float(total_cost),
                payment_pending=float(
                    (r.payment_pending or Decimal("0")).quantize(Decimal("0.01"))
                ),
            )
        )

    return data


# ======================PDF REPORT=============================================

from starlette.background import BackgroundTask

@router.get("/reports/materials/pdf", response_class=FileResponse)
async def export_pdf(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),):

    from decimal import Decimal
    import tempfile, os, uuid
    from datetime import datetime

    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.pagesizes import A4

    # ===== FETCH MATERIAL =====
    rows = (
        await db.execute(
            select(Material).where(Material.is_deleted == False)
        )
    ).scalars().all()

    file_path = os.path.join(tempfile.gettempdir(), f"material_{uuid.uuid4()}.pdf")

    doc = SimpleDocTemplate(file_path, pagesize=A4)
    styles = getSampleStyleSheet()
    elements = []

    # ===== HEADER =====
    elements.append(Paragraph("<b>Material Inventory Report</b>", styles["Title"]))
    elements.append(
        Paragraph(
            f"Generated on: {datetime.utcnow().strftime('%d-%m-%Y %H:%M')}",
            styles["Normal"],
        )
    )
    elements.append(Spacer(1, 20))

    # ===== TABLE HEADER =====
    data = [[
        "ID",
        "Material",
        "Purchased",
        "Used",
        "Remaining",
        "Transfer In",
        "Transfer Out",
        "Cost",
        "Paid",
        "Pending"
    ]]

    grand_total = Decimal("0")

    for m in rows:

        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        paid = m.payment_given or Decimal("0")
        pending = m.payment_pending or Decimal("0")

        # ===== WAC =====
        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")
        cost = (remaining * avg_rate).quantize(Decimal("0.01"))

        # ===== TRANSFER CALC =====
        transfer_in = await db.scalar(
            select(func.sum(MaterialTransaction.quantity)).where(
                MaterialTransaction.material_id == m.id,
                MaterialTransaction.type == DBTransactionType.TRANSFER_IN
            )
        ) or Decimal("0")

        transfer_out = await db.scalar(
            select(func.sum(MaterialTransaction.quantity)).where(
                MaterialTransaction.material_id == m.id,
                MaterialTransaction.type == DBTransactionType.TRANSFER_OUT
            )
        ) or Decimal("0")

        grand_total += cost

        data.append([
            m.id,
            (m.material_name or "").title(),
            float(purchased),
            float(used),
            float(remaining),
            float(transfer_in),
            float(transfer_out),
            float(cost),
            float(paid),
            float(pending),
        ])

    # ===== TOTAL ROW =====
    data.append(["", "", "", "", "", "", "TOTAL", float(grand_total), "", ""])

    # ===== TABLE =====
    table = Table(data, repeatRows=1)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E86C1")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),

        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),

        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.whitesmoke, colors.lightgrey]),

        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),

        # TOTAL highlight
        ("BACKGROUND", (0, -1), (-1, -1), colors.lightblue),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))

    elements.append(table)

    doc.build(elements)

    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename="material_full_report.pdf",
        background=BackgroundTask(safe_delete, file_path),
    )
# ==================excel report=====================

@router.get("/reports/materials/excel", response_class=FileResponse)
async def export_excel(db: AsyncSession = Depends(get_db_session),current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),):

    from decimal import Decimal

    materials = (
        await db.execute(
            select(Material).where(Material.is_deleted == False)
        )
    ).scalars().all()

    file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name

    wb = Workbook()
    ws = wb.active
    ws.title = "Material Report"

    # ===== STYLES =====
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="2E86C1", end_color="2E86C1", fill_type="solid")
    center_align = Alignment(horizontal="center", vertical="center")
    right_align = Alignment(horizontal="right")
    border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # ===== HEADER =====
    headers = [
        "ID", "Material", "Purchased", "Used", "Remaining",
        "Transfer In", "Transfer Out",
        "Cost", "Paid", "Pending"
    ]
    ws.append(headers)

    for col_num, col_name in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_num)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center_align
        cell.border = border

    ws.freeze_panes = "A2"

    total_cost = Decimal("0")

    row_idx = 2

    for m in materials:

        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        paid = m.payment_given or Decimal("0")
        pending = m.payment_pending or Decimal("0")

        # ===== WAC =====
        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")
        cost = remaining * avg_rate

        # ===== TRANSFER =====
        transfer_in = await db.scalar(
            select(func.sum(MaterialTransaction.quantity)).where(
                MaterialTransaction.material_id == m.id,
                MaterialTransaction.type == DBTransactionType.TRANSFER_IN
            )
        ) or Decimal("0")

        transfer_out = await db.scalar(
            select(func.sum(MaterialTransaction.quantity)).where(
                MaterialTransaction.material_id == m.id,
                MaterialTransaction.type == DBTransactionType.TRANSFER_OUT
            )
        ) or Decimal("0")

        total_cost += cost

        row = [
            m.id,
            (m.material_name or "").title(),
            float(purchased),
            float(used),
            float(remaining),
            float(transfer_in),
            float(transfer_out),
            float(round(cost, 2)),
            float(paid),
            float(pending),
        ]

        ws.append(row)

        # ===== STYLE ROW =====
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=row_idx, column=col)
            cell.border = border

            if col >= 3:  # numeric columns
                cell.alignment = right_align
            else:
                cell.alignment = center_align

        row_idx += 1

    # ===== TOTAL ROW =====
    ws.append([])
    ws.append(["", "", "", "", "", "", "TOTAL", float(round(total_cost, 2)), "", ""])

    total_row = ws.max_row
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=total_row, column=col)
        cell.font = Font(bold=True)
        cell.fill = PatternFill(start_color="D6EAF8", end_color="D6EAF8", fill_type="solid")

    # ===== AUTO FILTER =====
    ws.auto_filter.ref = f"A1:J{ws.max_row}"

    # ===== AUTO WIDTH =====
    for col in ws.columns:
        max_length = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max_length + 3

    wb.save(file_path)

    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="material_advanced_report.xlsx",
        background=BackgroundTask(safe_delete, file_path),
    )

# ===============price-history==========================
@router.get("/price-history/{material_id}")
async def price_history(
    material_id: int,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    from decimal import Decimal

    result = await db.execute(
        select(
            MaterialTransaction.rate,
            MaterialTransaction.created_at
        )
        .where(
            MaterialTransaction.material_id == material_id,
            MaterialTransaction.type == DBTransactionType.PURCHASE,  # ✅ FIX
        )
        .order_by(MaterialTransaction.created_at.asc())
    )

    rows = result.all()

    if not rows:
        raise HTTPException(status_code=404, detail="No price history found")

    history = []
    last_rate: Decimal | None = None

    for rate, created_at in rows:
        rate = rate or Decimal("0")

        # ✅ avoid float precision issue
        if last_rate is None or rate != last_rate:
            history.append(
                {
                    "rate": float(round(rate, 2)),
                    "date": created_at.strftime("%Y-%m-%d %H:%M"),  # ✅ clean format
                }
            )
            last_rate = rate

    return history

# ================= MATERIALS - DYNAMIC ROUTES =================
@router.post("", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    redis=Depends(get_request_redis),
):
    from decimal import Decimal
    import uuid

    data = payload.model_dump()

    # ===== NORMALIZE NAME =====
    raw_name = payload.material_name.strip()
    normalized_name = raw_name.lower()
    data["material_name"] = raw_name  # store clean name

    # ===== VALIDATE SUPPLIER =====
    supplier = await db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    # ===== DUPLICATE CHECK =====
    existing = await db.scalar(
        select(Material).where(
            Material.project_id == payload.project_id,
            Material.supplier_id == payload.supplier_id,
            func.lower(Material.material_name) == normalized_name,
            Material.is_deleted == False,
        )
    )
    if existing:
        raise HTTPException(400, "Material already exists for this project & supplier")

    # ===== GENERATE MATERIAL CODE =====
    material_code = await generate_business_id(
        db=db,
        model=Material,
        column_name="material_code",
        prefix="MAT",
    )

    # ===== CREATE OBJECT =====
    obj = Material(
        **data,
        material_code=material_code,
    )

    # ===== DEFAULT VALUES =====
    obj.quantity_purchased = obj.quantity_purchased or Decimal("0")
    obj.quantity_used = Decimal("0")
    obj.payment_given = obj.payment_given or Decimal("0")

    try:
        db.add(obj)
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Material already exists")

    # ===== CALCULATIONS =====
    obj.total_amount = (obj.quantity_purchased * obj.purchase_rate).quantize(Decimal("0.01"))

    # 👉 use centralized logic
    update_material_fields(obj)

    # ===== ALERT TYPE =====
    alert_type = get_alert_type(obj)

    # ===== REFERENCE =====
    reference = f"INIT-{uuid.uuid4().hex[:8]}"

    # ===== TRANSACTION + LEDGER =====
    if obj.quantity_purchased > 0:
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=obj.quantity_purchased,
                rate=obj.purchase_rate,
                total_amount=obj.total_amount,
                amount_paid=obj.payment_given,
                payment_pending=obj.payment_pending,
                issue_type=IssueType.PURCHASE,
                project_id=obj.project_id,
                remarks="Initial material entry",
                reference_id=reference,
            )
        )

        db.add(
            MaterialLedger(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=obj.quantity_purchased,
                rate=obj.purchase_rate,
                total_amount=obj.total_amount,
                amount_paid=obj.payment_given,
                payment_pending=obj.payment_pending,
                project_id=obj.project_id,
                remarks="Initial material entry",
                reference_id=reference,
            )
        )

    # ===== COMMIT =====
    await db.commit()
    await db.refresh(obj)

    await bump_cache_version(redis, VERSION_KEY)

    # ===== RESPONSE =====
    response = MaterialOut.model_validate({**obj.__dict__, "alert_type": alert_type})

    response.supplier_name = supplier.name
    response.material_name = obj.material_name.title()

    return response

#=================list_materials=========================

@router.get("", response_model=list[MaterialOut])
async def list_materials(
    project_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    # 🔹 JOIN for supplier (N+1 fix)
    query = (
        select(Material, Supplier.name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.is_deleted == False)
    )

    if project_id:
        query = query.where(Material.project_id == project_id)

    # pagination + sorting (optional but useful)
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    data = []

    for obj, supplier_name in rows:
        total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

        # alert logic (common)
        if obj.remaining_stock == 0:
            alert_type = "OUT_OF_STOCK"
        elif obj.remaining_stock <= obj.minimum_stock_level:
            alert_type = "LOW_STOCK"
        else:
            alert_type = "IN_STOCK"

        data.append(
            MaterialOut(
                id=obj.id,
                material_code=obj.material_code,
                project_id=obj.project_id,
                material_name=obj.material_name.strip().title(),  # normalize
                category=obj.category,
                unit=obj.unit,
                supplier_id=obj.supplier_id,
                supplier_name=supplier_name,
                purchase_rate=float(obj.purchase_rate),
                rate_type=obj.rate_type,
                quantity_purchased=float(obj.quantity_purchased),
                quantity_used=float(obj.quantity_used),
                remaining_stock=float(obj.remaining_stock),
                total_amount=total_amount,
                payment_given=payment_given,
                payment_pending=payment_pending,
                extra_paid=extra_paid,
                minimum_stock_level=float(obj.minimum_stock_level or 0),
                alert_type=alert_type,
            )
        )

    return data

#==============get_material=================

@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    obj = await db.get(Material, material_id)

    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail="Material not found")

    supplier = await db.get(Supplier, obj.supplier_id)

    total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

    # FIX: alert logic
    if obj.remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,
        project_id=obj.project_id,
        material_name=obj.material_name.strip().title(),
        category=obj.category,
        unit=obj.unit,
        supplier_id=obj.supplier_id,
        supplier_name=supplier.name if supplier else None,
        purchase_rate=float(obj.purchase_rate),
        rate_type=obj.rate_type,
        quantity_purchased=float(obj.quantity_purchased),
        quantity_used=float(obj.quantity_used),
        remaining_stock=float(obj.remaining_stock),
        total_amount=total_amount,
        payment_given=payment_given,
        payment_pending=payment_pending,
        extra_paid=extra_paid,
        minimum_stock_level=float(obj.minimum_stock_level or 0),
        alert_type=alert_type,
    )

#=============update_material==================
@router.put("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(
        select(Material)
        .where(Material.id == material_id, Material.is_deleted == False)
        .with_for_update()
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Material not found")

    update_data = payload.model_dump(exclude_unset=True)

    # ❌ direct payment block
    if "payment_given" in update_data:
        raise HTTPException(
            status_code=400,
            detail="Direct payment update not allowed. Use purchase API",
        )

    # ✅ NORMALIZE NAME
    if "material_name" in update_data:
        new_name = update_data["material_name"].strip()
        normalized_name = new_name.lower()
        update_data["material_name"] = new_name

        # ✅ DUPLICATE CHECK (IMPORTANT FIX 🔥)
        existing = await db.scalar(
            select(Material).where(
                Material.project_id == obj.project_id,
                Material.supplier_id == obj.supplier_id,
                func.lower(Material.material_name) == normalized_name,
                Material.id != obj.id,  # exclude current
                Material.is_deleted == False,
            )
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Material already exists for this project & supplier",
            )

    try:
        # ✅ apply updates
        for k, v in update_data.items():
            setattr(obj, k, v)

        # ✅ recalc
        update_material_fields(obj)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = await db.get(Supplier, obj.supplier_id)

    total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

    # ✅ alert fix (<= important)
    if obj.remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,
        project_id=obj.project_id,
        material_name=obj.material_name.strip().title(),
        category=obj.category,
        unit=obj.unit,
        supplier_id=obj.supplier_id,
        supplier_name=supplier.name if supplier else None,
        purchase_rate=float(obj.purchase_rate or 0),
        rate_type=obj.rate_type,
        quantity_purchased=float(obj.quantity_purchased or 0),
        quantity_used=float(obj.quantity_used or 0),
        remaining_stock=float(obj.remaining_stock or 0),
        total_amount=round(total_amount, 2),
        payment_given=round(payment_given, 2),
        payment_pending=round(payment_pending, 2),
        extra_paid=round(extra_paid, 2),
        minimum_stock_level=float(obj.minimum_stock_level or 0),
        alert_type=alert_type,
    )

#============delete_material===========

@router.delete("/{material_id}")
async def delete_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    redis=Depends(get_request_redis),
):
    obj = await db.get(Material, material_id)

    if not obj:
        raise HTTPException(status_code=404, detail="Material not found")

    obj.is_deleted = True

    await db.commit()
    await db.refresh(obj)

    await bump_cache_version(redis, VERSION_KEY)

    return {"message": "Deleted"}

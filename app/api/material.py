from datetime import date
from typing import Optional, List
from decimal import Decimal
import uuid
import os
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from reportlab.platypus import SimpleDocTemplate, Table
from openpyxl import Workbook
from app.schemas.material import TransferMaterial, TransferProject
from fastapi import HTTPException
from app.cache.redis import bump_cache_version
from app.core.dependencies import get_request_redis
from app.db.session import get_db_session
from app.schemas.material import MaterialReport
from app.schemas.material import PriceHistoryOut
import tempfile
from app.core.enums import IssueType, TransactionType
from app.utils.common import generate_business_id
from sqlalchemy.orm import selectinload

from app.models.material import (
    Material,
    MaterialLedger,
    MaterialTransaction,
    Supplier,
    PurchaseOrder,
    MaterialTransfer,
)

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

router = APIRouter(prefix="/materials", tags=["materials"])
VERSION_KEY = "cache_version:materials"

# ================= CENTRAL CALCULATION =================

def update_material_fields(obj: Material):
    qty_purchased = obj.quantity_purchased or Decimal("0")
    qty_used = obj.quantity_used or Decimal("0")
    payment_given = obj.payment_given or Decimal("0")
    total_amount = obj.total_amount or Decimal("0")

    obj.remaining_stock = max(qty_purchased - qty_used, Decimal("0"))

    obj.payment_pending = max(total_amount - payment_given, Decimal("0"))

    obj.advance_amount = max(payment_given - total_amount, Decimal("0"))

def to_transaction_type(value):
    if isinstance(value, DBTransactionType):
        return value

    if isinstance(value, str):
        try:
            return DBTransactionType(value.upper())
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"Invalid transaction type: {value}"
            )

    raise HTTPException(status_code=400, detail="Invalid transaction type format")

def safe_delete(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

def to_issue_type(value):
    if isinstance(value, IssueType):
        return value

    if isinstance(value, str):
        try:
            return IssueType(value.upper())
        except ValueError:
            raise HTTPException(400, f"Invalid issue type: {value}")

    return IssueType.SYSTEM

def to_material_out(obj, supplier_name):
    return {
        "id": obj.id,
        "project_id": obj.project_id,
        "material_name": obj.material_name,
        "category": obj.category,
        "unit": obj.unit,
        "supplier_id": obj.supplier_id,
        "supplier_name": supplier_name,
        "purchase_rate": float(obj.purchase_rate),
        "quantity_purchased": float(obj.quantity_purchased),
        "quantity_used": float(obj.quantity_used),
        "remaining_stock": float(obj.remaining_stock),
        "total_amount": float(obj.total_amount),
        "payment_given": float(obj.payment_given),
        "payment_pending": float(obj.payment_pending),
        "minimum_stock_level": float(obj.minimum_stock_level),
    }

def calculate_fields(obj):
    total_amount = float(obj.purchase_rate) * float(obj.quantity_purchased)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    return total_amount, payment_given, payment_pending, extra_paid

def map_material(obj, supplier_name):
    total_amount = float(obj.purchase_rate) * float(obj.quantity_purchased)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    return MaterialOut(...)

def calculate_material_fields(m):
    total_amount = float(m.purchase_rate) * float(m.quantity_purchased)
    payment_given = float(m.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    remaining_stock = float(m.quantity_purchased) - float(m.quantity_used)

    if remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif remaining_stock < float(m.minimum_stock_level or 0):
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    return total_amount, payment_given, payment_pending, extra_paid, remaining_stock, alert_type

# ================= SUMMARY =================

@router.get("/summary", response_model=SummaryOut)
async def material_summary(db: AsyncSession = Depends(get_db_session)):

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
        "total_stock_value": total_stock or Decimal("0"),
        "total_pending_payments": total_pending or Decimal("0"),
    }

# ================= SUPPLIERS =================

@router.get("/suppliers", response_model=List[SupplierOut])
async def list_suppliers(
    skip: int = 0,
    limit: int = 50,
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
async def get_supplier(id: int, db: AsyncSession = Depends(get_db_session)):

    obj = await db.scalar(
        select(Supplier).where(Supplier.id == id, Supplier.is_deleted == False)
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Supplier not found")

    return SupplierOut.model_validate(obj)

@router.post("/suppliers", response_model=SupplierOut, status_code=201)
async def create_supplier(
    payload: SupplierCreate,
    db: AsyncSession = Depends(get_db_session),
):
    name = payload.name.strip()
    contact = payload.contact.strip()

    import re
    if not re.match(r"^[6-9]\d{9}$", contact):
        raise HTTPException(status_code=400, detail="Invalid contact number")

    normalized_name = name.lower()

    existing = await db.scalar(
        select(Supplier).where(
            func.lower(Supplier.name) == normalized_name,
            Supplier.contact == contact,
            Supplier.is_deleted == False
        )
    )

    if existing:
        raise HTTPException(status_code=400, detail="Supplier already exists")

    supplier = Supplier(
        name=name,   
        contact=contact
    )

    try:
        db.add(supplier)
        await db.commit()
        await db.refresh(supplier)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Supplier already exists")

    return supplier

@router.put("/suppliers/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: int,
    payload: SupplierCreate,
    db: AsyncSession = Depends(get_db_session),
):
    supplier = await db.get(Supplier, supplier_id)

    if not supplier or supplier.is_deleted:
        raise HTTPException(404, "Supplier not found")

    new_name = payload.name.strip().title()
    new_contact = payload.contact.strip()

    # validation
    if not new_contact.isdigit() or len(new_contact) != 10:
        raise HTTPException(400, "Invalid contact number")

    # no-op check
    if supplier.name == new_name and supplier.contact == new_contact:
        return supplier

    # duplicate check
    existing = await db.scalar(
        select(Supplier).where(
            Supplier.contact == new_contact,
            Supplier.id != supplier_id,
            Supplier.is_deleted == False
        )
    )
    if existing:
        raise HTTPException(400, "Contact already used by another supplier")

    try:
        async with db.begin():
            supplier.name = new_name
            supplier.contact = new_contact
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Contact already used by another supplier")

    await db.refresh(supplier)
    return supplier

@router.delete("/suppliers/{id}")
async def delete_supplier(
    id: int,
    db: AsyncSession = Depends(get_db_session)
):
    obj = await db.scalar(
        select(Supplier).where(
            Supplier.id == id,
            Supplier.is_deleted == False
        )
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Supplier not found")

    in_use = await db.scalar(
        select(func.count()).where(Material.supplier_id == id)
    )

    if in_use and in_use > 0:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete supplier linked to materials"
        )

    obj.is_deleted = True

    await db.commit()

    return {"message": "Deleted"}


# ================= supplier materials =================

@router.get("/suppliers/{supplier_id}/materials", response_model=list[MaterialOut])
async def get_supplier_materials(
    supplier_id: int,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(Material, Supplier.name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.supplier_id == supplier_id, Material.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    data = []

    for m, supplier_name in rows:
        # centralized calculation
        total_amount = float(m.purchase_rate) * float(m.quantity_purchased)
        payment_given = float(m.payment_given or 0)
        payment_pending = max(0, total_amount - payment_given)
        extra_paid = max(0, payment_given - total_amount)

        if m.remaining_stock == 0:
            alert_type = "OUT_OF_STOCK"
        elif m.remaining_stock <= m.minimum_stock_level:
            alert_type = "LOW_STOCK"
        else:
            alert_type = "IN_STOCK"

        data.append(
            MaterialOut(
                id=m.id,
                material_code=m.material_code,
                project_id=m.project_id,
                material_name=m.material_name.strip().title(),
                category=m.category,
                unit=m.unit,
                supplier_id=m.supplier_id,
                supplier_name=supplier_name,
                purchase_rate=float(m.purchase_rate),
                rate_type=m.rate_type,
                quantity_purchased=float(m.quantity_purchased),
                quantity_used=float(m.quantity_used),
                remaining_stock=float(m.remaining_stock),
                total_amount=total_amount,
                payment_given=payment_given,
                payment_pending=payment_pending,
                extra_paid=extra_paid,
                minimum_stock_level=float(m.minimum_stock_level or 0),
                alert_type=alert_type, 
            )
        )

    return data

# ================= material_alerts =================

@router.get("/alerts", response_model=list[MaterialOut])
async def get_material_alerts(
    threshold: float | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    remaining = Material.remaining_stock

    query = (
        select(Material, Supplier.name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.is_deleted == False)
    )

    if threshold is not None:
        query = query.where(remaining <= threshold)
    else:
        query = query.where(remaining <= Material.minimum_stock_level)

    query = query.order_by(remaining.asc())

    result = await db.execute(query)
    rows = result.all()

    data = []

    for obj, supplier_name in rows:
        total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

        remaining_stock = float(obj.remaining_stock)

        if remaining_stock == 0:
            alert_type = "OUT_OF_STOCK"
        elif remaining_stock <= float(obj.minimum_stock_level or 0):
            alert_type = "LOW_STOCK"
        else:
            alert_type = "IN_STOCK"

        data.append(
            MaterialOut(
                id=obj.id,
                material_code=obj.material_code,
                project_id=obj.project_id,
                material_name=obj.material_name.title(), 
                category=obj.category,
                unit=obj.unit,
                supplier_id=obj.supplier_id,
                supplier_name=supplier_name,
                purchase_rate=float(obj.purchase_rate),
                rate_type=obj.rate_type,
                quantity_purchased=float(obj.quantity_purchased),
                quantity_used=float(obj.quantity_used),
                remaining_stock=remaining_stock,
                total_amount=total_amount,
                payment_given=payment_given,
                payment_pending=payment_pending,
                extra_paid=extra_paid,
                minimum_stock_level=float(obj.minimum_stock_level or 0),
                alert_type=alert_type,
            )
        )

    return data

# ================= PURCHASE ORDERS =================

@router.post("/purchase-orders", response_model=PurchaseOrderOut, status_code=201)
async def create_po(
    payload: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db_session),
):
    if payload.quantity <= 0:
        raise HTTPException(400, "Quantity must be greater than 0")

    if payload.rate <= 0:
        raise HTTPException(400, "Rate must be greater than 0")

    # ===== MATERIAL VALIDATION =====
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

    return PurchaseOrderOut(
        id=po.id,
        supplier_id=po.supplier_id,
        project_id=po.project_id,
        material_id=po.material_id,  
        material_name=material.material_name.strip().title(),
        quantity=float(po.quantity),
        rate=float(po.rate),
        total_amount=float(po.total_amount),
        status=po.status,
    )

@router.get("/purchase-orders/{id}", response_model=PurchaseOrderOut)
async def get_po(
    id: int,
    db: AsyncSession = Depends(get_db_session),
):
    po = await db.get(PurchaseOrder, id)

    if not po or po.is_deleted:
        raise HTTPException(404, "PO not found")

    return PurchaseOrderOut(
        id=po.id,
        supplier_id=po.supplier_id,
        project_id=po.project_id,
        material_id=po.material_id,
        material_name=po.material_name.strip().title(),
        quantity=float(po.quantity),
        rate=float(po.rate),
        total_amount=float(po.total_amount),
        status=po.status,
    )

@router.get("/purchase-orders", response_model=List[PurchaseOrderOut])
async def list_po(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
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

    return [
        PurchaseOrderOut(
            id=r.id,
            supplier_id=r.supplier_id,
            project_id=r.project_id,
            material_id=r.material_id,
            material_name=r.material_name.strip().title(),
            quantity=float(r.quantity),
            rate=float(r.rate),
            total_amount=float(r.total_amount),
            status=r.status,
        )
        for r in rows
    ]

@router.put("/purchase-orders/{id}", response_model=PurchaseOrderOut)
async def update_po(
    id: int,
    payload: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(PurchaseOrder, id)

    if not obj:
        raise HTTPException(404, "PO not found")

    if payload.quantity <= 0 or payload.rate <= 0:
        raise HTTPException(400, "Invalid quantity or rate")

    material = await db.get(Material, payload.material_id)
    if not material:
        raise HTTPException(404, "Material not found")

    supplier = await db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    obj.supplier_id = payload.supplier_id
    obj.project_id = payload.project_id
    obj.material_name = material.material_name
    obj.quantity = payload.quantity
    obj.rate = payload.rate
    obj.total_amount = payload.quantity * payload.rate

    await db.commit()
    await db.refresh(obj)

    return PurchaseOrderOut.model_validate(obj)


@router.delete("/purchase-orders/{id}")
async def delete_po(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(PurchaseOrder, id)

    if not obj or obj.is_deleted:
        raise HTTPException(status_code=404, detail="PO not found")

    obj.is_deleted = True

    await db.commit()

    return {"message": "Deleted"}

# =========================project_transactions=============================

@router.get("/projects/{project_id}/transactions")
async def project_transactions(
    project_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)

    query = (
        select(MaterialTransaction, Material.material_name, Supplier.name)
        .join(Material, Material.id == MaterialTransaction.material_id)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(MaterialTransaction.project_id == project_id)
        .order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.all()

    data = []

    for tx, material_name, supplier_name in rows:

        if tx.type.value in ["USAGE", "TRANSFER_OUT"]:
            qty = -abs(tx.quantity)
        else:
            qty = abs(tx.quantity)

        data.append(
            {
                "id": tx.id,
                "type": tx.type.value,
                "material_id": tx.material_id, 
                "material_name": material_name,
                "supplier_name": supplier_name,
                "quantity": float(qty),
                "total_amount": float(tx.total_amount),
                "project_id": tx.project_id,
                "created_at": tx.created_at,
            }
        )

    return data

# ================= material_transactions =================

@router.get("/{material_id}/transactions", response_model=List[MaterialLogOut])
async def get_material_transactions(
    material_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(MaterialTransaction)
        .where(MaterialTransaction.material_id == material_id)
        .order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return [MaterialLogOut.model_validate(r) for r in result.scalars().all()]


# ================= TRANSFERS =================

@router.post("/transfers", response_model=TransferOut)
async def create_transfer(
    payload: TransferCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    from decimal import Decimal
    import uuid

    if payload.quantity <= 0:
        raise HTTPException(400, "Quantity must be > 0")

    if payload.from_project_id == payload.to_project_id:
        raise HTTPException(400, "Cannot transfer to same project")

    # ===== PROJECT VALIDATION =====
    from_project = await db.get(Project, payload.from_project_id)
    to_project = await db.get(Project, payload.to_project_id)

    if not from_project or not to_project:
        raise HTTPException(404, "Project not found")

    reference = f"TRF-{uuid.uuid4().hex[:8]}"

    # ===== SOURCE MATERIAL (LOCK) =====
    material = await db.scalar(
        select(Material)
        .where(Material.id == payload.material_id, Material.is_deleted == False)
        .with_for_update()
    )

    if not material:
        raise HTTPException(404, "Material not found")

    if material.project_id != payload.from_project_id:
        raise HTTPException(400, "Material does not belong to source project")

    if payload.quantity > material.remaining_stock:
        raise HTTPException(400, "Not enough stock")

    # ===== REDUCE SOURCE =====
    material.quantity_used += payload.quantity
    update_material_fields(material)

    # ===== DESTINATION MATERIAL (LOCK / FIND) =====
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
        # ===== UPDATE EXISTING =====
        existing_material.quantity_purchased += payload.quantity
        update_material_fields(existing_material)

    else:
        # ===== CREATE NEW MATERIAL (FIXED) =====
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

        existing_material.remaining_stock = payload.quantity
        existing_material.total_amount = Decimal("0")
        existing_material.payment_pending = Decimal("0")

        db.add(existing_material)
        await db.flush()

    # ===== CALC =====
    total = payload.quantity * material.purchase_rate

    # ===== TRANSACTIONS =====
    db.add(
        MaterialTransaction(
            material_id=material.id,
            type=DBTransactionType.TRANSFER_OUT,
            project_id=payload.from_project_id,
            remarks="Transfer out",
            quantity=payload.quantity,
            rate=material.purchase_rate,
            total_amount=total,
            amount_paid=0,
            payment_pending=0,
            issue_type=IssueType.TRANSFER,
            reference_id=reference,
        )
    )

    db.add(
        MaterialTransaction(
            material_id=existing_material.id,
            type=DBTransactionType.TRANSFER_IN,
            project_id=payload.to_project_id,
            remarks="Transfer in",
            quantity=payload.quantity,
            rate=material.purchase_rate,
            total_amount=total,   
            amount_paid=0,
            payment_pending=0,
            issue_type=IssueType.TRANSFER,
            reference_id=reference,
        )
    )

    # ===== LEDGER =====
    db.add(
        MaterialLedger(
            material_id=material.id,
            type=DBTransactionType.TRANSFER_OUT,
            project_id=payload.from_project_id,
            remarks="Transfer out",
            quantity=payload.quantity,
            rate=material.purchase_rate,
            total_amount=total,
            reference_id=reference,
        )
    )

    db.add(
        MaterialLedger(
            material_id=existing_material.id,
            type=DBTransactionType.TRANSFER_IN,
            project_id=payload.to_project_id,
            remarks="Transfer in",
            quantity=payload.quantity,
            rate=material.purchase_rate,
            total_amount=total,
            reference_id=reference,
        )
    )

    # ===== TRANSFER RECORD =====
    obj = MaterialTransfer(
        **payload.model_dump(),
        status="COMPLETED",
        reference_id=reference,
    )

    db.add(obj)

    # ===== COMMIT =====
    await db.commit()
    await db.refresh(obj)

    await bump_cache_version(redis, VERSION_KEY)

    # ====== RESPONSE =====
    return TransferOut(
        id=obj.id,
        material=TransferMaterial(id=material.id, name=material.material_name),
        from_project=TransferProject(
            id=from_project.id, name=from_project.project_name
        ),
        to_project=TransferProject(
            id=to_project.id, name=to_project.project_name
        ),
        quantity=obj.quantity,
        status=obj.status,
        created_at=obj.created_at,
    )


# ================= LIST TRANSFERS =================

@router.get("/transfers")
async def list_transfers(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    # ===== VALIDATION =====
    if skip < 0 or limit <= 0:
        raise HTTPException(400, "Invalid pagination values")

    # ===== ALIASES =====
    FromProject = aliased(Project)
    ToProject = aliased(Project)

    # ===== TOTAL COUNT =====
    total = await db.scalar(select(func.count()).select_from(MaterialTransfer))

    # ===== QUERY =====
    result = await db.execute(
        select(
            MaterialTransfer,
            Material.material_name,
            FromProject.project_name.label("from_project_name"),
            ToProject.project_name.label("to_project_name"),
        )
        .join(Material, Material.id == MaterialTransfer.material_id)
        .join(FromProject, FromProject.id == MaterialTransfer.from_project_id)
        .join(ToProject, ToProject.id == MaterialTransfer.to_project_id)
        .order_by(MaterialTransfer.id.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.all()

    # ===== RESPONSE FORMAT =====
    data = []
    for t, material_name, from_name, to_name in rows:
        data.append(
            {
                "id": t.id,
                "material": {
                    "id": t.material_id,
                    "name": material_name,
                },
                "from_project": {
                    "id": t.from_project_id,
                    "name": from_name,
                },
                "to_project": {
                    "id": t.to_project_id,
                    "name": to_name,
                },
                "quantity": str(t.quantity),  # Decimal safe
                "status": t.status,  # no hardcode
            }
        )

    return {
        "total": total or 0,
        "skip": skip,
        "limit": limit,
        "data": data,
    }


# ================= GET SINGLE TRANSFER =================
@router.get("/transfers/{id}", response_model=TransferOut)
async def get_transfer(id: int, db: AsyncSession = Depends(get_db_session)):

    obj = await db.get(MaterialTransfer, id)

    if not obj:
        raise HTTPException(404, "Transfer not found")

    # ================= FETCH RELATED DATA =================
    material = await db.get(Material, obj.material_id)
    from_project = await db.get(Project, obj.from_project_id)
    to_project = await db.get(Project, obj.to_project_id)

    # ================= BUILD RESPONSE =================
    response = {
        "id": obj.id,
        "material": (
            {
                "id": material.id,
                "name": material.material_name,
            }
            if material
            else None
        ),
        "from_project": (
            {
                "id": from_project.id,
                "name": from_project.project_name,
            }
            if from_project
            else None
        ),
        "to_project": (
            {
                "id": to_project.id,
                "name": to_project.project_name,
            }
            if to_project
            else None
        ),
        "quantity": obj.quantity,
        "status": obj.status,
        "created_at": getattr(obj, "created_at", None),
    }

    return response

VALID_STATUS = {"PENDING", "COMPLETED", "CANCELLED"}


@router.put("/transfers/{id}", response_model=TransferOut)
async def update_transfer_status(
    id: int,
    status: str,
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
        material=TransferMaterial(id=material.id, name=material.material_name),
        from_project=TransferProject(
            id=from_project.id, name=from_project.project_name
        ),
        to_project=TransferProject(id=to_project.id, name=to_project.project_name),
        quantity=obj.quantity,
        status=obj.status,
        created_at=obj.created_at,
    )


# ================= USAGE =================

@router.post("/{material_id}/usage", response_model=MaterialOut)
async def usage(
    material_id: int,
    data: UsageMaterial,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    import uuid
    from decimal import Decimal

    async with db.begin():

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

        purchased = obj.quantity_purchased or Decimal("0")
        used = obj.quantity_used or Decimal("0")
        remaining_stock = purchased - used

        if remaining_stock <= 0:
            raise HTTPException(400, "Stock exhausted")

        if qty > remaining_stock:
            raise HTTPException(400, "Not enough stock")

        total_amount_current = obj.total_amount or Decimal("0")

        # ===== WAC (Weighted Avg Cost)
        avg_rate = total_amount_current / remaining_stock
        used_value = qty * avg_rate

        reference = f"USE-{uuid.uuid4().hex[:8]}"

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
                issue_type=data.issue_type,
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
                issue_type=data.issue_type,
                project_id=data.project_id,
                remarks="Material used",
                reference_id=reference,
            )
        )

        # ====== UPDATE MATERIAL ======
        obj.quantity_used = used + qty
        obj.total_amount = max(Decimal("0"), total_amount_current - used_value)

        update_material_fields(obj)

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier

    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    # ===== ALERT TYPE =====
    if obj.remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    # ===== FINAL RESPONSE (SAFE) =====
    response = MaterialOut.model_validate({
        **obj.__dict__,
        "alert_type": alert_type
    })

    response.material_name = obj.material_name.title()
    response.supplier_name = supplier.name if supplier else None

    return response

# ================= PURCHASE =================

@router.post("/{material_id}/purchase", response_model=MaterialOut)
async def purchase(
    material_id: int,
    data: PurchaseMaterial,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    async with db.begin():

        obj = await db.scalar(
            select(Material)
            .options(selectinload(Material.supplier))
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not obj:
            raise HTTPException(404, "Material not found")

        qty = Decimal(data.quantity)
        paid = Decimal(data.amount_paid or 0)

        if qty <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        total = qty * obj.purchase_rate

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
                payment_pending=total - paid,
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
                payment_pending=total - paid,
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

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier

    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(0, total_amount - payment_given)
    extra_paid = max(0, payment_given - total_amount)

    if obj.remaining_stock <= 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,   
        project_id=obj.project_id,
        material_name=obj.material_name,
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

# ================= ADD INVENTORY =================


@router.post("/inventory")
async def adjust_inventory(
    payload: InventoryAdjustRequest,
    db: AsyncSession = Depends(get_db_session),
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

    async with db.begin():

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

        if diff > 0:
            material.quantity_purchased += diff
            material.total_amount += diff * avg_rate
        else:
            material.quantity_used += abs(diff)
            material.total_amount -= abs(diff) * avg_rate

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

    await bump_cache_version(redis, VERSION_KEY)

    return {
        "material_id": material_id,
        "old_stock": float(old_stock),
        "new_stock": float(new_stock),
        "difference": float(diff),
        "reason": reason,
        "reference_id": reference,
    }


@router.get("/inventory")
async def get_all_inventory(db: AsyncSession = Depends(get_db_session)):
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
        qty_purchased = float(r.quantity_purchased or 0)
        remaining = float(r.remaining_stock or 0)
        total_amount = float(r.total_amount or 0)

        avg_rate = total_amount / qty_purchased if qty_purchased > 0 else 0
        total_value = remaining * avg_rate

        data.append(
            {
                "material_id": r.id,
                "material_name": r.material_name,
                "remaining_stock": remaining,
                "unit": r.unit,
                # ❗ change here
                "avg_rate": round(avg_rate, 2),
                "total_value": round(total_value, 2),
                "project_id": r.project_id,
            }
        )

    return data

@router.get("/inventory/valuation")
async def get_inventory_valuation(
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(select(Material).where(Material.is_deleted == False))

    materials = result.scalars().all()

    total_value = Decimal("0")

    for m in materials:
        purchase_rate = m.purchase_rate or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")

        total_value += remaining * purchase_rate

    return {"total_value": total_value}


@router.get("/inventory/{project_id}")
async def get_project_inventory(
    project_id: int,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(
            Material.id,
            Material.material_name,
            Material.remaining_stock,
            Material.total_amount,
        )
        .where(Material.project_id == project_id, Material.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )

    rows = result.all()

    data = []

    for r in rows:
        remaining = float(r.remaining_stock or 0)
        total_amt = float(r.total_amount or 0)

        avg_rate = total_amt / remaining if remaining > 0 else 0

        data.append(
            {
                "material_id": r.id,
                "material_name": r.material_name,
                "remaining_stock": remaining,
                "avg_rate": round(avg_rate, 2),
                "total_value": round(total_amt, 2),  # already correct
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
        quantity = float(r.quantity or 0)
        total_amount = float(r.total_amount or 0)
        rate = float(r.rate or 0)

        if quantity != 0:
            avg_rate = abs(total_amount / quantity)
        else:
            avg_rate = 0.0

        logs.append(
            MaterialLogOut(
                id=r.id,
                material_id=r.material_id,
                type=r.type,
                quantity=round(quantity, 3),
                rate=round(rate, 2),
                avg_rate=round(avg_rate, 2),
                total_amount=round(total_amount, 2),
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
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)

    query = select(Material).where(Material.is_deleted == False)

    if project_id is not None:
        query = query.where(Material.project_id == project_id)

    if category is not None:
        query = query.where(Material.category == category)

    query = query.offset(skip).limit(limit)

    rows = (await db.execute(query)).scalars().all()

    data = []

    for r in rows:
        purchased = float(r.quantity_purchased or 0)
        used = float(r.quantity_used or 0)
        remaining = float(r.remaining_stock or 0)
        total_amt = float(r.total_amount or 0)

        # SAFE avg rate (no drift, no negative issues)
        if purchased > 0:
            avg_rate = total_amt / purchased
        else:
            avg_rate = 0

        # recalculated inventory value
        total_cost = remaining * avg_rate

        data.append(
            MaterialReport(
                material_id=r.id,
                material_name=r.material_name,
                total_purchased=purchased,
                total_used=used,
                remaining_stock=remaining,
                total_cost=round(total_cost, 2),
                payment_pending=float(r.payment_pending or 0),
            )
        )

    return data


@router.get("/reports/materials/pdf", response_class=FileResponse)
async def export_pdf(db: AsyncSession = Depends(get_db_session)):

    rows = (
        await db.execute(
            select(
                Material.id,
                Material.material_name,
                Material.remaining_stock,
                Material.total_amount,
            ).where(Material.is_deleted == False)
        )
    ).all()

    import tempfile

    file_path = os.path.join(tempfile.gettempdir(), f"material_{uuid.uuid4()}.pdf")

    doc = SimpleDocTemplate(file_path)
    data = [["ID", "Material", "Stock", "Cost"]]

    for r in rows:
        data.append(
            [r.id, r.material_name, str(r.remaining_stock), str(r.total_amount)]
        )

    doc.build([Table(data)])

    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename="material_report.pdf",
        background=BackgroundTask(safe_delete, file_path),
    )


@router.get("/reports/materials/excel", response_class=FileResponse)
async def export_excel(db: AsyncSession = Depends(get_db_session)):

    rows = (
        await db.execute(
            select(
                Material.id,
                Material.material_name,
                Material.remaining_stock,
                Material.total_amount,
            ).where(Material.is_deleted == False)
        )
    ).all()

    file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name

    wb = Workbook()
    ws = wb.active
    ws.append(["ID", "Material", "Stock", "Cost"])

    for r in rows:
        ws.append([r.id, r.material_name, str(r.remaining_stock), str(r.total_amount)])

    wb.save(file_path)

    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="material_report.xlsx",
        background=BackgroundTask(safe_delete, file_path),
    )


# ===============price-history==========================


@router.get("/price-history/{material_id}")
async def price_history(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(MaterialTransaction.rate, MaterialTransaction.created_at)
        .where(
            MaterialTransaction.material_id == material_id,
            MaterialTransaction.type == TransactionType.PURCHASE,
        )
        .order_by(MaterialTransaction.created_at.asc())
    )

    rows = result.fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="No price history found")

    history = []
    last_rate = None

    for row in rows:
        rate = float(row.rate)

        # only push when rate changes
        if last_rate != rate:
            history.append(
                {
                    "rate": rate,
                    "date": row.created_at.isoformat(),
                }
            )
            last_rate = rate

    return history

# ================= MATERIALS - DYNAMIC ROUTES =================

@router.post("", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    data = payload.model_dump()

    # NORMALIZE NAME
    name = payload.material_name.strip().lower()
    data["material_name"] = name

    # VALIDATE SUPPLIER
    supplier = await db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    existing = await db.scalar(
        select(Material).where(
            Material.project_id == payload.project_id,
            Material.supplier_id == payload.supplier_id,
            func.lower(Material.material_name) == name,
            Material.is_deleted == False,
        )
    )
    if existing:
        raise HTTPException(400, "Material already exists for this project & supplier")

    # GENERATE MATERIAL CODE (MAT001 format)
    material_code = await generate_business_id(
        db=db,
        model=Material,
        column_name="material_code",
        prefix="MAT",
    )

    # CREATE MATERIAL
    obj = Material(
        **data,
        material_code=material_code,
    )

    # DEFAULT VALUES
    obj.quantity_purchased = obj.quantity_purchased or Decimal("0")
    obj.quantity_used = Decimal("0")
    obj.payment_given = obj.payment_given or Decimal("0")

    try:
        db.add(obj)
        await db.flush()
    except IntegrityError:
        raise HTTPException(400, "Material already exists")

    # CALCULATIONS
    total_amount = obj.quantity_purchased * obj.purchase_rate
    obj.total_amount = total_amount
    obj.payment_pending = total_amount - obj.payment_given
    obj.remaining_stock = obj.quantity_purchased - obj.quantity_used

    # ALERT TYPE
    if obj.remaining_stock == 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    # REFERENCE
    reference = f"INIT-{uuid.uuid4().hex[:8]}"

    # TRANSACTION + LEDGER
    if obj.quantity_purchased > 0 or obj.payment_given > 0:

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

    await db.commit()
    await db.refresh(obj)

    await bump_cache_version(redis, VERSION_KEY)

    # FIX: include alert_type during validation
    response = MaterialOut.model_validate({
        **obj.__dict__,
        "alert_type": alert_type
    })

    response.supplier_name = supplier.name
    response.material_name = obj.material_name.title()

    return response


@router.get("", response_model=list[MaterialOut])
async def list_materials(
    project_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
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


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
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


@router.put("/{material_id}", response_model=MaterialOut)
async def update_material(
    material_id: int,
    payload: MaterialUpdate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    async with db.begin():

        obj = await db.scalar(
            select(Material)
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not obj:
            raise HTTPException(status_code=404, detail="Material not found")

        update_data = payload.model_dump(exclude_unset=True)

        # direct payment block
        if "payment_given" in update_data:
            raise HTTPException(
                status_code=400,
                detail="Direct payment update not allowed. Use purchase API",
            )

        # apply updates
        for k, v in update_data.items():
            setattr(obj, k, v)

        # recalc fields
        update_material_fields(obj)

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = await db.get(Supplier, obj.supplier_id)

    total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

    # ALERT LOGIC (fix)
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

@router.delete("/{material_id}")
async def delete_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
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
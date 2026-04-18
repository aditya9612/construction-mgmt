from datetime import date
from typing import Optional, List
from decimal import Decimal
import uuid
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from reportlab.platypus import SimpleDocTemplate, Table
from openpyxl import Workbook
from app.schemas.material import TransferMaterial, TransferProject

from app.cache.redis import bump_cache_version
from app.core.dependencies import get_request_redis
from app.db.session import get_db_session
from app.schemas.material import MaterialReport
from app.schemas.material import PriceHistoryOut
import tempfile
from app.core.enums import IssueType, TransactionType

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
    purchase_rate = obj.purchase_rate or Decimal("0")
    qty_purchased = obj.quantity_purchased or Decimal("0")
    qty_used = obj.quantity_used or Decimal("0")
    payment_given = obj.payment_given or Decimal("0")

    # ✅ ONLY place where total is calculated
    obj.total_amount = purchase_rate * qty_purchased

    # ✅ allow advance (no restriction)
    obj.remaining_stock = max(qty_purchased - qty_used, Decimal("0"))

    obj.payment_pending = max(obj.total_amount - payment_given, Decimal("0"))

    # ✅ track advance separately
    obj.advance_amount = max(payment_given - obj.total_amount, Decimal("0"))


def to_transaction_type(value):
    if isinstance(value, DBTransactionType):
        return value

    if isinstance(value, str):
        try:
            return DBTransactionType(value.upper())  # 🔥 FIX
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


@router.post("/suppliers", response_model=SupplierOut)
async def create_supplier(
    payload: SupplierCreate,
    db: AsyncSession = Depends(get_db_session),
):
    async with db.begin():
        obj = Supplier(**payload.model_dump())
        db.add(obj)

    return SupplierOut.model_validate(obj)


from sqlalchemy.orm import selectinload


@router.get("/suppliers/{supplier_id}/materials", response_model=List[MaterialOut])
async def get_supplier_materials(
    supplier_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(Material)
        .options(selectinload(Material.supplier))  # ⚡ avoid N+1 query
        .where(Material.supplier_id == supplier_id, Material.is_deleted == False)
    )

    materials = result.scalars().all()

    response = []

    for m in materials:
        item = MaterialOut.model_validate(
            m
        )  # ✅ auto mapping (all fields incl. minimum_stock_level)
        item.supplier_name = m.supplier.name if m.supplier else None
        response.append(item)

    return response


@router.put("/suppliers/{id}", response_model=SupplierOut)
async def update_supplier(
    id: int,
    payload: SupplierCreate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(
        select(Supplier).where(Supplier.id == id, Supplier.is_deleted == False)
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Supplier not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()  # ✅ important
    await db.refresh(obj)  # ✅ latest data

    return SupplierOut.model_validate(obj)


@router.delete("/suppliers/{id}")
async def delete_supplier(id: int, db: AsyncSession = Depends(get_db_session)):

    obj = await db.scalar(
        select(Supplier).where(Supplier.id == id, Supplier.is_deleted == False)
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Supplier not found")

    async with db.begin():
        obj.is_deleted = True

    return {"message": "Deleted"}


# ================= material_alerts =================


@router.get("/alerts")
async def material_alerts(
    threshold: Decimal | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    condition = (
        Material.remaining_stock < threshold
        if threshold is not None
        else Material.remaining_stock < Material.minimum_stock_level
    )

    result = await db.execute(
        select(Material).where(Material.is_deleted == False, condition)
    )

    materials = result.scalars().all()

    return [
        {
            "material_id": m.id,
            "material_name": m.material_name,
            "remaining_stock": float(m.remaining_stock or 0),
            "minimum_stock_level": float(m.minimum_stock_level or 0),
            "message": f"{m.material_name} is low on stock",
        }
        for m in materials
    ]


# ================= PURCHASE ORDERS =================
@router.post("/purchase-orders", response_model=PurchaseOrderOut)
async def create_po(
    payload: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db_session),
):
    if payload.quantity <= 0 or payload.rate <= 0:
        raise HTTPException(400, "Invalid quantity or rate")

    total = payload.quantity * payload.rate

    async with db.begin():

        # ✅ fetch material (source of truth)
        material = await db.get(Material, payload.material_id)
        if not material:
            raise HTTPException(404, "Material not found")

        # ✅ fetch supplier
        supplier = await db.get(Supplier, payload.supplier_id)
        if not supplier:
            raise HTTPException(404, "Supplier not found")

        # ✅ create PO with controlled data
        obj = PurchaseOrder(
            supplier_id=payload.supplier_id,
            project_id=payload.project_id,
            material_name=material.material_name,  # 🔥 FIX
            quantity=payload.quantity,
            rate=payload.rate,
            total_amount=total,
        )

        db.add(obj)

    return PurchaseOrderOut.model_validate(obj)


@router.get("/purchase-orders", response_model=List[PurchaseOrderOut])
async def list_po(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    rows = (
        (
            await db.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.is_deleted == False)  # ✅ fix
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [PurchaseOrderOut.model_validate(r) for r in rows]


@router.get("/purchase-orders/{id}", response_model=PurchaseOrderOut)
async def get_po(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(PurchaseOrder, id)

    if not obj:
        raise HTTPException(status_code=404, detail="PO not found")

    return PurchaseOrderOut.model_validate(obj)


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

    # ✅ fetch material
    material = await db.get(Material, payload.material_id)
    if not material:
        raise HTTPException(404, "Material not found")

    # ✅ fetch supplier
    supplier = await db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    # ✅ update fields manually (NO setattr loop)
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

    async with db.begin():
        obj.is_deleted = True  # ✅ soft delete

    return {"message": "Deleted"}


# =========================project_transactions=============================


@router.get("/projects/{project_id}/transactions")
async def project_transactions(
    project_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)  # safety

    result = await db.execute(
        select(MaterialTransaction)
        .where(MaterialTransaction.project_id == project_id)
        .order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "type": r.type.value,
            "quantity": float(r.quantity),
            "total_amount": float(r.total_amount),
            "project_id": r.project_id,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ======================material_transactions================================


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

    async with db.begin():  # ✅ transaction safety

        # ===== PROJECT VALIDATION =====
        from_project = await db.get(Project, payload.from_project_id)
        to_project = await db.get(Project, payload.to_project_id)

        if not from_project or not to_project:
            raise HTTPException(404, "Project not found")

        reference = f"TRF-{uuid.uuid4().hex[:8]}"

        # ===== SOURCE MATERIAL =====
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

        # ===== DESTINATION MATERIAL =====
        existing_material = await db.scalar(
            select(Material)
            .where(
                Material.project_id == payload.to_project_id,
                Material.material_name == material.material_name,
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        if existing_material:
            existing_material.quantity_purchased += payload.quantity

            old_payment = existing_material.payment_given

            update_material_fields(existing_material)

            # 🔥 prevent financial impact
            existing_material.payment_given = old_payment
            existing_material.total_amount = (
                existing_material.purchase_rate * existing_material.quantity_purchased
            )
            existing_material.payment_pending = max(
                existing_material.total_amount - existing_material.payment_given,
                Decimal("0"),
            )

        else:
            existing_material = Material(
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
                minimum_stock_level=material.minimum_stock_level,  # ✅ important
            )

            existing_material.total_amount = Decimal("0")
            existing_material.payment_pending = Decimal("0")
            existing_material.remaining_stock = payload.quantity

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
                total_amount=Decimal("0"),
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
                total_amount=Decimal("0"),
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

    # ===== AFTER COMMIT =====
    await db.refresh(obj)  # ✅ FIXED (no relation names)

    await bump_cache_version(redis, VERSION_KEY)

    # ===== RESPONSE =====
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
        "status": obj.status,  # ✅ FIX
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

    # 🔥 FIX: manually fetch relations
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


from sqlalchemy.orm import selectinload
from decimal import Decimal
import uuid


@router.post("/{material_id}/usage", response_model=MaterialOut)
async def usage(
    material_id: int,
    data: UsageMaterial,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    async with db.begin():

        obj = await db.scalar(
            select(Material)
            .options(selectinload(Material.supplier))  # ✅ FIX
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not obj:
            raise HTTPException(404, "Material not found")

        qty = Decimal(data.quantity)

        if qty <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        if qty > obj.remaining_stock:
            raise HTTPException(400, "Not enough stock")

        reference = f"USE-{uuid.uuid4().hex[:8]}"
        total = qty * obj.purchase_rate

        # ===== TRANSACTION =====
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.USAGE,
                quantity=qty,
                rate=obj.purchase_rate,
                total_amount=total,
                amount_paid=0,
                payment_pending=0,
                issue_type=IssueType.SITE,
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
                rate=obj.purchase_rate,
                total_amount=total,
                amount_paid=0,
                payment_pending=0,
                issue_type=IssueType.SITE,
                project_id=data.project_id,
                remarks="Material used",
                reference_id=reference,
            )
        )

        # ===== UPDATE STOCK =====
        obj.quantity_used = (obj.quantity_used or Decimal("0")) + qty
        update_material_fields(obj)

    await bump_cache_version(redis, VERSION_KEY)

    await db.refresh(obj)

    # ✅ response fix
    response = MaterialOut.model_validate(obj)
    response.supplier_name = obj.supplier.name if obj.supplier else None

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
            .options(selectinload(Material.supplier))  # ✅ FIX
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not obj:
            raise HTTPException(404, "Material not found")

        project = await db.get(Project, data.project_id)
        if not project:
            raise HTTPException(404, "Project not found")

        if obj.project_id != data.project_id:
            raise HTTPException(400, "Material does not belong to this project")

        qty = Decimal(data.quantity)
        paid = Decimal(data.amount_paid)

        if qty <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        txn_total = qty * obj.purchase_rate
        pending = max(txn_total - paid, Decimal("0"))

        reference = f"PUR-{uuid.uuid4().hex[:8]}"

        # ================= UPDATE MATERIAL =================
        obj.quantity_purchased = (obj.quantity_purchased or Decimal("0")) + qty
        obj.payment_given = (obj.payment_given or Decimal("0")) + paid

        update_material_fields(obj)

        # ================= TRANSACTION =================
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=qty,
                rate=obj.purchase_rate,
                total_amount=txn_total,
                amount_paid=paid,
                payment_pending=pending,
                issue_type=(
                    to_issue_type(data.issue_type)
                    if data.issue_type
                    else IssueType.VENDOR
                ),
                project_id=data.project_id,
                remarks="Material purchase",
                reference_id=reference,
            )
        )

        # ================= LEDGER =================
        db.add(
            MaterialLedger(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=qty,
                rate=obj.purchase_rate,
                total_amount=txn_total,
                amount_paid=paid,
                payment_pending=pending,
                project_id=data.project_id,
                remarks="Material purchase",
                reference_id=reference,
            )
        )

    await bump_cache_version(redis, VERSION_KEY)

    await db.refresh(obj)

    response = MaterialOut.model_validate(obj)
    response.supplier_name = obj.supplier.name if obj.supplier else None

    return response


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
                "old_stock": old_stock,
                "new_stock": old_stock,
                "difference": 0,
                "reason": reason,
            }

        reference = f"ADJ-{uuid.uuid4().hex[:8]}"
        rate = material.purchase_rate or Decimal("0")
        total_amount = abs(diff) * rate

        # 🔥 FIX: adjust via purchased/used
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
                rate=rate,
                total_amount=total_amount,
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
                rate=rate,
                total_amount=total_amount,
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
        "old_stock": old_stock,
        "new_stock": new_stock,
        "difference": diff,
        "reason": reason,
        "reference_id": reference,
    }


@router.get("/inventory")
async def get_all_inventory(db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(select(Material).where(Material.is_deleted == False))

    materials = result.scalars().all()

    return [
        {
            "material_id": m.id,
            "material_name": m.material_name,
            "remaining_stock": float(m.remaining_stock or 0),
            "project_id": m.project_id,
            "unit": m.unit,
        }
        for m in materials
    ]


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
        select(Material)
        .where(Material.project_id == project_id)
        .where(Material.is_deleted == False)
        .offset(skip)  # ✅ added
        .limit(limit)  # ✅ added
    )

    materials = result.scalars().all()

    return [
        {
            "material_id": m.id,
            "material_name": m.material_name,
            "remaining_stock": float(m.remaining_stock or 0),
        }
        for m in materials
    ]


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
    # ✅ limit protection
    limit = min(max(limit, 1), 100)

    query = select(MaterialTransaction)

    # ✅ filters
    if material_id is not None:
        query = query.where(MaterialTransaction.material_id == material_id)

    if project_id is not None:
        query = query.where(MaterialTransaction.project_id == project_id)

    # ✅ enum filter (clean & safe)
    if type is not None:
        query = query.where(MaterialTransaction.type == type.value)

    # ✅ sorting + pagination
    query = (
        query.order_by(MaterialTransaction.created_at.desc())
        .offset(offset)
        .limit(limit)
    )

    result = await db.execute(query)
    rows = result.scalars().all()

    return [MaterialLogOut.model_validate(r) for r in rows]


# ================= REPORTS =================


@router.get("/reports", response_model=List[MaterialReport])
async def material_report(
    project_id: Optional[int] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    query = select(Material).where(Material.is_deleted == False)

    if project_id:
        query = query.where(Material.project_id == project_id)

    if category:
        query = query.where(Material.category == category)

    query = query.offset(skip).limit(limit)  # ✅ added

    rows = (await db.execute(query)).scalars().all()

    return [
        MaterialReport(
            material_id=r.id,
            material_name=r.material_name,
            total_purchased=r.quantity_purchased or Decimal("0"),
            total_used=r.quantity_used or Decimal("0"),
            remaining_stock=r.remaining_stock or Decimal("0"),
            total_cost=r.total_amount or Decimal("0"),
            payment_pending=r.payment_pending or Decimal("0"),
        )
        for r in rows
    ]


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
async def price_history(material_id: int, db: AsyncSession = Depends(get_db_session)):
    result = await db.execute(
        select(MaterialTransaction.rate, MaterialTransaction.created_at)
        .where(
            MaterialTransaction.material_id == material_id,
            MaterialTransaction.type == TransactionType.PURCHASE,  # ✅ FINAL FIX
        )
        .order_by(MaterialTransaction.created_at.asc())
    )

    data = result.fetchall()

    if not data:
        raise HTTPException(status_code=404, detail="No price history found")

    return [
        {"rate": float(row.rate), "date": row.created_at.isoformat()} for row in data
    ]


# ================= MATERIALS - DYNAMIC ROUTES =================


@router.post("", response_model=MaterialOut)
async def create_material(
    payload: MaterialCreate,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    data = payload.model_dump()

    # VALIDATE SUPPLIER
    supplier = await db.get(Supplier, payload.supplier_id)
    if not supplier:
        raise HTTPException(404, "Supplier not found")

    # CREATE MATERIAL
    obj = Material(**data, quantity_used=Decimal("0"))

    obj.quantity_purchased = obj.quantity_purchased or Decimal("0")
    obj.payment_given = obj.payment_given or Decimal("0")

    update_material_fields(obj)

    db.add(obj)
    await db.flush()

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
    await db.refresh(obj, ["supplier"])

    await bump_cache_version(redis, VERSION_KEY)

    response = MaterialOut.model_validate(obj)
    response.supplier_name = supplier.name

    return response


@router.get("", response_model=list[MaterialOut])
async def list_materials(
    project_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
):
    query = select(Material).where(Material.is_deleted == False)

    if project_id:
        query = query.where(Material.project_id == project_id)

    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    materials = result.scalars().all()

    return [
        MaterialOut(
            id=obj.id,
            project_id=obj.project_id,
            material_name=obj.material_name,
            category=obj.category,
            unit=obj.unit,
            supplier_id=obj.supplier_id,
            supplier_name=obj.supplier.name if obj.supplier else None,
            purchase_rate=obj.purchase_rate,
            rate_type=obj.rate_type,
            quantity_purchased=obj.quantity_purchased,
            quantity_used=obj.quantity_used,
            remaining_stock=obj.remaining_stock,
            total_amount=obj.total_amount,
            payment_given=obj.payment_given,
            payment_pending=obj.payment_pending,
            minimum_stock_level=obj.minimum_stock_level or Decimal("0.000"),
        )
        for obj in materials
    ]


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(
        select(Material).where(Material.id == material_id, Material.is_deleted == False)
    )

    if not obj:
        raise HTTPException(status_code=404, detail="Material not found")

    return MaterialOut(
        id=obj.id,
        project_id=obj.project_id,
        material_name=obj.material_name,
        category=obj.category,
        unit=obj.unit,
        supplier_id=obj.supplier_id,
        supplier_name=obj.supplier.name if obj.supplier else None,
        purchase_rate=obj.purchase_rate,
        rate_type=obj.rate_type,
        quantity_purchased=obj.quantity_purchased,
        quantity_used=obj.quantity_used,
        remaining_stock=obj.remaining_stock,
        total_amount=obj.total_amount,
        payment_given=obj.payment_given,
        payment_pending=obj.payment_pending,
        minimum_stock_level=obj.minimum_stock_level or Decimal("0.000"),
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
            raise HTTPException(404, "Material not found")

        update_data = payload.model_dump(exclude_unset=True)

        # ❌ Prevent direct payment manipulation
        if "payment_given" in update_data:
            raise HTTPException(
                status_code=400,
                detail="Direct payment update not allowed. Use purchase API.",
            )

        # ✅ normal field updates
        for k, v in update_data.items():
            setattr(obj, k, v)

        # ✅ recalculate everything
        update_material_fields(obj)

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    return MaterialOut.model_validate(obj)


@router.delete("/{material_id}")
async def delete_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.get(Material, material_id)

    if not obj:
        raise HTTPException(status_code=404, detail="Material not found")

    async with db.begin():
        obj.is_deleted = True

    await bump_cache_version(redis, VERSION_KEY)

    return {"message": "Deleted"}

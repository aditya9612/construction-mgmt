from datetime import date
from typing import Optional, List
from decimal import Decimal
import uuid
import os
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.core.enums import IssueType, TransactionType, TransferStatus
from app.utils.common import generate_business_id
from sqlalchemy.orm import selectinload
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
import os
from app.models.material import (
    Material,
    MaterialLedger,
    MaterialTransaction,
    Supplier,
    PurchaseOrder,
    MaterialTransfer,
)
from reportlab.platypus import Image
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics import renderPM
from reportlab.lib.units import inch
from starlette.background import BackgroundTask
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from app.models.material import MaterialUsage
from app.schemas.material import InventoryAdjustRequest
from app.models.project import Project
from sqlalchemy.orm import aliased
import re
from sqlalchemy import case
from decimal import Decimal
import tempfile, os, uuid
from datetime import datetime

from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    Image,
)
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle

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

MATERIAL_READ_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
        UserRole.ACCOUNTANT,
        UserRole.CLIENT,
    ]
]

MATERIAL_WRITE_ROLES = [
    r.value
    for r in [
        UserRole.ADMIN,
        UserRole.PROJECT_MANAGER,
        UserRole.SITE_ENGINEER,
    ]
]

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
            id=material.id, name=(material.material_name or "").title()
        ),
        from_project=TransferProject(
            id=from_project.id, name=from_project.project_name
        ),
        to_project=TransferProject(id=to_project.id, name=to_project.project_name),
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


def calculate_payment(total, paid):
    return (
        float(total),
        float(paid),
        float(max(total - paid, 0)),
        float(max(paid - total, 0)),
    )


def calculate_avg_rate(material):
    qty = material.quantity_purchased or Decimal("0")
    total = material.total_amount or Decimal("0")

    return total / qty if qty > 0 else Decimal("0")


def calculate_wac(material):
    qty = material.quantity_purchased or Decimal("0")
    total = material.total_amount or Decimal("0")

    return total / qty if qty > 0 else Decimal("0")


def generate_chart(rows, path):
    import matplotlib.pyplot as plt

    names = []
    stock = []

    for m, _ in rows[:10]:
        names.append((m.material_name or "")[:8])
        stock.append(float(m.remaining_stock or 0))

    plt.figure(figsize=(6, 3))
    plt.bar(names, stock)
    plt.xticks(rotation=30)
    plt.tight_layout()
    plt.savefig(path)
    plt.close()


# ================= SUMMARY =================
@router.get("/summary", response_model=SummaryOut)
async def material_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):

    # ✅ total materials (same)
    total_materials = await db.scalar(
        select(func.count(Material.id)).where(Material.is_deleted == False)
    )

    # 🔥 FIX: use WAC instead of purchase_rate
    rows = (
        (await db.execute(select(Material).where(Material.is_deleted == False)))
        .scalars()
        .all()
    )

    total_stock = Decimal("0")

    for m in rows:
        purchased = m.quantity_purchased or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")

        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")

        total_stock += remaining * avg_rate

    # ✅ pending (same)
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
async def get_supplier(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):

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
    import re

    # 🔹 Name validation
    supplier_name = payload.supplier_name.strip().title()
    if len(supplier_name) < 3:
        raise HTTPException(400, "Supplier name must be at least 3 characters")

    # 🔹 Contact person validation
    contact_person = (
        payload.contact_person.strip().title() if payload.contact_person else None
    )
    if contact_person and not re.fullmatch(r"[A-Za-z ]{3,}", contact_person):
        raise HTTPException(400, "Invalid contact person name")

    # 🔹 Phone / Email validation (PERFECT)
    phone_email = payload.phone_email.strip() if payload.phone_email else None

    if phone_email:
        cleaned = re.sub(r"[^\d]", "", phone_email)

        # handle +91 / 91
        if cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = cleaned[2:]

        if cleaned.isdigit():
            if not re.fullmatch(r"[6-9]\d{9}", cleaned):
                raise HTTPException(400, "Invalid Indian mobile number")
            phone_email = cleaned  # store clean number
        elif "@" in phone_email:
            if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", phone_email):
                raise HTTPException(400, "Invalid email format")
        else:
            raise HTTPException(400, "Enter valid phone number or email")

    # 🔹 GST validation
    gst_number = payload.gst_number.strip().upper() if payload.gst_number else None
    if gst_number and not re.fullmatch(
        r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]", gst_number
    ):
        raise HTTPException(400, "Invalid GST number format")

    # 🔹 Address validation
    address = payload.address.strip() if payload.address else None
    if address and len(address) < 3:
        raise HTTPException(400, "Address too short")

    # 🔹 Duplicate check
    existing = await db.scalar(
        select(Supplier).where(
            Supplier.is_deleted == False,
            (
                (Supplier.gst_number == gst_number if gst_number else False)
                | (Supplier.phone_email == phone_email if phone_email else False)
            ),
        )
    )

    if existing:
        raise HTTPException(400, "Supplier with same GST or phone/email already exists")

    # 🔹 Create supplier
    supplier = Supplier(
        supplier_name=supplier_name,
        contact_person=contact_person,
        phone_email=phone_email,
        gst_number=gst_number,
        address=address,
    )

    try:
        db.add(supplier)
        await db.commit()
        await db.refresh(supplier)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Duplicate supplier")

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

    new_name = payload.supplier_name.strip().title()
    new_contact_person = (
        payload.contact_person.strip() if payload.contact_person else None
    )
    new_phone_email = payload.phone_email.strip() if payload.phone_email else None
    new_gst = payload.gst_number.strip().upper() if payload.gst_number else None
    new_address = payload.address.strip() if payload.address else None

    import re

    # ✅ Phone validation
    if new_phone_email and new_phone_email.isdigit():
        if not re.match(r"^[6-9]\d{9}$", new_phone_email):
            raise HTTPException(400, "Invalid phone number")

    # ✅ GST validation
    if new_gst and not re.match(r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]", new_gst):
        raise HTTPException(400, "Invalid GST number")

    # ✅ No change
    if (
        supplier.supplier_name == new_name
        and supplier.contact_person == new_contact_person
        and supplier.phone_email == new_phone_email
        and supplier.gst_number == new_gst
        and supplier.address == new_address
    ):
        return SupplierOut.model_validate(supplier)

    # ✅ Duplicate check
    existing = await db.scalar(
        select(Supplier).where(
            Supplier.id != supplier_id,
            Supplier.is_deleted == False,
            (
                (Supplier.gst_number == new_gst if new_gst else False)
                | (
                    Supplier.phone_email == new_phone_email
                    if new_phone_email
                    else False
                )
            ),
        )
    )

    if existing:
        raise HTTPException(400, "GST or phone/email already used")

    try:
        supplier.supplier_name = new_name
        supplier.contact_person = new_contact_person
        supplier.phone_email = new_phone_email
        supplier.gst_number = new_gst
        supplier.address = new_address

        await db.commit()
        await db.refresh(supplier)

    except IntegrityError:
        await db.rollback()
        raise HTTPException(400, "Duplicate supplier")

    return SupplierOut.model_validate(supplier)


@router.delete("/suppliers/{id}")
async def delete_supplier(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
):
    obj = await db.get(Supplier, id)

    if not obj or obj.is_deleted:
        raise HTTPException(404, "Supplier not found")

    # ✅ FIX: ignore deleted materials
    in_use = await db.scalar(
        select(func.count()).where(
            Material.supplier_id == id, Material.is_deleted == False  # 🔥 important fix
        )
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
        select(Material, Supplier.supplier_name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.supplier_id == supplier_id, Material.is_deleted == False)
        .offset(skip)
        .limit(limit)
    )

    rows = (await db.execute(query)).all()

    return [build_material_response(m, supplier_name) for m, supplier_name in rows]


# ================= material_alerts =================


@router.get("/alerts", response_model=list[MaterialOut])
async def get_material_alerts(
    threshold: float | None = None,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(Material, Supplier.supplier_name)
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
async def create_po(
    payload: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
):

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
async def get_po(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):

    po = await db.get(PurchaseOrder, id)

    if not po or po.is_deleted:
        raise HTTPException(404, "PO not found")

    return build_po_response(po)


@router.get("/purchase-orders", response_model=List[PurchaseOrderOut])
async def list_po(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):

    limit = min(max(limit, 1), 100)

    rows = (
        (
            await db.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.is_deleted == False)
                .order_by(PurchaseOrder.id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

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
async def delete_po(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
):

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
    if current_user.role != UserRole.ADMIN.value and project_id not in (
        current_user.allowed_projects or []
    ):
        raise HTTPException(403, "Access denied")

    query = (
        select(MaterialTransaction, Material.material_name, Supplier.supplier_name)
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
    import uuid
    from decimal import Decimal

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

        # ===== LOCK SOURCE =====
        material = await db.scalar(
            select(Material)
            .where(Material.id == payload.material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not material:
            raise HTTPException(404, "Material not found")

        if payload.quantity > material.remaining_stock:
            raise HTTPException(400, "Not enough stock")

        # ===== WAC CALCULATION =====
        qty_purchased = material.quantity_purchased or Decimal("0")
        total_amt = material.total_amount or Decimal("0")

        avg_rate = total_amt / qty_purchased if qty_purchased > 0 else Decimal("0")
        total = payload.quantity * avg_rate

        # ===== UPDATE SOURCE =====
        material.quantity_used += payload.quantity
        update_material_fields(material)

        # ===== LOCK DESTINATION =====
        existing_material = await db.scalar(
            select(Material)
            .where(
                Material.project_id == payload.to_project_id,
                func.lower(Material.material_name)
                == material.material_name.strip().lower(),
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        if existing_material:
            existing_material.quantity_purchased += payload.quantity
            existing_material.total_amount += total
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
                purchase_rate=avg_rate,
                rate_type=material.rate_type,
                quantity_purchased=payload.quantity,
                quantity_used=Decimal("0"),
                payment_given=Decimal("0"),
                total_amount=total,
                minimum_stock_level=material.minimum_stock_level,
            )

            db.add(existing_material)
            await db.flush()

        # ===== TRANSACTION + LEDGER =====
        def create_entry(mat_id, type_, project_id, qty):
            db.add(
                MaterialTransaction(
                    material_id=mat_id,
                    type=type_,
                    project_id=project_id,
                    quantity=qty,  # ✅ FIXED SIGN HERE
                    rate=avg_rate,
                    total_amount=total,
                    issue_type=IssueType.TRANSFER,
                    reference_id=reference,
                )
            )

            db.add(
                MaterialLedger(
                    material_id=mat_id,
                    type=type_,
                    project_id=project_id,
                    quantity=qty,  # ✅ FIXED SIGN HERE
                    rate=avg_rate,
                    total_amount=total,
                    reference_id=reference,
                )
            )

        # ✅ CORRECT SIGN USAGE
        create_entry(
            material.id,
            DBTransactionType.TRANSFER_OUT,
            payload.from_project_id,
            -payload.quantity,  # 🔴 OUT = NEGATIVE
        )

        create_entry(
            existing_material.id,
            DBTransactionType.TRANSFER_IN,
            payload.to_project_id,
            payload.quantity,  # 🟢 IN = POSITIVE
        )

        obj = MaterialTransfer(
            **payload.model_dump(),
            status="COMPLETED",
            reference_id=reference,
        )

        db.add(obj)

        await db.commit()
        await db.refresh(obj)

    except Exception as e:
        await db.rollback()
        raise e

    await bump_cache_version(redis, VERSION_KEY)

    return build_transfer_response(obj, material, from_project, to_project)


# ================= LIST TRANSFERS =================


@router.get("/transfers")
async def list_transfers(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):

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
async def get_transfer(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):

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


# =================update_transfer_status=========


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
        material=(
            TransferMaterial(id=material.id, name=material.material_name)
            if material
            else None
        ),
        from_project=(
            TransferProject(id=from_project.id, name=from_project.project_name)
            if from_project
            else None
        ),
        to_project=(
            TransferProject(id=to_project.id, name=to_project.project_name)
            if to_project
            else None
        ),
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

    # ===== FIXED AVG RATE =====
    # ✅ Based ONLY on purchases (your design)
    avg_rate = total_amount_current / purchased if purchased > 0 else Decimal("0")

    used_value = qty * avg_rate

    reference = f"USE-{uuid.uuid4().hex[:8]}"
    issue_type = data.issue_type or IssueType.SYSTEM

    try:
        # ===== TRANSACTION =====
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.USAGE,
                quantity=-qty,  # ✅ FIX: negative for usage
                rate=avg_rate,
                avg_rate=avg_rate,  # ✅ consistency
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
                quantity=-qty,  # ✅ FIX
                rate=avg_rate,
                avg_rate=avg_rate,
                total_amount=used_value,
                amount_paid=0,
                payment_pending=0,
                issue_type=issue_type,
                project_id=data.project_id,
                remarks="Material used",
                reference_id=reference,
            )
        )

        # ===== USAGE TABLE =====
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

        # ❌ DO NOT TOUCH total_amount (as per your design)

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
        supplier_name=supplier.supplier_name if supplier else None,
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
    from decimal import Decimal, ROUND_HALF_UP

    try:
        obj = await db.scalar(
            select(Material)
            .options(selectinload(Material.supplier))
            .where(Material.id == material_id, Material.is_deleted == False)
            .with_for_update()
        )

        if not obj:
            raise HTTPException(404, "Material not found")

        # ===== SAFE INPUT =====
        qty = Decimal(str(data.quantity))
        rate = Decimal(str(data.rate))
        paid = Decimal(str(data.amount_paid or 0))

        if qty <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        if rate <= 0:
            raise HTTPException(400, "Rate must be > 0")

        if paid < 0:
            raise HTTPException(400, "Payment cannot be negative")

        # ===== EXISTING DATA =====
        old_qty = obj.quantity_purchased or Decimal("0")
        old_rate = obj.purchase_rate or Decimal("0")

        # ===== SAME LOGIC =====
        if old_qty > 0:
            new_rate = ((old_qty * old_rate) + (qty * rate)) / (old_qty + qty)
        else:
            new_rate = rate

        obj.purchase_rate = new_rate

        # ===== TOTAL =====
        total = (qty * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # ===== PAYMENT =====
        payment_pending = max(Decimal("0"), total - paid)

        reference = f"PUR-{uuid.uuid4().hex[:8]}"

        # ===== TRANSACTION =====
        db.add(
            MaterialTransaction(
                material_id=obj.id,
                type=DBTransactionType.PURCHASE,
                quantity=qty,
                rate=rate,
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
                rate=rate,
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
        obj.remaining_stock = (obj.remaining_stock or Decimal("0")) + qty
        obj.payment_given = (obj.payment_given or Decimal("0")) + paid
        obj.total_amount = (obj.total_amount or Decimal("0")) + total

        # 🔥 IMPORTANT FIX (DB consistency)
        obj.payment_pending = max(Decimal("0"), obj.total_amount - obj.payment_given)

        # ⚠️ must not override total/stock
        update_material_fields(obj)

        await db.commit()

    except Exception as e:
        await db.rollback()
        raise e

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier

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
        supplier_name=supplier.supplier_name if supplier else None,
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


# ================= ADD INVENTORY =================
@router.post("/inventory")
async def adjust_inventory(
    payload: InventoryAdjustRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    redis=Depends(get_request_redis),
):
    import uuid
    from decimal import Decimal

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

        # ✅ SAFE avg_rate
        avg_rate = (
            total_amt / qty_purchased
            if qty_purchased and qty_purchased > 0
            else (material.purchase_rate or Decimal("0"))
        )

        # ===== STOCK + COST UPDATE =====
        if diff > 0:
            # Stock increase
            material.quantity_purchased += diff

            # Cost increase (OK)
            material.total_amount += diff * avg_rate

        else:
            # Stock decrease
            decrease_qty = abs(diff)
            material.quantity_used += decrease_qty

            # ⚠️ IMPORTANT FIX:
            # DO NOT reduce total_amount (this was breaking avg_rate)
            cost_reduction = decrease_qty * avg_rate  # kept for logging/debug only

            # Keep structure but skip deduction
            if material.total_amount <= 0:
                material.total_amount = Decimal("0")

        # ✅ Recalculate derived fields
        update_material_fields(material)

        audit_remark = f"Stock adjusted: {old_stock} → {new_stock} | {reason}"

        # ===== TRANSACTION =====
        db.add(
            MaterialTransaction(
                material_id=material.id,
                type=DBTransactionType.ADJUSTMENT,
                quantity=diff,
                rate=avg_rate,
                total_amount=abs(diff) * avg_rate if diff > 0 else 0,
                amount_paid=0,
                payment_pending=0,
                issue_type=IssueType.SYSTEM,
                project_id=material.project_id,
                remarks=audit_remark,
                reference_id=reference,
            )
        )

        # ===== LEDGER =====
        db.add(
            MaterialLedger(
                material_id=material.id,
                type=DBTransactionType.ADJUSTMENT,
                quantity=diff,
                rate=avg_rate,
                total_amount=abs(diff) * avg_rate if diff > 0 else 0,
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


# ===============get_all_inventory===========================


@router.get("/inventory")
async def get_all_inventory(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
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

        avg_rate = total_amount / qty_purchased if qty_purchased > 0 else Decimal("0")

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


# ==================get_inventory_valuation=======================


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

        avg_rate = total_amount / purchased if purchased > 0 else Decimal("0")

        total_value += remaining * avg_rate

    return {"total_value": float(total_value.quantize(Decimal("0.01")))}


# ======================================================


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

        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")

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

        avg_rate = (total_amt / purchased if purchased > 0 else Decimal("0")).quantize(
            Decimal("0.01")
        )

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

NAVY = colors.HexColor("#163A6B")
ORANGE = colors.HexColor("#F57C00")
BG_LIGHT = colors.HexColor("#F4F7FC")
BORDER_CLR = colors.HexColor("#D0D5DD")
TEXT_DARK = colors.HexColor("#1A1A2E")
TEXT_GREY = colors.HexColor("#667085")
RED_ALERT = colors.HexColor("#D92D20")
RED_BG = colors.HexColor("#FFF1F0")
GREEN_OK = colors.HexColor("#027A48")
AMBER_LOW = colors.HexColor("#B54708")
AMBER_BG = colors.HexColor("#FFFAEB")
WHITE = colors.white
FOOTER_BG = colors.HexColor("#EEF2F7")


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def fmt(val: Decimal, dec: int = 2) -> str:
    try:
        return f"{float(val):,.{dec}f}"
    except Exception:
        return str(val)


def rs(val: Decimal, dec: int = 2) -> str:
    return f"Rs. {fmt(val, dec)}"


# ─── CANVAS: PAGE HEADER + FOOTER ─────────────────────────────────────────────
def _draw_page(canvas_obj, doc):
    canvas_obj.saveState()
    w, h = A4

    # top orange bar
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.rect(0, h - 6, w, 6, fill=1, stroke=0)

    # navy header band
    canvas_obj.setFillColor(NAVY)
    canvas_obj.rect(0, h - 70, w, 64, fill=1, stroke=0)

    # INFRA (white) + PILOT (orange)
    canvas_obj.setFillColor(WHITE)
    canvas_obj.setFont("Helvetica-Bold", 20)
    canvas_obj.drawString(25 * mm, h - 38, "INFRA")
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.drawString(25 * mm + 60, h - 38, "PILOT")

    # tagline
    canvas_obj.setFillColor(colors.HexColor("#AECBF5"))
    canvas_obj.setFont("Helvetica", 8)
    canvas_obj.drawString(25 * mm, h - 51, "Construction Billing Software")

    # REPORT badge (top-right)
    bx, by = w - 55 * mm, h - 57
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.roundRect(bx, by, 48 * mm, 23, radius=4, fill=1, stroke=0)
    canvas_obj.setFillColor(WHITE)
    canvas_obj.setFont("Helvetica-Bold", 13)
    canvas_obj.drawCentredString(bx + 24 * mm, by + 6, "REPORT")

    # generated timestamp
    canvas_obj.setFillColor(colors.HexColor("#AECBF5"))
    canvas_obj.setFont("Helvetica", 7.5)
    ts = datetime.utcnow().strftime("%d/%m/%Y  %H:%M UTC")
    canvas_obj.drawRightString(w - 15 * mm, h - 67, f"Generated: {ts}")

    # page-1 title block — canvas-drawn for pixel-perfect centering
    if doc.page == 1:
        canvas_obj.setFillColor(NAVY)
        canvas_obj.setFont("Helvetica-Bold", 16)
        canvas_obj.drawCentredString(w / 2, h - 96, "Material Inventory Report")

        canvas_obj.setFillColor(TEXT_GREY)
        canvas_obj.setFont("Helvetica", 9)
        date_str = datetime.utcnow().strftime("%d %B %Y")
        canvas_obj.drawCentredString(
            w / 2, h - 111, f"Pune, Maharashtra   |   {date_str}"
        )

        canvas_obj.setStrokeColor(ORANGE)
        canvas_obj.setLineWidth(2)
        canvas_obj.line(15 * mm, h - 119, w - 15 * mm, h - 119)

    # bottom orange bar
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.rect(0, 0, w, 5, fill=1, stroke=0)

    # footer text + page number
    canvas_obj.setFillColor(TEXT_GREY)
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.drawCentredString(
        w / 2, 10, "Generated by Infra Pilot System  \u2022  Confidential"
    )
    canvas_obj.drawRightString(w - 15 * mm, 10, f"Page {doc.page}")

    canvas_obj.restoreState()


# ─── PDF BUILDER ────────────────────────────────────────────────────
def _build_pdf(file_path: str, rows: list) -> None:
    doc = BaseDocTemplate(
        file_path,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=130,
        bottomMargin=22,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_draw_page)])

    elements = []

    # ── styles ───────────────────────────────────────────────────
    sec_s = ParagraphStyle(
        "sec",
        fontName="Helvetica-Bold",
        fontSize=10,
        textColor=NAVY,
        spaceBefore=2,
        spaceAfter=2,
    )
    hdr_s = ParagraphStyle(
        "hdr", fontName="Helvetica-Bold", fontSize=8, textColor=WHITE, leading=11
    )
    cell_s = ParagraphStyle(
        "cell", fontName="Helvetica", fontSize=8, textColor=TEXT_DARK, leading=11
    )
    foot_s = ParagraphStyle(
        "foot", fontName="Helvetica-Bold", fontSize=8, textColor=NAVY, leading=11
    )

    # ── spacer for canvas title ───────────────────────────────────
    elements.append(Spacer(1, 18))

    # ── contact info row ─────────────────────────────────────────
    ci_s = ParagraphStyle("ci", fontName="Helvetica-Bold", fontSize=8, textColor=NAVY)
    ci_table = Table(
        [
            [
                Paragraph("Pune, Maharashtra", ci_s),
                Paragraph("+91 9999999999", ci_s),
                Paragraph("info@infrapilot.com", ci_s),
                Paragraph("www.infrapilot.com", ci_s),
            ]
        ],
        colWidths=[doc.width / 4] * 4,
    )
    ci_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER_CLR),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    elements.append(ci_table)
    elements.append(Spacer(1, 16))

    # ── compute summary + processed rows ─────────────────────────
    total_purchased = Decimal("0")
    total_used = Decimal("0")
    total_remaining = Decimal("0")
    total_value = Decimal("0")
    total_pending = Decimal("0")
    alerts_out: list[str] = []
    alerts_low: list[str] = []
    processed = []

    for m, sup in rows:
        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        pend = m.payment_pending or Decimal("0")
        min_lvl = Decimal(str(m.minimum_stock_level or 0))

        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")
        value = remaining * avg_rate

        total_purchased += purchased
        total_used += used
        total_remaining += remaining
        total_value += value
        total_pending += pend

        if remaining == 0:
            status = "OUT"
            alerts_out.append(m.material_name.title())
        elif remaining <= min_lvl:
            status = "LOW"
            alerts_low.append(m.material_name.title())
        else:
            status = "OK"

        processed.append((m, sup, purchased, used, remaining, avg_rate, value, status))

    # ── summary cards ────────────────────────────────────────────
    elements.append(Paragraph("SUMMARY", sec_s))
    elements.append(Spacer(1, 6))

    card_w = doc.width / 5 - 2

    def _card(label: str, value: str) -> Table:
        lbl_s = ParagraphStyle(
            "lbl", fontName="Helvetica", fontSize=7.5, textColor=TEXT_GREY
        )
        val_s = ParagraphStyle(
            "val", fontName="Helvetica-Bold", fontSize=12, textColor=NAVY, leading=15
        )
        return Table(
            [[Paragraph(label, lbl_s)], [Paragraph(f"<b>{value}</b>", val_s)]],
            colWidths=[card_w],
        )

    cards = Table(
        [
            [
                _card("Total Materials", str(len(rows))),
                _card("Total Purchased", fmt(total_purchased, 0) + " units"),
                _card("Total Used", fmt(total_used, 0) + " units"),
                _card("Stock Value", rs(total_value, 0)),
                _card("Pending", rs(total_pending, 0)),
            ]
        ],
        colWidths=[card_w + 3] * 5,
    )

    cards.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
                ("BOX", (0, 0), (-1, -1), 1, BORDER_CLR),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER_CLR),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("LINEABOVE", (0, 0), (-1, 0), 3, ORANGE),
            ]
        )
    )
    elements.append(cards)
    elements.append(Spacer(1, 20))

    # ── material details table ────────────────────────────────────
    elements.append(Paragraph("MATERIAL DETAILS", sec_s))
    elements.append(Spacer(1, 6))

    col_widths = [
        10 * mm,
        35 * mm,
        35 * mm,
        20 * mm,
        16 * mm,
        20 * mm,
        20 * mm,
        28 * mm,
        16 * mm,
    ]
    col_labels = [
        "#",
        "Material Name",
        "Supplier",
        "Purchased",
        "Used",
        "Remaining",
        "Avg Rate",
        "Value (Rs.)",
        "Status",
    ]

    tdata = [[Paragraph(c, hdr_s) for c in col_labels]]
    out_rows: list[int] = []
    low_rows: list[int] = []

    for i, (m, sup, purchased, used, remaining, avg_rate, value, status) in enumerate(
        processed
    ):
        ridx = i + 1
        if status == "OUT":
            st = Paragraph(
                "<b>OUT</b>",
                ParagraphStyle(
                    "s", fontName="Helvetica-Bold", fontSize=8, textColor=RED_ALERT
                ),
            )
            out_rows.append(ridx)
        elif status == "LOW":
            st = Paragraph(
                "<b>LOW</b>",
                ParagraphStyle(
                    "s", fontName="Helvetica-Bold", fontSize=8, textColor=AMBER_LOW
                ),
            )
            low_rows.append(ridx)
        else:
            st = Paragraph(
                "<b>OK</b>",
                ParagraphStyle(
                    "s", fontName="Helvetica-Bold", fontSize=8, textColor=GREEN_OK
                ),
            )

        tdata.append(
            [
                Paragraph(str(i + 1), cell_s),
                Paragraph((m.material_name or "").title(), cell_s),
                Paragraph(sup or "N/A", cell_s),
                Paragraph(fmt(purchased, 1), cell_s),
                Paragraph(fmt(used, 1), cell_s),
                Paragraph(fmt(remaining, 1), cell_s),
                Paragraph(fmt(avg_rate, 2), cell_s),
                Paragraph(fmt(value, 2), cell_s),
                st,
            ]
        )

    # totals footer row
    tdata.append(
        [
            Paragraph("", foot_s),
            Paragraph("<b>TOTAL</b>", foot_s),
            Paragraph("", foot_s),
            Paragraph(f"<b>{fmt(total_purchased, 1)}</b>", foot_s),
            Paragraph(f"<b>{fmt(total_used, 1)}</b>", foot_s),
            Paragraph(f"<b>{fmt(total_remaining, 1)}</b>", foot_s),
            Paragraph("", foot_s),
            Paragraph(f"<b>{fmt(total_value, 2)}</b>", foot_s),
            Paragraph("", foot_s),
        ]
    )
    frow = len(tdata) - 1

    detail = Table(tdata, colWidths=col_widths, repeatRows=1)
    ts = TableStyle(
        [
            # header
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("LINEBELOW", (0, 0), (-1, 0), 2, ORANGE),
            ("TOPPADDING", (0, 0), (-1, 0), 8),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
            # data rows
            ("ROWBACKGROUNDS", (0, 1), (-1, frow - 1), [WHITE, BG_LIGHT]),
            ("TOPPADDING", (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
            # totals footer
            ("BACKGROUND", (0, frow), (-1, frow), FOOTER_BG),
            ("LINEABOVE", (0, frow), (-1, frow), 1.5, NAVY),
            ("LINEBELOW", (0, frow), (-1, frow), 1.5, NAVY),
            # grid
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER_CLR),
            ("BOX", (0, 0), (-1, -1), 1, BORDER_CLR),
            # alignment
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (3, 0), (7, -1), "RIGHT"),
            ("ALIGN", (8, 0), (8, -1), "CENTER"),
            # padding
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
            ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
    )
    for r in out_rows:
        ts.add("BACKGROUND", (0, r), (-1, r), RED_BG)
    for r in low_rows:
        ts.add("BACKGROUND", (0, r), (-1, r), AMBER_BG)

    detail.setStyle(ts)
    elements.append(detail)

    # ── alerts ───────────────────────────────────────────────────
    all_alerts = [(a, "OUT OF STOCK", RED_ALERT, RED_BG) for a in alerts_out] + [
        (a, "LOW STOCK", AMBER_LOW, AMBER_BG) for a in alerts_low
    ]
    if all_alerts:
        elements.append(Spacer(1, 18))
        # section header with left orange accent bar
        alert_hdr = Table([[Paragraph("ALERTS", sec_s)]], colWidths=[doc.width])
        alert_hdr.setStyle(
            TableStyle(
                [
                    ("LINEBEFORE", (0, 0), (0, -1), 4, ORANGE),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        elements.append(alert_hdr)
        elements.append(Spacer(1, 8))

        a_rows = []
        for name, label, txt_clr, _ in all_alerts:
            badge_s = ParagraphStyle(
                "badge", fontName="Helvetica-Bold", fontSize=7.5, textColor=txt_clr
            )
            name_s = ParagraphStyle(
                "aname", fontName="Helvetica-Bold", fontSize=8.5, textColor=TEXT_DARK
            )
            a_rows.append(
                [Paragraph(f"<b>{label}</b>", badge_s), Paragraph(name, name_s)]
            )

        a_table = Table(a_rows, colWidths=[32 * mm, doc.width - 32 * mm])
        a_ts = TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, BORDER_CLR),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER_CLR),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBEFORE", (0, 0), (0, -1), 4, RED_ALERT),
            ]
        )
        for i, (_, _, _, bg_clr) in enumerate(all_alerts):
            a_ts.add("BACKGROUND", (0, i), (-1, i), bg_clr)
        a_table.setStyle(a_ts)
        elements.append(a_table)

    doc.build(elements)


# ─── FASTAPI ENDPOINT ─────────────────────────────────────────────────────────
@router.get("/reports/materials/pdf", response_class=FileResponse)
async def export_pdf(
    supplier_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_roles(MATERIAL_READ_ROLES)),
):
    query = (
        select(Material, Supplier.supplier_name)
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .where(Material.is_deleted == False)
    )
    if supplier_id:
        query = query.where(Material.supplier_id == supplier_id)

    rows = (await db.execute(query)).all()
    if not rows:
        raise HTTPException(404, "No data found")

    file_path = os.path.join(tempfile.gettempdir(), f"mat_report_{uuid.uuid4()}.pdf")
    _build_pdf(file_path, rows)

    return FileResponse(
        file_path,
        media_type="application/pdf",
        filename="material_report.pdf",
        background=BackgroundTask(safe_delete, file_path),
    )


# ==================excel report=====================


@router.get("/reports/materials/excel", response_class=FileResponse)
async def export_excel(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    from decimal import Decimal
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, GradientFill
    from openpyxl.utils import get_column_letter
    from openpyxl.formatting.rule import ColorScaleRule, CellIsRule
    from openpyxl.styles.numbers import FORMAT_NUMBER_COMMA_SEPARATED1

    # ── Fetch data ─────────────────────────────────────────────────────────────
    materials = (
        (await db.execute(select(Material).where(Material.is_deleted == False)))
        .scalars()
        .all()
    )

    transfer_data = await db.execute(
        select(
            MaterialTransaction.material_id,
            func.sum(
                case(
                    (
                        MaterialTransaction.type == DBTransactionType.TRANSFER_IN,
                        MaterialTransaction.quantity,
                    ),
                    else_=0,
                )
            ).label("transfer_in"),
            func.sum(
                case(
                    (
                        MaterialTransaction.type == DBTransactionType.TRANSFER_OUT,
                        MaterialTransaction.quantity,
                    ),
                    else_=0,
                )
            ).label("transfer_out"),
        ).group_by(MaterialTransaction.material_id)
    )

    transfer_map = {
        row.material_id: {
            "in": row.transfer_in or Decimal("0"),
            "out": row.transfer_out or Decimal("0"),
        }
        for row in transfer_data
    }

    # ── Colors ─────────────────────────────────────────────────────────────────
    C_NAVY = "1A2B4A"
    C_ORANGE = "F5A623"
    C_WHITE = "FFFFFF"
    C_LIGHT_GREY = "F5F5F5"
    C_ALT_ROW = "EFF3FB"
    C_GREEN_BG = "E8F5E9"
    C_GREEN_FG = "2E7D32"
    C_RED_BG = "FFEBEE"
    C_RED_FG = "C62828"
    C_AMBER_BG = "FFF8E1"
    C_AMBER_FG = "E65100"
    C_BORDER = "CCCCCC"
    C_TOTAL_BG = "1A2B4A"

    # ── Style helpers ──────────────────────────────────────────────────────────
    def fill(hex_color):
        return PatternFill("solid", fgColor=hex_color)

    def font(bold=False, color=C_NAVY, size=10, italic=False):
        return Font(name="Arial", bold=bold, color=color, size=size, italic=italic)

    def align(h="left", v="center", wrap=False):
        return Alignment(horizontal=h, vertical=v, wrap_text=wrap)

    def thin_border(left=True, right=True, top=True, bottom=True):
        s = Side(style="thin", color=C_BORDER)
        n = Side(style=None)
        return Border(
            left=s if left else n,
            right=s if right else n,
            top=s if top else n,
            bottom=s if bottom else n,
        )

    def thick_bottom():
        return Border(bottom=Side(style="medium", color=C_NAVY))

    NUM_FMT_INT = "#,##0"
    NUM_FMT_DEC = "#,##0.00"
    NUM_FMT_MONEY = "₹ #,##0.00"

    # ── Workbook setup ─────────────────────────────────────────────────────────
    wb = Workbook()
    ws = wb.active
    ws.title = "Material Report"

    # ── Row 1: Title banner ────────────────────────────────────────────────────
    ws.merge_cells("A1:J1")
    ws["A1"] = "INFRAPILOT — Material Inventory Report"
    ws["A1"].font = font(bold=True, color=C_WHITE, size=14)
    ws["A1"].fill = fill(C_NAVY)
    ws["A1"].alignment = align("center")
    ws.row_dimensions[1].height = 32

    # ── Row 2: Generated date ──────────────────────────────────────────────────
    from datetime import datetime

    ws.merge_cells("A2:J2")
    ws["A2"] = f"Generated: {datetime.now().strftime('%d %b %Y  %I:%M %p')}"
    ws["A2"].font = font(color=C_WHITE, size=9, italic=True)
    ws["A2"].fill = fill(C_ORANGE)
    ws["A2"].alignment = align("right")
    ws.row_dimensions[2].height = 18

    # ── Row 3: blank spacer ────────────────────────────────────────────────────
    ws.row_dimensions[3].height = 8

    # ── Rows 4-6: Summary KPI block ────────────────────────────────────────────
    #  Labels row
    kpi_labels = [
        "Total Materials",
        "Total Purchased",
        "Total Used",
        "Remaining Stock",
        "Total Cost (Rs.)",
        "Total Pending (Rs.)",
    ]
    kpi_cols = [
        "A",
        "B",
        "C",
        "D",
        "G",
        "J",
    ]  # approximate columns for 2-col merged groups

    # We'll use a 3-wide merge per KPI across 6 KPIs → cols A-R would be too wide
    # Instead: use cols A:B, C:D, E:F, G:H, I:J split across 5 pairs in 10 cols
    kpi_pairs = [("A", "B"), ("C", "D"), ("E", "F"), ("G", "H"), ("I", "J")]

    total_purchased = sum(float(m.quantity_purchased or 0) for m in materials)
    total_used = sum(float(m.quantity_used or 0) for m in materials)
    total_remaining = sum(float(m.remaining_stock or 0) for m in materials)

    total_cost = Decimal("0")
    for m in materials:
        purchased = m.quantity_purchased or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")
        total_cost += remaining * avg_rate

    total_pending = sum(float(m.payment_pending or 0) for m in materials)

    kpi_data = [
        ("Total Materials", len(materials), None),
        ("Total Purchased", total_purchased, "units"),
        ("Total Used", total_used, "units"),
        ("Remaining Stock", total_remaining, "units"),
        ("Total Cost", float(round(total_cost, 2)), "Rs."),
        ("Pending Payment", total_pending, "Rs."),
    ]

    for row in [4, 5]:
        ws.row_dimensions[row].height = 20

    for idx, ((start_col, end_col), (label, val, unit)) in enumerate(
        zip(kpi_pairs, kpi_data)
    ):
        # Label cell (row 4)
        lbl_cell = f"{start_col}4"
        ws.merge_cells(f"{start_col}4:{end_col}4")
        ws[lbl_cell] = label
        ws[lbl_cell].font = font(bold=False, color="777777", size=8)
        ws[lbl_cell].fill = fill(C_LIGHT_GREY)
        ws[lbl_cell].alignment = align("center")

        # Value cell (row 5)
        val_cell = f"{start_col}5"
        ws.merge_cells(f"{start_col}5:{end_col}5")
        display = (
            f"Rs. {val:,.0f}"
            if unit == "Rs."
            else (f"{val:,.0f} {unit}" if unit else str(val))
        )
        ws[val_cell] = display
        ws[val_cell].font = font(bold=True, color=C_NAVY, size=11)
        ws[val_cell].fill = fill(C_WHITE)
        ws[val_cell].alignment = align("center")

        for r in [4, 5]:
            ws[f"{start_col}{r}"].border = thin_border()

    ws.row_dimensions[6].height = 10  # spacer

    # ── Row 7: Section label ───────────────────────────────────────────────────
    ws.merge_cells("A7:J7")
    ws["A7"] = "MATERIAL DETAILS"
    ws["A7"].font = font(bold=True, color=C_NAVY, size=10)
    ws["A7"].fill = fill(C_LIGHT_GREY)
    ws["A7"].alignment = align("left")
    ws["A7"].border = thick_bottom()
    ws.row_dimensions[7].height = 22

    # ── Row 8: Column headers ──────────────────────────────────────────────────
    headers = [
        "#",
        "Material Name",
        "Supplier",
        "Purchased\n(units)",
        "Used\n(units)",
        "Remaining\n(units)",
        "Transfer In",
        "Transfer Out",
        "Avg Rate\n(Rs.)",
        "Stock Value\n(Rs.)",
        "Paid\n(Rs.)",
        "Pending\n(Rs.)",
        "Status",
    ]

    # Expand to 13 columns — add Supplier & Status
    ws.merge_cells("A8:A8")  # no-op but keeps it consistent

    for col_idx, hdr in enumerate(headers, start=1):
        cell = ws.cell(row=8, column=col_idx, value=hdr)
        cell.font = font(bold=True, color=C_WHITE, size=9)
        cell.fill = fill(C_NAVY)
        cell.alignment = align("center", wrap=True)
        cell.border = thin_border()

    ws.row_dimensions[8].height = 30

    # ── Column widths ──────────────────────────────────────────────────────────
    col_widths = [5, 22, 18, 12, 12, 12, 12, 12, 12, 14, 12, 12, 10]
    for i, w in enumerate(col_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # ── Data rows ──────────────────────────────────────────────────────────────
    DATA_START = 9
    row_idx = DATA_START

    for i, m in enumerate(materials, start=1):
        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        paid = m.payment_given or Decimal("0")
        pending = m.payment_pending or Decimal("0")
        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")
        stock_val = remaining * avg_rate
        t_in = transfer_map.get(m.id, {}).get("in", Decimal("0"))
        t_out = transfer_map.get(m.id, {}).get("out", Decimal("0"))

        # Status logic
        if float(remaining) == 0:
            status, s_fg, s_bg = "OUT", C_RED_FG, C_RED_BG
        elif float(remaining) < float(purchased) * 0.15:
            status, s_fg, s_bg = "LOW", C_AMBER_FG, C_AMBER_BG
        else:
            status, s_fg, s_bg = "OK", C_GREEN_FG, C_GREEN_BG

        bg = C_WHITE if i % 2 == 1 else C_ALT_ROW

        row_vals = [
            i,
            (m.material_name or "").title(),
            getattr(m, "supplier_name", None) or "-",
            float(purchased),
            float(used),
            float(remaining),
            float(t_in),
            float(t_out),
            float(round(avg_rate, 2)),
            float(round(stock_val, 2)),
            float(paid),
            float(pending),
            status,
        ]

        for col_idx, val in enumerate(row_vals, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = font(size=9, color="333333")
            cell.fill = fill(bg)
            cell.alignment = align("center")
            cell.border = thin_border()

            # Number formatting
            if col_idx in (4, 5, 6, 7, 8):
                cell.number_format = NUM_FMT_INT
            elif col_idx in (9, 10, 11, 12):
                cell.number_format = NUM_FMT_DEC
                cell.alignment = align("right")

            # Status column styling
            if col_idx == 13:
                cell.fill = fill(s_bg)
                cell.font = font(bold=True, color=s_fg, size=9)
                cell.alignment = align("center")

        ws.row_dimensions[row_idx].height = 18
        row_idx += 1

    # ── Total row ─────────────────────────────────────────────────────────────
    data_end = row_idx - 1
    ws.row_dimensions[row_idx].height = 22

    total_labels = {
        1: "TOTAL",
        4: f"=SUM(D{DATA_START}:D{data_end})",
        5: f"=SUM(E{DATA_START}:E{data_end})",
        6: f"=SUM(F{DATA_START}:F{data_end})",
        7: f"=SUM(G{DATA_START}:G{data_end})",
        8: f"=SUM(H{DATA_START}:H{data_end})",
        10: f"=SUM(J{DATA_START}:J{data_end})",
        11: f"=SUM(K{DATA_START}:K{data_end})",
        12: f"=SUM(L{DATA_START}:L{data_end})",
    }

    for col_idx in range(1, 14):
        val = total_labels.get(col_idx, "")
        cell = ws.cell(row=row_idx, column=col_idx, value=val)
        cell.font = font(bold=True, color=C_WHITE, size=9)
        cell.fill = fill(C_TOTAL_BG)
        cell.alignment = align("center")
        cell.border = thin_border()
        if col_idx in (10, 11, 12):
            cell.number_format = NUM_FMT_DEC
            cell.alignment = align("right")
        elif col_idx in (4, 5, 6, 7, 8):
            cell.number_format = NUM_FMT_INT

    # ── Freeze panes (header rows + col A) ────────────────────────────────────
    ws.freeze_panes = "B9"

    # ── Auto-filter on header row ──────────────────────────────────────────────
    ws.auto_filter.ref = f"A8:{get_column_letter(13)}{data_end}"

    # ── Conditional formatting: Pending column (red if > 0) ───────────────────
    pending_col = f"L{DATA_START}:L{data_end}"
    ws.conditional_formatting.add(
        pending_col,
        CellIsRule(
            operator="greaterThan",
            formula=["0"],
            fill=PatternFill("solid", fgColor=C_RED_BG),
            font=Font(color=C_RED_FG, bold=True, name="Arial", size=9),
        ),
    )

    # ── ALERTS sheet ───────────────────────────────────────────────────────────
    alerts = [
        (m, "OUT OF STOCK", C_RED_FG, C_RED_BG)
        for m in materials
        if float(m.remaining_stock or 0) == 0
    ]
    alerts += [
        (m, "LOW STOCK", C_AMBER_FG, C_AMBER_BG)
        for m in materials
        if 0 < float(m.remaining_stock or 0) < float(m.quantity_purchased or 0) * 0.15
    ]

    if alerts:
        ws_alert = wb.create_sheet("Alerts")
        ws_alert.merge_cells("A1:D1")
        ws_alert["A1"] = "ALERTS — Materials Needing Attention"
        ws_alert["A1"].font = font(bold=True, color=C_WHITE, size=12)
        ws_alert["A1"].fill = fill(C_NAVY)
        ws_alert["A1"].alignment = align("center")
        ws_alert.row_dimensions[1].height = 28

        for col_i, hdr in enumerate(
            ["Status", "Material Name", "Remaining", "Purchased"], 1
        ):
            c = ws_alert.cell(row=2, column=col_i, value=hdr)
            c.font = font(bold=True, color=C_WHITE, size=9)
            c.fill = fill(C_ORANGE)
            c.alignment = align("center")

        for r_i, (m, label, fg, bg) in enumerate(alerts, start=3):
            row_data = [
                label,
                (m.material_name or "").title(),
                float(m.remaining_stock or 0),
                float(m.quantity_purchased or 0),
            ]
            for c_i, v in enumerate(row_data, 1):
                c = ws_alert.cell(row=r_i, column=c_i, value=v)
                c.font = font(bold=(c_i == 1), color=fg, size=9)
                c.fill = fill(bg)
                c.alignment = align("center")
                c.border = thin_border()

        for col_i, w in zip([1, 2, 3, 4], [16, 28, 14, 14]):
            ws_alert.column_dimensions[get_column_letter(col_i)].width = w

    # ── Save ───────────────────────────────────────────────────────────────────
    file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
    wb.save(file_path)

    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"material_report_{datetime.now().strftime('%Y%m%d')}.xlsx",
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
        select(MaterialTransaction.rate, MaterialTransaction.created_at)
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
    normalized_name = raw_name.strip().lower()
    data["material_name"] = normalized_name  # ✅ store normalized

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
    obj.total_amount = (obj.quantity_purchased * obj.purchase_rate).quantize(
        Decimal("0.01")
    )

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

    response.supplier_name = supplier.supplier_name
    response.material_name = obj.material_name.title()

    return response


# =================list_materials=========================


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
        select(Material, Supplier.supplier_name)
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


# ==============get_material=================


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
        supplier_name=supplier.supplier_name if supplier else None,
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


# =============update_material==================


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

    # direct payment block
    if "payment_given" in update_data:
        raise HTTPException(
            status_code=400,
            detail="Direct payment update not allowed. Use purchase API",
        )

    # NORMALIZE NAME
    if "material_name" in update_data:
        new_name = update_data["material_name"].strip()
        normalized_name = new_name.lower()
        update_data["material_name"] = new_name

        # DUPLICATE CHECK (IMPORTANT FIX )
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
        # apply updates
        for k, v in update_data.items():
            setattr(obj, k, v)

        # recalc
        update_material_fields(obj)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    await db.refresh(obj)
    await bump_cache_version(redis, VERSION_KEY)

    supplier = await db.get(Supplier, obj.supplier_id)

    total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

    #  alert fix (<= important)
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
        supplier_name=supplier.supplier_name if supplier else None,
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


# ============delete_material===========


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
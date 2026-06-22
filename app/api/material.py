from datetime import date
from typing import Optional, List
from decimal import Decimal, InvalidOperation
import uuid
import os
from sqlalchemy.exc import IntegrityError
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from reportlab.platypus import SimpleDocTemplate, Table
from openpyxl import Workbook
from app.models.user import User, UserRole
from app.schemas.material import (
    InventoryAdjustResponse,
    MaterialReportResponse,
    MaterialReportSummary,
    TransferMaterial,
    TransferProject,
)
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
from app.services.notification_service import create_notification
from app.models import project as proj_model
import os
from app.models.project import Project
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
from app.models.master_data import MaterialMaster, Unit
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

from app.core.logger import logger

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


def build_material_response(
    obj,
    supplier_name: str | None,
    unit_name=None,
):
    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(
        0,
        total_amount - payment_given,
    )

    extra_paid = max(
        0,
        payment_given - total_amount,
    )

    remaining = float(obj.remaining_stock or 0)

    min_level = float(obj.minimum_stock_level or 0)

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
        material_master_id=obj.material_master_id,
        material_master_name=(
            obj.material_master.name if obj.material_master else None
        ),
        material_master_brand=(
            obj.material_master.brand if obj.material_master else None
        ),
        material_master_specification=(
            obj.material_master.specification if obj.material_master else None
        ),
        material_master_hsn_code=(
            obj.material_master.hsn_code if obj.material_master else None
        ),
        material_name=(obj.material_name or "").strip().title(),
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=unit_name or "",
        supplier_id=obj.supplier_id,
        supplier_name=(supplier_name if supplier_name else "N/A"),
        purchase_rate=round(
            float(obj.purchase_rate or 0),
            2,
        ),
        rate_type=obj.rate_type,
        quantity_purchased=round(
            float(obj.quantity_purchased or 0),
            2,
        ),
        quantity_used=round(
            float(obj.quantity_used or 0),
            2,
        ),
        remaining_stock=round(
            remaining,
            2,
        ),
        total_amount=round(
            total_amount,
            2,
        ),
        payment_given=round(
            payment_given,
            2,
        ),
        payment_pending=round(
            payment_pending,
            2,
        ),
        extra_paid=round(
            extra_paid,
            2,
        ),
        minimum_stock_level=round(
            min_level,
            2,
        ),
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
    import traceback
    from decimal import Decimal
    from fastapi import HTTPException

    try:
        print("\n" + "=" * 80)
        print("DEBUG: /materials/summary API called")
        print("=" * 80)

        # ==========================================================
        # STEP 1: TOTAL MATERIALS
        # ==========================================================
        print("DEBUG: Fetching total materials count...")

        total_materials = await db.scalar(
            select(func.count(Material.id)).where(Material.is_deleted == False)
        )

        print(f"DEBUG: total_materials = {total_materials}")

        # ==========================================================
        # STEP 2: FETCH ALL MATERIALS
        # ==========================================================
        print("DEBUG: Fetching all active materials...")

        result = await db.execute(select(Material).where(Material.is_deleted == False))

        rows = result.scalars().all()

        print(f"DEBUG: Number of materials fetched = {len(rows)}")

        # ==========================================================
        # STEP 3: CALCULATE TOTAL STOCK VALUE
        # ==========================================================
        total_stock = Decimal("0")

        print("DEBUG: Starting stock valuation calculation...")

        for index, m in enumerate(rows, start=1):
            try:
                print("-" * 60)
                print(f"DEBUG: Processing material #{index}")
                print(f"DEBUG: id = {m.id}")
                print(f"DEBUG: material_name = {m.material_name}")
                print(f"DEBUG: quantity_purchased = {m.quantity_purchased}")
                print(f"DEBUG: total_amount = {m.total_amount}")
                print(f"DEBUG: remaining_stock = {m.remaining_stock}")

                purchased = m.quantity_purchased or Decimal("0")
                total_amt = m.total_amount or Decimal("0")
                remaining = m.remaining_stock or Decimal("0")

                print(f"DEBUG: purchased = {purchased}")
                print(f"DEBUG: total_amt = {total_amt}")
                print(f"DEBUG: remaining = {remaining}")

                # Weighted Average Cost (WAC)
                if purchased > 0:
                    avg_rate = total_amt / purchased
                else:
                    avg_rate = Decimal("0")

                print(f"DEBUG: avg_rate = {avg_rate}")

                material_stock_value = remaining * avg_rate

                print(f"DEBUG: material_stock_value = {material_stock_value}")

                total_stock += material_stock_value

                print(f"DEBUG: running total_stock = {total_stock}")

            except Exception as material_error:
                print("ERROR while processing material:")
                print(f"ERROR: material_id = {getattr(m, 'id', None)}")
                print(f"ERROR: material_name = {getattr(m, 'material_name', None)}")
                print(f"ERROR: {str(material_error)}")
                traceback.print_exc()
                raise

        print("=" * 80)
        print(f"DEBUG: Final total_stock = {total_stock}")
        print("=" * 80)

        # ==========================================================
        # STEP 4: TOTAL PENDING PAYMENTS
        # ==========================================================
        print("DEBUG: Fetching total pending payments...")

        total_pending = await db.scalar(
            select(func.sum(Material.payment_pending)).where(
                Material.is_deleted == False
            )
        )

        print(f"DEBUG: total_pending = {total_pending}")

        # ==========================================================
        # STEP 5: PREPARE RESPONSE
        # ==========================================================
        response = {
            "total_materials": total_materials or 0,
            "total_stock_value": round(float(total_stock or 0), 2),
            "total_pending_payments": round(float(total_pending or 0), 2),
        }

        print("=" * 80)
        print("DEBUG: Final Response")
        print(response)
        print("=" * 80)

        return response

    except Exception as e:
        print("\n" + "!" * 80)
        print("CRITICAL ERROR IN /materials/summary")
        print(f"ERROR TYPE: {type(e).__name__}")
        print(f"ERROR MESSAGE: {str(e)}")
        traceback.print_exc()
        print("!" * 80 + "\n")

        # Optional: return actual error in API response while debugging
        raise HTTPException(
            status_code=500,
            detail=f"Material summary failed: {type(e).__name__}: {str(e)}",
        )


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


# ================Get_supplier===============


@router.get("/suppliers/{supplier_id}", response_model=SupplierOut)
async def get_supplier(
    supplier_id: int,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    supplier = await db.get(Supplier, supplier_id)

    if not supplier or supplier.is_deleted:
        raise HTTPException(
            status_code=404,
            detail="Supplier not found",
        )

    return SupplierOut.model_validate(supplier)


# ================Create_supplier===============

from sqlalchemy import or_


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

    # 🔹 Phone / Email validation
    phone_email = payload.phone_email.strip() if payload.phone_email else None

    if phone_email:
        cleaned = re.sub(r"[^\d]", "", phone_email)

        if cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = cleaned[2:]

        if cleaned.isdigit():
            if not re.fullmatch(r"[6-9]\d{9}", cleaned):
                raise HTTPException(400, "Invalid Indian mobile number")

            phone_email = cleaned

        elif "@" in phone_email:
            if not re.fullmatch(r"[^@]+@[^@]+\.[^@]+", phone_email):
                raise HTTPException(400, "Invalid email format")

        else:
            raise HTTPException(400, "Enter valid phone number or email")

    # 🔹 GST validation
    gst_number = payload.gst_number.strip().upper() if payload.gst_number else None

    if gst_number and not re.fullmatch(
        r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]",
        gst_number,
    ):
        raise HTTPException(400, "Invalid GST number format")

    # 🔹 Address validation
    address = payload.address.strip() if payload.address else None

    if address and len(address) < 3:
        raise HTTPException(400, "Address too short")

    # ==================================================
    # 🔹 Duplicate Check (FIXED)
    # ==================================================
    conditions = []

    if gst_number:
        conditions.append(Supplier.gst_number == gst_number)

    if phone_email:
        conditions.append(Supplier.phone_email == phone_email)

    existing = None

    if conditions:
        existing = await db.scalar(
            select(Supplier).where(
                Supplier.is_deleted.is_(False),
                or_(*conditions),
            )
        )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Supplier with same GST or phone/email already exists",
        )

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
        raise HTTPException(
            status_code=400,
            detail="Duplicate supplier",
        )

    return SupplierOut.model_validate(supplier)


# ================update_supplier===============


@router.put("/suppliers/{supplier_id}", response_model=SupplierOut)
async def update_supplier(
    supplier_id: int,
    payload: SupplierCreate,
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    import re

    supplier = await db.get(Supplier, supplier_id)

    if not supplier or supplier.is_deleted:
        raise HTTPException(status_code=404, detail="Supplier not found")

    # ================= NORMALIZE =================
    new_name = payload.supplier_name.strip().title()

    new_contact_person = (
        payload.contact_person.strip().title() if payload.contact_person else None
    )

    new_phone_email = payload.phone_email.strip() if payload.phone_email else None

    new_gst = payload.gst_number.strip().upper() if payload.gst_number else None

    new_address = payload.address.strip() if payload.address else None

    # ================= VALIDATIONS =================

    if len(new_name) < 3:
        raise HTTPException(
            status_code=400,
            detail="Supplier name must be at least 3 characters",
        )

    if new_contact_person:
        if not re.fullmatch(r"[A-Za-z ]{3,}", new_contact_person):
            raise HTTPException(
                status_code=400,
                detail="Invalid contact person name",
            )

    # Phone / Email validation
    if new_phone_email:

        cleaned = re.sub(r"[^\d]", "", new_phone_email)

        if cleaned.startswith("91") and len(cleaned) == 12:
            cleaned = cleaned[2:]

        if cleaned.isdigit():

            if not re.fullmatch(r"[6-9]\d{9}", cleaned):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid Indian mobile number",
                )

            new_phone_email = cleaned

        elif "@" in new_phone_email:

            if not re.fullmatch(
                r"[^@]+@[^@]+\.[^@]+",
                new_phone_email,
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Invalid email format",
                )

        else:
            raise HTTPException(
                status_code=400,
                detail="Enter valid phone number or email",
            )

    # GST Validation
    if new_gst:
        if not re.fullmatch(
            r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z][A-Z\d]",
            new_gst,
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid GST number format",
            )

    # ================= DUPLICATE CHECK =================

    duplicate_conditions = []

    if new_gst:
        duplicate_conditions.append(Supplier.gst_number == new_gst)

    if new_phone_email:
        duplicate_conditions.append(Supplier.phone_email == new_phone_email)

    if duplicate_conditions:

        existing = await db.scalar(
            select(Supplier).where(
                Supplier.id != supplier_id,
                Supplier.is_deleted == False,
                or_(*duplicate_conditions),
            )
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail="GST number or phone/email already exists",
            )

    # ================= UPDATE =================

    supplier.supplier_name = new_name
    supplier.contact_person = new_contact_person
    supplier.phone_email = new_phone_email
    supplier.gst_number = new_gst
    supplier.address = new_address

    try:
        await db.commit()
        await db.refresh(supplier)

    except IntegrityError:
        await db.rollback()

        raise HTTPException(
            status_code=400,
            detail="Duplicate supplier",
        )

    return SupplierOut.model_validate(supplier)


# =============delete_supplier============


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


from sqlalchemy.orm import selectinload


@router.get(
    "/suppliers/{supplier_id}/materials",
    response_model=list[MaterialOut],
)
async def get_supplier_materials(
    supplier_id: int,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(
            Material,
            Supplier.supplier_name,
        )
        .options(
            selectinload(Material.unit),
            selectinload(Material.material_master),  # ✅ Added
        )
        .join(
            Supplier,
            Supplier.id == Material.supplier_id,
            isouter=True,
        )
        .where(
            Material.supplier_id == supplier_id,
            Material.is_deleted == False,
        )
        .offset(skip)
        .limit(limit)
    )

    rows = (await db.execute(query)).all()

    return [
        build_material_response(
            m,
            supplier_name,
        )
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
        select(Material, Supplier.supplier_name, Unit.name.label("unit_name"))
        .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
        .join(Unit, Unit.id == Material.unit_id, isouter=True)
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

    for obj, supplier_name, unit_name in rows:

        response = build_material_response(
            obj,
            supplier_name=supplier_name,
            unit_name=unit_name,
        )

        if (
            threshold is not None
            and response.alert_type == "IN_STOCK"
            and response.remaining_stock <= threshold
        ):
            response.alert_type = "NEAR_LOW"

        data.append(MaterialOut.model_validate(response))

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


# ==============================================================


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


# ==============================================================


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


# ==============================================================


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

    if obj.status in ["PENDING", "APPROVED"]:
        raise HTTPException(
            400, "Cannot edit a Purchase Order that is pending or approved."
        )

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


# ==============================================================



@router.delete("/purchase-orders/{id}")

async def delete_po(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
):
    obj = await db.get(PurchaseOrder, id)
    if not obj or obj.is_deleted:
        raise HTTPException(404, "PO not found")
    if obj.status in ["PENDING", "APPROVED"]:
        raise HTTPException(400, "Cannot delete a Purchase Order that is pending or approved.")
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
    try:

        if payload.quantity <= 0:
            raise HTTPException(
                status_code=400,
                detail="Quantity must be greater than zero",
            )

        if payload.from_project_id == payload.to_project_id:
            raise HTTPException(
                status_code=400,
                detail="Source and destination project cannot be same",
            )

        from_project = await db.get(
            Project,
            payload.from_project_id,
        )

        to_project = await db.get(
            Project,
            payload.to_project_id,
        )

        if not from_project:
            raise HTTPException(
                status_code=404,
                detail="Source project not found",
            )

        if not to_project:
            raise HTTPException(
                status_code=404,
                detail="Destination project not found",
            )

        reference_id = f"TRF-{uuid.uuid4().hex[:8].upper()}"

        # =====================================================
        # SOURCE MATERIAL
        # =====================================================

        material = await db.scalar(
            select(Material)
            .options(
                selectinload(Material.unit),
                selectinload(Material.supplier),
                selectinload(Material.material_master),
            )
            .where(
                Material.id == payload.material_id,
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        if not material:
            raise HTTPException(
                status_code=404,
                detail="Material not found",
            )

        if material.project_id != payload.from_project_id:
            raise HTTPException(
                status_code=400,
                detail="Material does not belong to source project",
            )

        if material.remaining_stock < payload.quantity:
            raise HTTPException(
                status_code=400,
                detail="Insufficient stock available",
            )

        qty_purchased = material.quantity_purchased or Decimal("0")
        total_amount = material.total_amount or Decimal("0")

        avg_rate = total_amount / qty_purchased if qty_purchased > 0 else Decimal("0")

        transfer_amount = avg_rate * payload.quantity

        # =====================================================
        # REDUCE STOCK FROM SOURCE
        # =====================================================

        material.quantity_used += payload.quantity

        update_material_fields(material)

        # =====================================================
        # FIND DESTINATION MATERIAL
        # =====================================================

        destination_material = await db.scalar(
            select(Material)
            .where(
                Material.project_id == payload.to_project_id,
                Material.material_master_id == material.material_master_id,
                Material.supplier_id == material.supplier_id,
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        # =====================================================
        # CREATE DESTINATION MATERIAL IF NOT EXISTS
        # =====================================================

        if not destination_material:

            material_code = await generate_business_id(
                db=db,
                model=Material,
                column_name="material_code",
                prefix="MAT",
            )

            destination_material = Material(
                material_code=material_code,
                project_id=payload.to_project_id,
                material_master_id=material.material_master_id,
                material_name=material.material_name,
                category=material.category,
                unit_id=material.unit_id,
                supplier_id=material.supplier_id,
                purchase_rate=avg_rate,
                rate_type=material.rate_type,
                quantity_purchased=payload.quantity,
                quantity_used=Decimal("0"),
                total_amount=transfer_amount,
                payment_given=Decimal("0"),
                payment_pending=Decimal("0"),
                minimum_stock_level=material.minimum_stock_level,
            )

            update_material_fields(destination_material)

            db.add(destination_material)

            await db.flush()

        else:

            destination_material.quantity_purchased += payload.quantity

            destination_material.total_amount += transfer_amount

            update_material_fields(destination_material)

        # =====================================================
        # MATERIAL TRANSACTION
        # =====================================================

        tx_out = MaterialTransaction(
            material_id=material.id,
            project_id=payload.from_project_id,
            type=TransactionType.TRANSFER_OUT,
            quantity=-payload.quantity,
            rate=avg_rate,
            total_amount=transfer_amount,
            issue_type=IssueType.TRANSFER,
            reference_id=reference_id,
        )

        tx_in = MaterialTransaction(
            material_id=destination_material.id,
            project_id=payload.to_project_id,
            type=TransactionType.TRANSFER_IN,
            quantity=payload.quantity,
            rate=avg_rate,
            total_amount=transfer_amount,
            issue_type=IssueType.TRANSFER,
            reference_id=reference_id,
        )

        db.add(tx_out)
        db.add(tx_in)

        # =====================================================
        # MATERIAL LEDGER
        # =====================================================

        ledger_out = MaterialLedger(
            material_id=material.id,
            project_id=payload.from_project_id,
            type=TransactionType.TRANSFER_OUT,
            quantity=-payload.quantity,
            rate=avg_rate,
            total_amount=transfer_amount,
            issue_type=IssueType.TRANSFER,
            reference_id=reference_id,
        )

        ledger_in = MaterialLedger(
            material_id=destination_material.id,
            project_id=payload.to_project_id,
            type=TransactionType.TRANSFER_IN,
            quantity=payload.quantity,
            rate=avg_rate,
            total_amount=transfer_amount,
            issue_type=IssueType.TRANSFER,
            reference_id=reference_id,
        )

        db.add(ledger_out)
        db.add(ledger_in)

        # =====================================================
        # TRANSFER RECORD
        # =====================================================

        transfer = MaterialTransfer(
            material_id=material.id,
            from_project_id=payload.from_project_id,
            to_project_id=payload.to_project_id,
            quantity=payload.quantity,
            status="COMPLETED",
            reference_id=reference_id,
        )

        db.add(transfer)

        await db.commit()

        await db.refresh(transfer)

        await bump_cache_version(
            redis,
            VERSION_KEY,
        )

        return build_transfer_response(
            transfer,
            material,
            from_project,
            to_project,
        )

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise


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

    obj = await db.scalar(
        select(Material)
        .options(
            selectinload(Material.supplier),
            selectinload(Material.unit),
            selectinload(Material.material_master),
        )
        .where(
            Material.id == material_id,
            Material.is_deleted == False,
        )
        .with_for_update()
    )

    if not obj:
        raise HTTPException(404, "Material not found")

    qty = Decimal(str(data.quantity))

    if qty <= 0:
        raise HTTPException(400, "Quantity must be greater than 0")

    purchased = obj.quantity_purchased or Decimal("0")
    used = obj.quantity_used or Decimal("0")

    current_stock = purchased - used

    if current_stock <= 0:
        raise HTTPException(400, "Stock exhausted")

    if qty > current_stock:
        raise HTTPException(
            400,
            f"Insufficient stock. Available: {current_stock}",
        )

    total_amount_current = obj.total_amount or Decimal("0")

    avg_rate = total_amount_current / purchased if purchased > 0 else Decimal("0")

    used_value = qty * avg_rate

    reference = f"USE-{uuid.uuid4().hex[:8]}"
    issue_type = data.issue_type or IssueType.SYSTEM

    try:

        transaction = MaterialTransaction(
            material_id=obj.id,
            type=DBTransactionType.USAGE,
            quantity=-qty,
            rate=avg_rate,
            total_amount=used_value,
            amount_paid=Decimal("0"),
            payment_pending=Decimal("0"),
            issue_type=issue_type,
            project_id=data.project_id,
            task_id=data.task_id,
            remarks="Material used",
            reference_id=reference,
        )

        ledger = MaterialLedger(
            material_id=obj.id,
            type=DBTransactionType.USAGE,
            quantity=-qty,
            rate=avg_rate,
            total_amount=used_value,
            amount_paid=Decimal("0"),
            payment_pending=Decimal("0"),
            issue_type=issue_type,
            project_id=data.project_id,
            remarks="Material used",
            reference_id=reference,
        )

        usage_entry = MaterialUsage(
            material_id=obj.id,
            project_id=data.project_id,
            task_id=data.task_id,
            quantity_used=qty,
            usage_date=datetime.utcnow(),
        )

        db.add(transaction)
        db.add(ledger)
        db.add(usage_entry)

        obj.quantity_used = used + qty

        update_material_fields(obj)

        await db.commit()

        await db.refresh(obj)

    except Exception:
        await db.rollback()
        raise

    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier
    master = obj.material_master

    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(
        0,
        total_amount - payment_given,
    )

    extra_paid = max(
        0,
        payment_given - total_amount,
    )

    if obj.remaining_stock <= 0:
        alert_type = "OUT_OF_STOCK"
    elif obj.remaining_stock <= obj.minimum_stock_level:
        alert_type = "LOW_STOCK"
    else:
        alert_type = "IN_STOCK"

    if alert_type in ["LOW_STOCK", "OUT_OF_STOCK"]:

        pm = await db.scalar(
            select(proj_model.ProjectMember.user_id)
            .join(
                User,
                User.id == proj_model.ProjectMember.user_id,
            )
            .where(
                proj_model.ProjectMember.project_id == data.project_id,
                User.role == UserRole.PROJECT_MANAGER.value,
            )
            .limit(1)
        )

        if pm:
            await create_notification(
                db=db,
                user_id=pm,
                title=f"Material Alert: {alert_type.replace('_', ' ')}",
                message=(
                    f"Stock for {obj.material_name} "
                    f"is now {obj.remaining_stock} "
                    f"{obj.unit.name if obj.unit else ''}"
                ),
                type="alert",
            )

            await db.commit()

    return MaterialOut(
        id=obj.id,
        material_code=obj.material_code,
        project_id=obj.project_id,
        material_master_id=obj.material_master_id,
        material_master_name=master.name if master else None,
        material_master_brand=master.brand if master else None,
        material_master_specification=master.specification if master else None,
        material_master_hsn_code=master.hsn_code if master else None,
        material_name=obj.material_name,
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=obj.unit.name if obj.unit else "",
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
        minimum_stock_level=float(obj.minimum_stock_level),
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
    from sqlalchemy.orm import selectinload

    try:
        obj = await db.scalar(
            select(Material)
            .options(
                selectinload(Material.supplier),
                selectinload(Material.unit),
                selectinload(Material.material_master),
            )
            .where(
                Material.id == material_id,
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        if not obj:
            raise HTTPException(404, "Material not found")

        qty = Decimal(str(data.quantity))
        rate = Decimal(str(data.rate))
        paid = Decimal(str(data.amount_paid or 0))

        if qty <= 0:
            raise HTTPException(400, "Quantity must be > 0")

        if rate <= 0:
            raise HTTPException(400, "Rate must be > 0")

        if paid < 0:
            raise HTTPException(400, "Payment cannot be negative")

        old_qty = obj.quantity_purchased or Decimal("0")
        old_rate = obj.purchase_rate or Decimal("0")

        if old_qty > 0:
            new_rate = ((old_qty * old_rate) + (qty * rate)) / (old_qty + qty)
        else:
            new_rate = rate

        obj.purchase_rate = new_rate.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        total = (qty * rate).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )

        payment_pending = max(
            Decimal("0"),
            total - paid,
        )

        reference = f"PUR-{uuid.uuid4().hex[:8]}"

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

        obj.quantity_purchased = (obj.quantity_purchased or Decimal("0")) + qty

        obj.remaining_stock = (obj.remaining_stock or Decimal("0")) + qty

        obj.payment_given = (obj.payment_given or Decimal("0")) + paid

        obj.total_amount = (obj.total_amount or Decimal("0")) + total

        obj.payment_pending = max(
            Decimal("0"),
            obj.total_amount - obj.payment_given,
        )

        update_material_fields(obj)

        await db.commit()

        result = await db.execute(
            select(Material)
            .options(
                selectinload(Material.supplier),
                selectinload(Material.unit),
                selectinload(Material.material_master),
            )
            .where(Material.id == obj.id)
        )

        obj = result.scalar_one()

    except Exception:
        await db.rollback()
        raise

    await bump_cache_version(redis, VERSION_KEY)

    supplier = obj.supplier
    unit = obj.unit
    master = obj.material_master

    total_amount = float(obj.total_amount or 0)
    payment_given = float(obj.payment_given or 0)

    payment_pending = max(
        0,
        total_amount - payment_given,
    )

    extra_paid = max(
        0,
        payment_given - total_amount,
    )

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
        material_master_id=obj.material_master_id,
        material_master_name=master.name if master else None,
        material_master_brand=master.brand if master else None,
        material_master_specification=master.specification if master else None,
        material_master_hsn_code=master.hsn_code if master else None,
        material_name=obj.material_name.strip().title(),
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=unit.name if unit else "",
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


# ================= ADD INVENTORY =================


@router.post(
    "/inventory",
    response_model=InventoryAdjustResponse,
)
async def adjust_inventory(
    payload: InventoryAdjustRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_WRITE_ROLES)),
    redis=Depends(get_request_redis),
):

    material_id = payload.material_id

    reason = " ".join((payload.reason or "").strip().split())

    try:
        new_stock = Decimal(str(payload.new_stock)).quantize(Decimal("0.001"))

    except (InvalidOperation, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Invalid stock value",
        )

    # ================= VALIDATIONS =================

    if new_stock < 0:
        raise HTTPException(
            status_code=400,
            detail="Stock cannot be negative",
        )

    if new_stock > Decimal("999999999"):
        raise HTTPException(
            status_code=400,
            detail="Stock value too large",
        )

    if not reason:
        raise HTTPException(
            status_code=400,
            detail="Reason is required",
        )

    if len(reason) > 500:
        raise HTTPException(
            status_code=400,
            detail="Reason too long",
        )

    try:
        material = await db.scalar(
            select(Material)
            .where(
                Material.id == material_id,
                Material.is_deleted == False,
            )
            .with_for_update()
        )

        if not material:
            raise HTTPException(
                status_code=404,
                detail="Material not found",
            )

        old_stock = material.remaining_stock or Decimal("0")

        diff = new_stock - old_stock

        if diff == 0:
            raise HTTPException(
                status_code=400,
                detail="No stock change detected",
            )

        reference = f"ADJ-{uuid.uuid4().hex[:8]}"

        qty_purchased = material.quantity_purchased or Decimal("0")

        total_amt = material.total_amount or Decimal("0")

        avg_rate = (
            total_amt / qty_purchased
            if qty_purchased > 0
            else (material.purchase_rate or Decimal("0"))
        )

        # ================= STOCK RECONCILIATION =================

        if diff > 0:
            # Physical stock found extra

            material.quantity_purchased += diff

            # Increase inventory valuation
            material.total_amount += diff * avg_rate

        else:
            # Physical stock less than system stock

            material.quantity_used += abs(diff)

            # DO NOT reduce total_amount
            # total_amount = historical purchase cost

        # ================= SET ACTUAL STOCK =================

        material.remaining_stock = new_stock

        # ================= PAYMENT FIELDS =================

        material.payment_pending = max(
            (material.total_amount or Decimal("0"))
            - (material.payment_given or Decimal("0")),
            Decimal("0"),
        )

        material.advance_amount = max(
            (material.payment_given or Decimal("0"))
            - (material.total_amount or Decimal("0")),
            Decimal("0"),
        )

        audit_remark = f"Stock adjusted: " f"{old_stock} → {new_stock} | {reason}"

        adjustment_total = abs(diff) * avg_rate

        # ================= TRANSACTION =================

        db.add(
            MaterialTransaction(
                material_id=material.id,
                type=DBTransactionType.ADJUSTMENT,
                quantity=diff,
                rate=avg_rate,
                total_amount=adjustment_total,
                amount_paid=0,
                payment_pending=0,
                issue_type=IssueType.SYSTEM,
                project_id=material.project_id,
                remarks=audit_remark,
                reference_id=reference,
            )
        )

        # ================= LEDGER =================

        db.add(
            MaterialLedger(
                material_id=material.id,
                type=DBTransactionType.ADJUSTMENT,
                quantity=diff,
                rate=avg_rate,
                total_amount=adjustment_total,
                amount_paid=0,
                payment_pending=0,
                project_id=material.project_id,
                remarks=audit_remark,
                reference_id=reference,
            )
        )

        await db.commit()
        await db.refresh(material)

    except HTTPException:
        await db.rollback()
        raise

    except Exception:
        await db.rollback()
        raise

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    return InventoryAdjustResponse(
        material_id=material.id,
        material_name=material.material_name,
        old_stock=float(old_stock),
        new_stock=float(material.remaining_stock),
        difference=float(diff),
        avg_rate=float(avg_rate),
        reason=reason,
        reference_id=reference,
        message="Inventory adjusted successfully",
    )


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


from sqlalchemy.orm import selectinload
from decimal import Decimal
from typing import Optional


@router.get(
    "/reports",
    response_model=MaterialReportResponse,
)
async def material_report(
    project_id: int = Query(...),
    supplier_id: Optional[int] = None,
    material_id: Optional[int] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)

    query = (
        select(Material)
        .options(
            selectinload(Material.unit),
            selectinload(Material.supplier),
            selectinload(Material.material_master),
        )
        .where(Material.is_deleted == False)
    )

    query = query.where(Material.project_id == project_id)

    if supplier_id:
        query = query.where(Material.supplier_id == supplier_id)

    if category:
        query = query.where(func.lower(Material.category) == category.lower())

    if material_id:
        query = query.where(Material.id == material_id)

    query = query.order_by(Material.id.desc()).offset(skip).limit(limit)

    materials = (await db.execute(query)).scalars().all()

    report_rows = []

    total_purchased = Decimal("0")
    total_used = Decimal("0")
    total_remaining = Decimal("0")
    total_stock_value = Decimal("0")
    total_payment_given = Decimal("0")
    total_payment_pending = Decimal("0")

    in_stock_count = 0
    low_stock_count = 0
    out_of_stock_count = 0

    for m in materials:

        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amount = m.total_amount or Decimal("0")

        avg_rate = total_amount / purchased if purchased > 0 else Decimal("0")

        stock_value = remaining * avg_rate

        if remaining == 0:
            alert_type = "OUT_OF_STOCK"
            out_of_stock_count += 1

        elif remaining <= (m.minimum_stock_level or Decimal("0")):
            alert_type = "LOW_STOCK"
            low_stock_count += 1

        else:
            alert_type = "IN_STOCK"
            in_stock_count += 1

        total_purchased += purchased
        total_used += used
        total_remaining += remaining
        total_stock_value += stock_value

        total_payment_given += m.payment_given or Decimal("0")

        total_payment_pending += m.payment_pending or Decimal("0")

        report_rows.append(
            MaterialReport(
                material_id=m.id,
                material_code=m.material_code,
                material_master_id=m.material_master_id,
                material_master_name=(
                    m.material_master.name if m.material_master else None
                ),
                material_master_brand=(
                    m.material_master.brand if m.material_master else None
                ),
                material_master_specification=(
                    m.material_master.specification if m.material_master else None
                ),
                material_master_hsn_code=(
                    m.material_master.hsn_code if m.material_master else None
                ),
                material_name=m.material_name,
                category=m.category,
                unit_id=m.unit_id,
                unit_name=(m.unit.name if m.unit else None),
                supplier_id=m.supplier_id,
                supplier_name=(m.supplier.supplier_name if m.supplier else None),
                project_id=m.project_id,
                total_purchased=float(purchased),
                total_used=float(used),
                remaining_stock=float(remaining),
                avg_rate=float(avg_rate),
                stock_value=float(stock_value),
                payment_given=float(m.payment_given or 0),
                payment_pending=float(m.payment_pending or 0),
                minimum_stock_level=float(m.minimum_stock_level or 0),
                alert_type=alert_type,
            )
        )

    return MaterialReportResponse(
        summary=MaterialReportSummary(
            total_materials=len(report_rows),
            total_purchased=float(total_purchased),
            total_used=float(total_used),
            total_remaining=float(total_remaining),
            total_stock_value=float(total_stock_value),
            total_payment_given=float(total_payment_given),
            total_payment_pending=float(total_payment_pending),
            in_stock_count=in_stock_count,
            low_stock_count=low_stock_count,
            out_of_stock_count=out_of_stock_count,
        ),
        materials=report_rows,
    )


# ======================PDF REPORT=============================================

import os
import uuid
import tempfile
from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Query, Depends, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.db.session import get_db_session
from app.models.material import Material, Supplier
from app.models.master_data import Unit
from app.models.project import Project
from app.models.user import User

# ─────────────────────────── SIMPLE COLOR PALETTE ─────────────────────────

PRIMARY_DARK = colors.HexColor("#1F2937")
PRIMARY_BLUE = colors.HexColor("#2563EB")
TEXT_DARK = colors.HexColor("#1F2937")
TEXT_GRAY = colors.HexColor("#6B7280")
BORDER_LIGHT = colors.HexColor("#D1D5DB")
HEADER_BG = colors.HexColor("#1F2937")
ZEBRA_BG = colors.HexColor("#F3F4F6")
CARD_BG = colors.HexColor("#FFFFFF")

STATUS_OK_FG = colors.HexColor("#166534")
STATUS_LOW_FG = colors.HexColor("#92400E")
STATUS_OUT_FG = colors.HexColor("#991B1B")
STATUS_OK_BG = colors.HexColor("#DCFCE7")
STATUS_LOW_BG = colors.HexColor("#FEF3C7")
STATUS_OUT_BG = colors.HexColor("#FEE2E2")


# ─────────────────────────── FORMATTING HELPERS ────────────────────────────
def fmt(val, dec: int = 2) -> str:
    try:
        val = val or Decimal("0")
        return f"{float(val):,.{dec}f}"
    except Exception:
        return "0"


def rs(val, dec: int = 2) -> str:
    return f"\u20b9 {fmt(val, dec)}"


def safe_delete(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def alert_to_status(alert_type: str) -> str:
    mapping = {"OUT_OF_STOCK": "OUT", "LOW_STOCK": "LOW", "IN_STOCK": "OK"}
    return mapping.get((alert_type or "").upper(), "OK")


def compute_avg_rate(total_amount, quantity_purchased) -> Decimal:
    """Weighted average cost per unit = total purchase cost / total qty purchased.
    This is the correct basis for stock valuation (NOT material.purchase_rate,
    which only reflects a single/last rate and ignores price changes across POs)."""
    total_amount = total_amount or Decimal("0")
    quantity_purchased = quantity_purchased or Decimal("0")
    if quantity_purchased > 0:
        return total_amount / quantity_purchased
    return Decimal("0")


# ─────────────────────────── PDF STYLE FACTORY ───────────────────────────────
def _styles():
    return {
        "title": ParagraphStyle(
            "title",
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=PRIMARY_DARK,
            leading=22,
            alignment=1,
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            fontName="Helvetica",
            fontSize=9.5,
            textColor=TEXT_GRAY,
            leading=12,
            alignment=1,
        ),
        "section": ParagraphStyle(
            "section",
            fontName="Helvetica-Bold",
            fontSize=10.5,
            textColor=PRIMARY_DARK,
            leading=13,
            leftIndent=2,
        ),
        "th": ParagraphStyle(
            "th",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            leading=9.5,
            alignment=0,
        ),
        "th_c": ParagraphStyle(
            "th_c",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            leading=9.5,
            alignment=1,
        ),
        "th_r": ParagraphStyle(
            "th_r",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            leading=9.5,
            alignment=2,
        ),
        "td": ParagraphStyle(
            "td",
            fontName="Helvetica",
            fontSize=8,
            textColor=TEXT_DARK,
            leading=10,
            alignment=0,
        ),
        "td_c": ParagraphStyle(
            "td_c",
            fontName="Helvetica",
            fontSize=8,
            textColor=TEXT_DARK,
            leading=10,
            alignment=1,
        ),
        "td_r": ParagraphStyle(
            "td_r",
            fontName="Helvetica",
            fontSize=8,
            textColor=TEXT_DARK,
            leading=10,
            alignment=2,
        ),
        "td_bold_r": ParagraphStyle(
            "td_bold_r",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=PRIMARY_DARK,
            leading=10,
            alignment=2,
        ),
        "td_bold_l": ParagraphStyle(
            "td_bold_l",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=PRIMARY_DARK,
            leading=10,
            alignment=0,
        ),
        "card_label": ParagraphStyle(
            "card_label",
            fontName="Helvetica",
            fontSize=7.3,
            textColor=TEXT_GRAY,
            leading=9,
            alignment=1,
        ),
        "card_value": ParagraphStyle(
            "card_value",
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=PRIMARY_BLUE,
            leading=15,
            alignment=1,
        ),
    }


def _section_header(title: str, s, doc_width: float) -> Table:
    t = Table([[Paragraph(title, s["section"])]], colWidths=[doc_width])
    t.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 1.2, PRIMARY_BLUE),
            ]
        )
    )
    return t


def _status_badge(status: str) -> Table:
    status_config = {
        "OK": (STATUS_OK_FG, STATUS_OK_BG, "IN STOCK"),
        "LOW": (STATUS_LOW_FG, STATUS_LOW_BG, "LOW STOCK"),
        "OUT": (STATUS_OUT_FG, STATUS_OUT_BG, "OUT OF STOCK"),
    }
    fg, bg, label = status_config.get(status, status_config["OK"])
    ps = ParagraphStyle(
        f"badge_{status}",
        fontName="Helvetica-Bold",
        fontSize=6.3,
        textColor=fg,
        leading=8,
        alignment=1,
    )
    t = Table([[Paragraph(label, ps)]], colWidths=[15.5 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return t


def _draw_simple_header(canvas_obj, doc):
    canvas_obj.saveState()
    w, h = A4

    # Single flat header bar - no double-stacked bands
    canvas_obj.setFillColor(PRIMARY_DARK)
    canvas_obj.rect(0, h - 40, w, 40, fill=1, stroke=0)

    canvas_obj.setFillColor(colors.white)
    canvas_obj.setFont("Helvetica-Bold", 14)
    canvas_obj.drawString(18 * mm, h - 25, "INFRA PILOT")

    canvas_obj.setFillColor(colors.HexColor("#9CA3AF"))
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.drawString(18 * mm, h - 34, "Material Inventory Management")

    ts = datetime.utcnow().strftime("%d %b %Y, %H:%M UTC")
    canvas_obj.setFillColor(colors.HexColor("#D1D5DB"))
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.drawRightString(w - 18 * mm, h - 25, ts)

    if doc.page == 1:
        title_y = h - 40 - 22
        canvas_obj.setFillColor(PRIMARY_DARK)
        canvas_obj.setFont("Helvetica-Bold", 15)
        canvas_obj.drawCentredString(w / 2, title_y, "Material Inventory Report")

        proj_line = getattr(doc, "_project_line", None)
        sub_y = title_y - 14
        if proj_line:
            canvas_obj.setFillColor(PRIMARY_BLUE)
            canvas_obj.setFont("Helvetica-Bold", 9)
            canvas_obj.drawCentredString(w / 2, sub_y, proj_line)
            sub_y -= 12

        canvas_obj.setFillColor(TEXT_GRAY)
        canvas_obj.setFont("Helvetica", 8)
        canvas_obj.drawCentredString(
            w / 2, sub_y, datetime.utcnow().strftime("%d %B %Y")
        )

    # Simple footer - one line only
    canvas_obj.setStrokeColor(BORDER_LIGHT)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(18 * mm, 18, w - 18 * mm, 18)

    canvas_obj.setFillColor(TEXT_GRAY)
    canvas_obj.setFont("Helvetica", 6.5)
    canvas_obj.drawString(18 * mm, 8, "Confidential \u2022 Infra Pilot")
    canvas_obj.drawRightString(w - 18 * mm, 8, f"Page {doc.page}")

    canvas_obj.restoreState()


# ─────────────────────────── PDF BUILDER ──────────────────────────────────
def _build_pdf(
    file_path: str,
    rows: list,
    project_name: Optional[str] = None,
    project_code: Optional[str] = None,
):
    doc = BaseDocTemplate(
        file_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=125,
        bottomMargin=28,
    )

    if project_name or project_code:
        parts = [
            p
            for p in (project_name, f"[{project_code}]" if project_code else None)
            if p
        ]
        doc._project_line = " \u2022 ".join(parts)
    else:
        doc._project_line = None

    frame = Frame(
        doc.leftMargin,
        doc.bottomMargin,
        doc.width,
        doc.height,
        id="main",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    doc.addPageTemplates(
        [PageTemplate(id="main", frames=frame, onPage=_draw_simple_header)]
    )

    s = _styles()
    DW = doc.width
    elements = []

    # ── PROCESS DATA (single source of truth for avg_rate / stock value) ───
    totals = {
        k: Decimal("0")
        for k in (
            "purchased",
            "used",
            "remaining",
            "value",
            "pending",
            "given",
            "advance",
            "purchase_cost",
        )
    }
    processed = []
    alerts_out, alerts_low = [], []

    for m, sup, unit_name in rows:
        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        pending = m.payment_pending or Decimal("0")
        given = m.payment_given or Decimal("0")
        advance = m.advance_amount or Decimal("0")

        # Stock valuation basis: WEIGHTED AVERAGE COST (total_amount / qty purchased).
        # Intentionally NOT material.purchase_rate, which is just one rate and
        # doesn't reflect blended cost across multiple purchase orders.
        avg_rate = compute_avg_rate(total_amt, purchased)
        value = remaining * avg_rate

        for key, val in [
            ("purchased", purchased),
            ("used", used),
            ("remaining", remaining),
            ("value", value),
            ("pending", pending),
            ("given", given),
            ("advance", advance),
            ("purchase_cost", total_amt),
        ]:
            totals[key] += val

        status = alert_to_status(m.alert_type)
        if status == "OUT":
            alerts_out.append((m.material_name or "").title())
        elif status == "LOW":
            alerts_low.append((m.material_name or "").title())

        processed.append(
            (m, sup, unit_name, purchased, used, remaining, avg_rate, value, status)
        )

    # ── SUMMARY (single row of 5 plain cards, no double row) ───────────────
    elements.append(_section_header("SUMMARY", s, DW))
    elements.append(Spacer(1, 8))

    card_configs = [
        ("MATERIALS", str(len(rows)), PRIMARY_DARK),
        ("STOCK VALUE", rs(totals["value"], 0), PRIMARY_BLUE),
        ("PENDING PAYMENT", rs(totals["pending"], 0), STATUS_OUT_FG),
        ("LOW STOCK", str(len(alerts_low)), STATUS_LOW_FG),
        ("OUT OF STOCK", str(len(alerts_out)), STATUS_OUT_FG),
    ]

    def _create_card(label, value, color, w):
        lbl_ps = ParagraphStyle(
            f"cl_{label[:4]}",
            fontName="Helvetica",
            fontSize=7,
            textColor=TEXT_GRAY,
            leading=8.5,
            alignment=1,
        )
        val_ps = ParagraphStyle(
            f"cv_{label[:4]}",
            fontName="Helvetica-Bold",
            fontSize=12,
            textColor=color,
            leading=15,
            alignment=1,
        )
        t = Table(
            [[Paragraph(label, lbl_ps)], [Paragraph(value, val_ps)]], colWidths=[w]
        )
        t.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, BORDER_LIGHT),
                    ("TOPPADDING", (0, 0), (-1, 0), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                    ("TOPPADDING", (0, 1), (-1, 1), 1),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
                ]
            )
        )
        return t

    gap = 4
    card_w = (DW - gap * (len(card_configs) - 1)) / len(card_configs)
    cells, widths = [], []
    for i, cfg in enumerate(card_configs):
        cells.append(_create_card(*cfg, card_w))
        widths.append(card_w)
        if i < len(card_configs) - 1:
            cells.append("")
            widths.append(gap)
    elements.append(
        Table(
            [cells],
            colWidths=widths,
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]),
        )
    )
    elements.append(Spacer(1, 16))

    # ── MATERIAL DETAILS TABLE (cleaned up: clearer columns, less crowding) ─
    elements.append(_section_header("MATERIAL DETAILS", s, DW))
    elements.append(Spacer(1, 8))

    col_spec = [
        ("#", 4, s["th_c"]),
        ("Material", 21, s["th"]),
        ("Unit", 7, s["th_c"]),
        ("Supplier", 17, s["th"]),
        ("Purchased", 11, s["th_r"]),
        ("Used", 9, s["th_r"]),
        ("Remaining", 11, s["th_r"]),
        ("Avg Rate", 10, s["th_r"]),
        ("Stock Value", 12, s["th_r"]),
        ("Pending", 11, s["th_r"]),
        ("Status", 17, s["th_c"]),
    ]
    total_units = sum(c[1] for c in col_spec)
    col_widths = [c[1] * DW / total_units for c in col_spec]
    headers = [Paragraph(c[0], c[2]) for c in col_spec]
    tdata = [headers]

    for i, item in enumerate(processed):
        m, sup, unit_name, purchased, used, remaining, avg_rate, value, status = item
        tdata.append(
            [
                Paragraph(str(i + 1), s["td_c"]),
                Paragraph((m.material_name or "").title(), s["td"]),
                Paragraph(unit_name or "\u2014", s["td_c"]),
                Paragraph(sup or "N/A", s["td"]),
                Paragraph(fmt(purchased, 1), s["td_r"]),
                Paragraph(fmt(used, 1), s["td_r"]),
                Paragraph(fmt(remaining, 1), s["td_r"]),
                Paragraph(fmt(avg_rate, 2), s["td_r"]),
                Paragraph(fmt(value, 2), s["td_r"]),
                Paragraph(fmt(m.payment_pending, 2), s["td_r"]),
                _status_badge(status),
            ]
        )

    frow = len(tdata)
    tdata.append(
        [
            "",
            Paragraph("TOTAL", s["td_bold_l"]),
            "",
            "",
            Paragraph(fmt(totals["purchased"], 1), s["td_bold_r"]),
            Paragraph(fmt(totals["used"], 1), s["td_bold_r"]),
            Paragraph(fmt(totals["remaining"], 1), s["td_bold_r"]),
            "",
            Paragraph(fmt(totals["value"], 2), s["td_bold_r"]),
            Paragraph(fmt(totals["pending"], 2), s["td_bold_r"]),
            "",
        ]
    )

    det_table = Table(tdata, colWidths=col_widths, repeatRows=1)
    det_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("ROWBACKGROUNDS", (0, 1), (-1, frow - 1), [CARD_BG, ZEBRA_BG]),
                ("BACKGROUND", (0, frow), (-1, frow), colors.HexColor("#E5E7EB")),
                ("LINEABOVE", (0, frow), (-1, frow), 1, PRIMARY_DARK),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.5, PRIMARY_DARK),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (10, 0), (10, -1), "CENTER"),
            ]
        )
    )
    elements.append(det_table)
    elements.append(Spacer(1, 16))

    # ── CRITICAL ALERTS (if any) ──────────────────────────────────────────
    if alerts_out or alerts_low:
        elements.append(_section_header("CRITICAL STOCK ALERTS", s, DW))
        elements.append(Spacer(1, 8))

        alert_th = ParagraphStyle(
            "ath",
            fontName="Helvetica-Bold",
            fontSize=8,
            textColor=colors.white,
            leading=10,
        )
        alert_td = ParagraphStyle(
            "atd", fontName="Helvetica", fontSize=8, textColor=TEXT_DARK, leading=10
        )

        a_data = [
            [
                Paragraph("Status", alert_th),
                Paragraph("Material", alert_th),
                Paragraph("Action", alert_th),
            ]
        ]
        for name in alerts_out:
            a_data.append(
                [
                    _status_badge("OUT"),
                    Paragraph(name, alert_td),
                    Paragraph("Immediate replenishment required", alert_td),
                ]
            )
        for name in alerts_low:
            a_data.append(
                [
                    _status_badge("LOW"),
                    Paragraph(name, alert_td),
                    Paragraph("Schedule reorder soon", alert_td),
                ]
            )

        a_cw = [24 * mm, DW * 0.38, DW - 24 * mm - DW * 0.38]
        a_table = Table(a_data, colWidths=a_cw, repeatRows=1)
        a_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                    ("GRID", (0, 0), (-1, -1), 0.3, BORDER_LIGHT),
                    ("BOX", (0, 0), (-1, -1), 0.5, PRIMARY_DARK),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [CARD_BG, ZEBRA_BG]),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        elements.append(a_table)
        elements.append(Spacer(1, 16))

    # ── PAYMENT SUMMARY ───────────────────────────────────────────────────
    elements.append(_section_header("PAYMENT SUMMARY", s, DW))
    elements.append(Spacer(1, 8))

    pay_configs = [
        ("PURCHASE COST", rs(totals["purchase_cost"], 2), PRIMARY_DARK),
        ("PAID", rs(totals["given"], 2), STATUS_OK_FG),
        ("PENDING", rs(totals["pending"], 2), STATUS_OUT_FG),
        ("ADVANCE", rs(totals["advance"], 2), STATUS_LOW_FG),
    ]
    pay_gap = 5
    pay_card_w = (DW - pay_gap * 3) / 4

    def _pay_card(label, value, color):
        lbl_ps = ParagraphStyle(
            f"pl_{label[:3]}",
            fontName="Helvetica",
            fontSize=7,
            textColor=TEXT_GRAY,
            leading=9,
            alignment=1,
        )
        val_ps = ParagraphStyle(
            f"pv_{label[:3]}",
            fontName="Helvetica-Bold",
            fontSize=11.5,
            textColor=color,
            leading=15,
            alignment=1,
        )
        t = Table(
            [[Paragraph(label, lbl_ps)], [Paragraph(value, val_ps)]],
            colWidths=[pay_card_w],
        )
        t.setStyle(
            TableStyle(
                [
                    ("BOX", (0, 0), (-1, -1), 0.6, BORDER_LIGHT),
                    ("TOPPADDING", (0, 0), (-1, 0), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
                    ("TOPPADDING", (0, 1), (-1, 1), 1),
                    ("BOTTOMPADDING", (0, 1), (-1, 1), 9),
                ]
            )
        )
        return t

    cells, widths = [], []
    for i, cfg in enumerate(pay_configs):
        cells.append(_pay_card(*cfg))
        widths.append(pay_card_w)
        if i < len(pay_configs) - 1:
            cells.append("")
            widths.append(pay_gap)
    elements.append(
        Table(
            [cells],
            colWidths=widths,
            style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")]),
        )
    )

    doc.build(elements)


# ─────────────────────────── PDF ENDPOINT ─────────────────────────────────
@router.get("/reports/pdf", response_class=FileResponse)
async def export_pdf(
    project_id: int = Query(...),
    category: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    material_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    try:
        query = (
            select(Material, Supplier.supplier_name, Unit.name)
            .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
            .join(Unit, Unit.id == Material.unit_id, isouter=True)
            .where(Material.is_deleted == False, Material.project_id == project_id)
        )
        if category:
            query = query.where(func.lower(Material.category) == category.lower())
        if supplier_id:
            query = query.where(Material.supplier_id == supplier_id)
        if material_id:
            query = query.where(Material.id == material_id)

        rows = (await db.execute(query.order_by(Material.id.desc()))).all()
        if not rows:
            raise HTTPException(status_code=404, detail="No material data found")

        project = await db.get(Project, project_id)
        project_name = project.project_name if project else None

        file_path = os.path.join(
            tempfile.gettempdir(), f"material_report_{uuid.uuid4()}.pdf"
        )
        try:
            _build_pdf(file_path=file_path, rows=rows, project_name=project_name)
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"PDF generation failed: {str(e)}"
            )

        return FileResponse(
            path=file_path,
            filename="material_report.pdf",
            media_type="application/pdf",
            background=BackgroundTask(safe_delete, file_path),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Material report error: {str(e)}")


# ─────────────────────────── EXCEL ENDPOINT ──────────────────────────────
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter


@router.get("/reports/excel", response_class=FileResponse)
async def export_excel(
    project_id: int = Query(...),
    category: Optional[str] = Query(None),
    supplier_id: Optional[int] = Query(None),
    material_id: Optional[int] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    try:
        project = await db.get(Project, project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        query = (
            select(Material, Supplier.supplier_name, Unit.name)
            .outerjoin(Supplier, Supplier.id == Material.supplier_id)
            .outerjoin(Unit, Unit.id == Material.unit_id)
            .where(Material.is_deleted == False, Material.project_id == project_id)
            .order_by(Material.id.desc())
        )
        if category:
            query = query.where(func.lower(Material.category) == category.lower())
        if supplier_id:
            query = query.where(Material.supplier_id == supplier_id)
        if material_id:
            query = query.where(Material.id == material_id)

        rows = (await db.execute(query)).all()
        if not rows:
            raise HTTPException(status_code=404, detail="No material data found")

        wb = Workbook()
        ws = wb.active
        ws.title = "Materials"

        border = Border(
            left=Side(style="thin", color="D1D5DB"),
            right=Side(style="thin", color="D1D5DB"),
            top=Side(style="thin", color="D1D5DB"),
            bottom=Side(style="thin", color="D1D5DB"),
        )
        header_fill = PatternFill(
            start_color="1F2937", end_color="1F2937", fill_type="solid"
        )
        header_font = Font(bold=True, color="FFFFFF", size=10)
        accent_fill = PatternFill(
            start_color="F9FAFB", end_color="F9FAFB", fill_type="solid"
        )
        title_font = Font(bold=True, size=14, color="1F2937")
        subtitle_font = Font(size=10, color="6B7280")

        # ── TITLE (simple, 2 lines only) ────────────────────────────────────
        ws.merge_cells("A1:K1")
        ws["A1"] = "MATERIAL INVENTORY REPORT"
        ws["A1"].font = title_font
        ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 22

        ws.merge_cells("A2:K2")
        ws["A2"] = (
            f"Project: {project.project_name}  |  Generated: {datetime.now().strftime('%d %b %Y %H:%M')}"
        )
        ws["A2"].font = subtitle_font
        ws["A2"].alignment = Alignment(horizontal="center")
        ws.row_dimensions[2].height = 16

        row = 4

        # ── CALCULATE TOTALS (single pass, all keys tracked correctly) ─────
        # NOTE: stock valuation uses WEIGHTED AVERAGE COST
        #   avg_rate = total_amount / quantity_purchased
        #   stock_value = remaining_stock * avg_rate
        # This is intentional — see compute_avg_rate() docstring above.
        totals = {
            k: Decimal("0")
            for k in [
                "purchased",
                "used",
                "remaining",
                "value",
                "paid",
                "pending",
                "advance",
                "purchase_cost",
            ]
        }
        status_counts = {"OK": 0, "LOW": 0, "OUT": 0}
        computed_rows = []

        for material, supplier_name, unit_name in rows:
            purchased = material.quantity_purchased or Decimal("0")
            used = material.quantity_used or Decimal("0")
            remaining = material.remaining_stock or Decimal("0")
            total_amt = material.total_amount or Decimal("0")
            avg_rate = compute_avg_rate(total_amt, purchased)
            value = remaining * avg_rate

            totals["purchased"] += purchased
            totals["used"] += used
            totals["remaining"] += remaining
            totals["value"] += value
            totals["paid"] += material.payment_given or Decimal("0")
            totals["pending"] += material.payment_pending or Decimal("0")
            totals["advance"] += material.advance_amount or Decimal("0")
            totals[
                "purchase_cost"
            ] += total_amt  # FIX: accumulate real cost, not qty*last_rate

            status = alert_to_status(material.alert_type)
            status_counts[status] += 1
            computed_rows.append((material, supplier_name, unit_name, avg_rate, value))

        # ── SUMMARY (one compact row) ───────────────────────────────────────
        ws.merge_cells(f"A{row}:D{row}")
        ws[f"A{row}"] = "INVENTORY SUMMARY"
        ws[f"A{row}"].font = Font(bold=True, size=10, color="1F2937")
        ws[f"A{row}"].fill = PatternFill(
            start_color="E5E7EB", end_color="E5E7EB", fill_type="solid"
        )
        row += 1

        summary_data = [
            ("Total Materials", len(rows), "1F2937"),
            ("Total Stock Value", f"\u20b9 {fmt(totals['value'], 2)}", "2563EB"),
            ("In Stock", status_counts["OK"], "166534"),
            ("Low Stock", status_counts["LOW"], "92400E"),
            ("Out of Stock", status_counts["OUT"], "991B1B"),
        ]
        for col, (label, value, color) in enumerate(summary_data, 1):
            ws.cell(row, col, label).font = Font(size=9, color="6B7280")
            ws.cell(row, col).alignment = Alignment(horizontal="center")
            val_cell = ws.cell(row + 1, col, value)
            val_cell.font = Font(bold=True, size=11, color=color)
            val_cell.alignment = Alignment(horizontal="center")

        row += 3

        # ── DATA TABLE (trimmed to the columns that matter) ─────────────────
        headers = [
            "ID",
            "Material",
            "Code",
            "Unit",
            "Supplier",
            "Purchased",
            "Used",
            "Remaining",
            "Avg Rate",
            "Stock Value",
            "Paid",
            "Pending",
            "Advance",
            "Alert",
        ]
        for col_num, header in enumerate(headers, 1):
            cell = ws.cell(row, col_num, header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = border
        ws.row_dimensions[row].height = 18
        header_row = row
        row += 1

        for idx, (material, supplier_name, unit_name, avg_rate, value) in enumerate(
            computed_rows
        ):
            values = [
                material.id,
                material.material_name or "-",
                material.material_code or "-",
                unit_name or "-",
                supplier_name or "-",
                float(material.quantity_purchased or 0),
                float(material.quantity_used or 0),
                float(material.remaining_stock or 0),
                float(avg_rate),
                float(value),
                float(material.payment_given or 0),
                float(material.payment_pending or 0),
                float(material.advance_amount or 0),
                material.alert_type or "-",
            ]
            fill = accent_fill if idx % 2 == 1 else None
            for col_num, val in enumerate(values, 1):
                cell = ws.cell(row, col_num, val)
                cell.border = border
                cell.alignment = Alignment(
                    horizontal="right" if col_num > 5 else "left", vertical="center"
                )
                if fill:
                    cell.fill = fill
            row += 1

        # ── TOTALS ROW (FIXED: uses accumulated totals, not stray avg_rate) ─
        total_row = row
        ws.cell(total_row, 2, "TOTAL").font = Font(bold=True, color="1F2937")
        ws.cell(total_row, 6, float(totals["purchased"])).font = Font(bold=True)
        ws.cell(total_row, 7, float(totals["used"])).font = Font(bold=True)
        ws.cell(total_row, 8, float(totals["remaining"])).font = Font(bold=True)
        ws.cell(total_row, 10, float(totals["value"])).font = Font(bold=True)
        ws.cell(total_row, 11, float(totals["paid"])).font = Font(bold=True)
        ws.cell(total_row, 12, float(totals["pending"])).font = Font(bold=True)
        ws.cell(total_row, 13, float(totals["advance"])).font = Font(bold=True)

        total_fill = PatternFill(
            start_color="F3F4F6", end_color="F3F4F6", fill_type="solid"
        )
        for col in range(1, len(headers) + 1):
            cell = ws.cell(total_row, col)
            cell.fill = total_fill
            cell.border = border
            cell.alignment = Alignment(horizontal="right" if col > 5 else "left")

        # ── FORMATTING ────────────────────────────────────────────────────
        ws.freeze_panes = f"A{header_row + 1}"
        ws.auto_filter.ref = f"A{header_row}:N{total_row - 1}"

        for col_num in range(1, len(headers) + 1):
            col_letter = get_column_letter(col_num)
            max_width = 12
            for cell in ws[col_letter]:
                try:
                    if cell.value:
                        max_width = max(max_width, len(str(cell.value)))
                except Exception:
                    pass
            ws.column_dimensions[col_letter].width = min(max_width + 2, 25)

        file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx").name
        wb.save(file_path)

        return FileResponse(
            path=file_path,
            filename=f"material_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            background=BackgroundTask(safe_delete, file_path),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Excel report error: {str(e)}")


# ===============price-history==========================


@router.get("/price-history/{material_id}", response_model=list[PriceHistoryOut])
async def price_history(
    material_id: int,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(MaterialTransaction.rate, MaterialTransaction.created_at)
        .where(
            MaterialTransaction.material_id == material_id,
            MaterialTransaction.type == DBTransactionType.PURCHASE,
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

        #  avoid float precision issue
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

    # ===== VALIDATE SUPPLIER =====
    supplier = await db.get(
        Supplier,
        payload.supplier_id,
    )

    if not supplier:
        raise HTTPException(
            status_code=404,
            detail="Supplier not found",
        )

    # ===== VALIDATE MATERIAL MASTER =====
    material_master = await db.get(
        MaterialMaster,
        payload.material_master_id,
    )

    if not material_master:
        raise HTTPException(
            status_code=404,
            detail="Material master not found",
        )

    # ===== DUPLICATE CHECK =====
    existing = await db.scalar(
        select(Material).where(
            Material.project_id == payload.project_id,
            Material.material_master_id == payload.material_master_id,
            Material.supplier_id == payload.supplier_id,
            Material.is_deleted == False,
        )
    )

    if existing:
        raise HTTPException(
            status_code=400,
            detail="Material already exists for this project & supplier",
        )

    # ===== AUTO FILL FROM MASTER =====
    data["material_name"] = material_master.name
    data["category"] = material_master.category or "GENERAL"
    data["unit_id"] = material_master.unit_id

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
        raise HTTPException(
            status_code=400,
            detail="Material already exists",
        )

    # ===== CALCULATIONS =====
    obj.total_amount = (obj.quantity_purchased * obj.purchase_rate).quantize(
        Decimal("0.01")
    )

    update_material_fields(obj)

    # ===== ALERT =====
    alert_type = get_alert_type(obj)

    # ===== REFERENCE =====
    reference = f"INIT-{uuid.uuid4().hex[:8]}"

    # ===== TRANSACTION =====
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

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    # ===== RESPONSE =====
    unit = await db.get(
        Unit,
        obj.unit_id,
    )

    response = MaterialOut.model_validate(
        {
            **obj.__dict__,
            "unit_id": obj.unit_id,
            "unit_name": unit.name if unit else "",
            "alert_type": alert_type,
        }
    )

    response.supplier_name = supplier.supplier_name
    response.material_master_name = material_master.name
    response.material_master_brand = material_master.brand
    response.material_master_specification = material_master.specification
    response.material_master_hsn_code = material_master.hsn_code

    return response


# =================list_materials=========================

from sqlalchemy.orm import selectinload


@router.get("", response_model=list[MaterialOut])
async def list_materials(
    project_id: int | None = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    query = (
        select(
            Material,
            Supplier.supplier_name,
        )
        .options(
            selectinload(Material.unit),
            selectinload(Material.material_master),
        )
        .join(
            Supplier,
            Supplier.id == Material.supplier_id,
            isouter=True,
        )
        .where(
            Material.is_deleted == False,
        )
    )

    if project_id:
        query = query.where(
            Material.project_id == project_id,
        )

    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    data = []

    for obj, supplier_name in rows:

        total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

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
                material_master_id=obj.material_master_id,
                material_master_name=(
                    obj.material_master.name if obj.material_master else None
                ),
                material_master_brand=(
                    obj.material_master.brand if obj.material_master else None
                ),
                material_master_specification=(
                    obj.material_master.specification if obj.material_master else None
                ),
                material_master_hsn_code=(
                    obj.material_master.hsn_code if obj.material_master else None
                ),
                material_name=obj.material_name,
                category=obj.category,
                unit_id=obj.unit_id,
                unit_name=unit_name or "",
                supplier_id=obj.supplier_id,
                supplier_name=supplier_name,
                purchase_rate=float(obj.purchase_rate or 0),
                rate_type=obj.rate_type,
                quantity_purchased=float(obj.quantity_purchased or 0),
                quantity_used=float(obj.quantity_used or 0),
                remaining_stock=float(obj.remaining_stock or 0),
                total_amount=round(
                    total_amount,
                    2,
                ),
                payment_given=round(
                    payment_given,
                    2,
                ),
                payment_pending=round(
                    payment_pending,
                    2,
                ),
                extra_paid=round(
                    extra_paid,
                    2,
                ),
                minimum_stock_level=float(obj.minimum_stock_level or 0),
                alert_type=alert_type,
            )
        )

    return data


# ==============get_material=================


from sqlalchemy.orm import selectinload


@router.get("/{material_id}", response_model=MaterialOut)
async def get_material(
    material_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    obj = await db.scalar(
        select(Material)
        .options(
            selectinload(Material.unit),
            selectinload(Material.material_master),
        )
        .where(
            Material.id == material_id,
            Material.is_deleted == False,
        )
    )

    if not obj:
        raise HTTPException(
            status_code=404,
            detail="Material not found",
        )

    supplier = await db.get(
        Supplier,
        obj.supplier_id,
    )

    total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

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
        material_master_id=obj.material_master_id,
        material_master_name=(
            obj.material_master.name if obj.material_master else None
        ),
        material_master_brand=(
            obj.material_master.brand if obj.material_master else None
        ),
        material_master_specification=(
            obj.material_master.specification if obj.material_master else None
        ),
        material_master_hsn_code=(
            obj.material_master.hsn_code if obj.material_master else None
        ),
        material_name=obj.material_name,
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=unit_name or "",
        supplier_id=obj.supplier_id,
        supplier_name=(supplier.supplier_name if supplier else None),
        purchase_rate=float(obj.purchase_rate or 0),
        rate_type=obj.rate_type,
        quantity_purchased=float(obj.quantity_purchased or 0),
        quantity_used=float(obj.quantity_used or 0),
        remaining_stock=float(obj.remaining_stock or 0),
        total_amount=round(
            total_amount,
            2,
        ),
        payment_given=round(
            payment_given,
            2,
        ),
        payment_pending=round(
            payment_pending,
            2,
        ),
        extra_paid=round(
            extra_paid,
            2,
        ),
        minimum_stock_level=float(obj.minimum_stock_level or 0),
        alert_type=alert_type,
    )


# =============update_material==================


from sqlalchemy.orm import selectinload


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
        .where(
            Material.id == material_id,
            Material.is_deleted == False,
        )
        .with_for_update()
    )

    if not obj:
        raise HTTPException(
            status_code=404,
            detail="Material not found",
        )

    update_data = payload.model_dump(exclude_unset=True)

    # ===== DIRECT PAYMENT BLOCK =====
    if "payment_given" in update_data:
        raise HTTPException(
            status_code=400,
            detail="Direct payment update not allowed. Use purchase API",
        )

    # ===== MATERIAL MASTER CHANGE =====
    if "material_master_id" in update_data:

        material_master = await db.get(
            MaterialMaster,
            update_data["material_master_id"],
        )

        if not material_master:
            raise HTTPException(
                status_code=404,
                detail="Material master not found",
            )

        supplier_id = update_data.get(
            "supplier_id",
            obj.supplier_id,
        )

        existing = await db.scalar(
            select(Material).where(
                Material.project_id == obj.project_id,
                Material.material_master_id == update_data["material_master_id"],
                Material.supplier_id == supplier_id,
                Material.id != obj.id,
                Material.is_deleted == False,
            )
        )

        if existing:
            raise HTTPException(
                status_code=400,
                detail="Material already exists for this project & supplier",
            )

        # ===== AUTO SYNC FROM MASTER =====
        update_data["material_name"] = material_master.name
        update_data["category"] = (
            material_master.category if material_master.category else "GENERAL"
        )
        update_data["unit_id"] = material_master.unit_id

    try:

        # ===== APPLY UPDATES =====
        for key, value in update_data.items():
            setattr(obj, key, value)

        # ===== RECALCULATE =====
        update_material_fields(obj)

        await db.commit()

    except Exception:
        await db.rollback()
        raise

    # ===== RELOAD WITH RELATIONSHIPS =====
    obj = await db.scalar(
        select(Material)
        .options(
            selectinload(Material.unit),
            selectinload(Material.material_master),
        )
        .where(
            Material.id == material_id,
            Material.is_deleted == False,
        )
    )

    await bump_cache_version(
        redis,
        VERSION_KEY,
    )

    supplier = await db.get(
        Supplier,
        obj.supplier_id,
    )

    total_amount, payment_given, payment_pending, extra_paid = calculate_fields(obj)

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
        material_master_id=obj.material_master_id,
        material_master_name=(
            obj.material_master.name if obj.material_master else None
        ),
        material_master_brand=(
            obj.material_master.brand if obj.material_master else None
        ),
        material_master_specification=(
            obj.material_master.specification if obj.material_master else None
        ),
        material_master_hsn_code=(
            obj.material_master.hsn_code if obj.material_master else None
        ),
        material_name=obj.material_name,
        category=obj.category,
        unit_id=obj.unit_id,
        unit_name=unit_name or "",
        supplier_id=obj.supplier_id,
        supplier_name=(supplier.supplier_name if supplier else None),
        purchase_rate=float(obj.purchase_rate or 0),
        rate_type=obj.rate_type,
        quantity_purchased=float(obj.quantity_purchased or 0),
        quantity_used=float(obj.quantity_used or 0),
        remaining_stock=float(obj.remaining_stock or 0),
        total_amount=round(
            total_amount,
            2,
        ),
        payment_given=round(
            payment_given,
            2,
        ),
        payment_pending=round(
            payment_pending,
            2,
        ),
        extra_paid=round(
            extra_paid,
            2,
        ),
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

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
from app.schemas.material import (
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

    logger.info(f"Total material alerts fetched: {len(rows)}")

    data = []

    for obj, supplier_name in rows:
        try:
            # Log raw database values
            logger.info(f"""
VALIDATING MATERIAL ALERT
-----------------------------------
Material ID         : {obj.id}
Material Name       : {obj.material_name}
Supplier Name       : {supplier_name}
Quantity Purchased  : {obj.quantity_purchased}
Quantity Used       : {obj.quantity_used}
Remaining Stock     : {obj.remaining_stock}
Minimum Stock Level : {obj.minimum_stock_level}
Threshold Param     : {threshold}
-----------------------------------
""")

            # Build response
            response = build_material_response(obj, supplier_name)

            # Override alert type for threshold-based near-low condition
            if (
                threshold is not None
                and response.alert_type == "IN_STOCK"
                and response.remaining_stock <= threshold
            ):
                response.alert_type = "NEAR_LOW"

            # Validate explicitly against response model
            validated = MaterialOut.model_validate(response)

            logger.info(
                f"Material ID {obj.id} validated successfully. "
                f"Alert Type: {validated.alert_type}"
            )

            data.append(validated)

        except Exception as e:
            logger.exception(f"""
FAILED TO VALIDATE MATERIAL ALERT
===================================
Material ID         : {obj.id}
Material Name       : {obj.material_name}
Supplier Name       : {supplier_name}
Remaining Stock     : {obj.remaining_stock}
Minimum Stock Level : {obj.minimum_stock_level}
Threshold Param     : {threshold}

Error:
{repr(e)}
===================================
""")
            raise

    logger.info("All material alerts validated successfully.")

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
                    quantity=qty,  #  FIXED SIGN HERE
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
                    quantity=qty,  #  FIXED SIGN HERE
                    rate=avg_rate,
                    total_amount=total,
                    reference_id=reference,
                )
            )

        #  CORRECT SIGN USAGE
        create_entry(
            material.id,
            DBTransactionType.TRANSFER_OUT,
            payload.from_project_id,
            -payload.quantity,  #  OUT = NEGATIVE
        )

        create_entry(
            existing_material.id,
            DBTransactionType.TRANSFER_IN,
            payload.to_project_id,
            payload.quantity,  #  IN = POSITIVE
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

    if alert_type in ["LOW_STOCK", "OUT_OF_STOCK"]:
        pm = await db.scalar(
            select(proj_model.ProjectMember.user_id)
            .join(User, User.id == proj_model.ProjectMember.user_id)
            .where(
                proj_model.ProjectMember.project_id == data.project_id,
                User.role == UserRole.PROJECT_MANAGER.value,
            )
            .limit(1)
        )
        if pm:
            await create_notification(
                db,
                user_id=pm,
                title=f"Material Alert: {alert_type.replace('_', ' ')}",
                message=f"Stock for {obj.material_name} is now {obj.remaining_stock} {obj.unit}.",
                type="alert",
            )
            await db.commit()

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
    from decimal import Decimal, InvalidOperation

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

    return {
        "material_id": material.id,
        "material_name": material.material_name,
        "old_stock": float(old_stock),
        "new_stock": float(material.remaining_stock),
        "difference": float(diff),
        "avg_rate": float(avg_rate),
        "reason": reason,
        "reference_id": reference,
        "message": "Inventory adjusted successfully",
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
@router.get(
    "/reports",
    response_model=MaterialReportResponse,
)
async def material_report(
    project_id: int = Query(...),  # Required
    supplier_id: Optional[str] = None,
    material_id: Optional[str] = None,
    category: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
    db: AsyncSession = Depends(get_db_session),
):
    limit = min(max(limit, 1), 100)
    skip = max(skip, 0)

    query = select(Material).where(Material.is_deleted == False)

    if project_id:
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

        avg_rate = m.avg_rate if m.avg_rate else Decimal("0")

        stock_value = remaining * avg_rate

        total_purchased += purchased
        total_used += used
        total_remaining += remaining

        total_stock_value += stock_value

        total_payment_given += m.payment_given or Decimal("0")

        total_payment_pending += m.payment_pending or Decimal("0")

        if m.alert_type == "IN_STOCK":
            in_stock_count += 1

        elif m.alert_type == "LOW_STOCK":
            low_stock_count += 1

        elif m.alert_type == "OUT_OF_STOCK":
            out_of_stock_count += 1

        report_rows.append(
            MaterialReport(
                material_id=m.id,
                material_code=m.material_code,
                material_name=m.material_name,
                category=m.category,
                unit=m.unit,
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
                alert_type=m.alert_type,
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
    HRFlowable,
    KeepTogether,
)

# ─────────────────────────── COLOUR PALETTE ──────────────────────────────────
NAVY = colors.HexColor("#163A6B")
NAVY_LIGHT = colors.HexColor("#1E4D8C")
ORANGE = colors.HexColor("#F57C00")
ORANGE_PALE = colors.HexColor("#FFF3E0")
BG_LIGHT = colors.HexColor("#F4F7FC")
BG_STRIPE = colors.HexColor("#EBF0FA")
BORDER_CLR = colors.HexColor("#D0D5DD")
DIVIDER_CLR = colors.HexColor("#E4E9F2")
TEXT_DARK = colors.HexColor("#1A1A2E")
TEXT_MID = colors.HexColor("#344054")
TEXT_GREY = colors.HexColor("#667085")
TEXT_LIGHT = colors.HexColor("#98A2B3")

RED_ALERT = colors.HexColor("#D92D20")
RED_DARK = colors.HexColor("#B42318")
RED_BG = colors.HexColor("#FFF1F0")
RED_BORDER = colors.HexColor("#FDA29B")

GREEN_OK = colors.HexColor("#027A48")
GREEN_DARK = colors.HexColor("#05603A")
GREEN_BG = colors.HexColor("#ECFDF3")
GREEN_BORDER = colors.HexColor("#6CE9A6")

AMBER_LOW = colors.HexColor("#B54708")
AMBER_DARK = colors.HexColor("#93370D")
AMBER_BG = colors.HexColor("#FFFAEB")
AMBER_BORDER = colors.HexColor("#FEC84B")

WHITE = colors.white
FOOTER_BG = colors.HexColor("#EEF2F7")
SECTION_BAR = colors.HexColor("#E8EEF7")


# ─────────────────────────── SAFE HELPERS ────────────────────────────────────
def fmt(val, dec: int = 2) -> str:
    try:
        val = val or Decimal("0")
        return f"{float(val):,.{dec}f}"
    except Exception:
        return "0"


def rs(val, dec: int = 2) -> str:
    return f"Rs. {fmt(val, dec)}"


def safe_delete(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# ─────────────────────────── STYLE FACTORY ───────────────────────────────────
def _styles():
    """Return a dict of all named ParagraphStyles used in the report."""
    return {
        # Section heading strip
        "sec": ParagraphStyle(
            "sec",
            fontName="Helvetica-Bold",
            fontSize=8.5,
            textColor=NAVY,
            leading=11,
            spaceBefore=0,
            spaceAfter=0,
            leftIndent=6,
        ),
        # Table header
        "th": ParagraphStyle(
            "th",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=WHITE,
            leading=10,
            alignment=0,
        ),
        "th_c": ParagraphStyle(
            "th_c",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=WHITE,
            leading=10,
            alignment=1,
        ),
        "th_r": ParagraphStyle(
            "th_r",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=WHITE,
            leading=10,
            alignment=2,
        ),
        # Table cell
        "td": ParagraphStyle(
            "td",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=TEXT_DARK,
            leading=10,
        ),
        "td_r": ParagraphStyle(
            "td_r",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=TEXT_DARK,
            leading=10,
            alignment=2,
        ),
        "td_c": ParagraphStyle(
            "td_c",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=TEXT_DARK,
            leading=10,
            alignment=1,
        ),
        # Table total/footer row
        "tf": ParagraphStyle(
            "tf",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=NAVY,
            leading=10,
        ),
        "tf_r": ParagraphStyle(
            "tf_r",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=NAVY,
            leading=10,
            alignment=2,
        ),
        # Contact bar
        "ci": ParagraphStyle(
            "ci",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=TEXT_MID,
            leading=10,
            alignment=1,
        ),
        "ci_icon": ParagraphStyle(
            "ci_icon",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=NAVY,
            leading=10,
            alignment=1,
        ),
    }


# ─────────────────────────── SECTION HEADING ─────────────────────────────────
def _section(title: str, s, doc_width: float) -> Table:
    """Render a full-width navy-left-bar section heading strip."""
    t = Table(
        [[Paragraph(title, s["sec"])]],
        colWidths=[doc_width],
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), SECTION_BAR),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBEFORE", (0, 0), (0, -1), 4, ORANGE),
                ("BOX", (0, 0), (-1, -1), 0.4, DIVIDER_CLR),
            ]
        )
    )
    return t


# ─────────────────────────── STATUS BADGE ────────────────────────────────────
_STATUS_CFG = {
    "OUT": ("#D92D20", "#B42318", RED_BG, RED_BORDER, "OUT OF STOCK"),
    "LOW": ("#B54708", "#93370D", AMBER_BG, AMBER_BORDER, "LOW STOCK"),
    "OK": ("#027A48", "#05603A", GREEN_BG, GREEN_BORDER, "IN STOCK"),
}


def _badge(status: str, name_suffix: str = "") -> Table:
    """Pill-style status badge rendered as a 1-cell Table."""
    fg_hex, _, bg, border, label = _STATUS_CFG.get(status, _STATUS_CFG["OK"])
    ps = ParagraphStyle(
        f"badge_{status}_{name_suffix}",
        fontName="Helvetica-Bold",
        fontSize=6.5,
        textColor=colors.HexColor(fg_hex),
        leading=8,
        alignment=1,
    )
    t = Table([[Paragraph(f"<b>{label}</b>", ps)]], colWidths=[22 * mm])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), bg),
                ("BOX", (0, 0), (-1, -1), 0.6, border),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("ROUNDEDCORNERS", [3]),
            ]
        )
    )
    return t


# ─────────────────────────── PAGE HEADER / FOOTER ────────────────────────────
def _draw_page(canvas_obj, doc):
    canvas_obj.saveState()
    w, h = A4

    # ── Top accent bar ──────────────────────────────────────────────────────
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.rect(0, h - 5, w, 5, fill=1, stroke=0)

    # ── Navy header band ────────────────────────────────────────────────────
    BAND_H = 58
    canvas_obj.setFillColor(NAVY)
    canvas_obj.rect(0, h - 5 - BAND_H, w, BAND_H, fill=1, stroke=0)

    # subtle inner bottom border on header
    canvas_obj.setStrokeColor(NAVY_LIGHT)
    canvas_obj.setLineWidth(0.5)
    canvas_obj.line(0, h - 5 - BAND_H, w, h - 5 - BAND_H)

    header_mid = h - 5 - BAND_H / 2  # vertical centre of band

    # ── Logo ────────────────────────────────────────────────────────────────
    logo_x = 18 * mm
    canvas_obj.setFillColor(WHITE)
    canvas_obj.setFont("Helvetica-Bold", 19)
    canvas_obj.drawString(logo_x, header_mid + 5, "INFRA")
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.drawString(logo_x + 57, header_mid + 5, "PILOT")

    # tagline
    canvas_obj.setFillColor(colors.HexColor("#AECBF5"))
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.drawString(logo_x, header_mid - 8, "Construction Billing Software")

    # thin vertical divider between logo and badge area
    canvas_obj.setStrokeColor(colors.HexColor("#2E5FA3"))
    canvas_obj.setLineWidth(0.8)
    canvas_obj.line(logo_x + 115, header_mid - 14, logo_x + 115, header_mid + 17)

    # ── REPORT badge ────────────────────────────────────────────────────────
    badge_w, badge_h, badge_r = 42 * mm, 22, 4
    badge_x = w - 18 * mm - badge_w
    badge_y = header_mid - badge_h / 2

    # shadow
    canvas_obj.setFillColor(colors.HexColor("#0D2A52"))
    canvas_obj.roundRect(
        badge_x + 1.5, badge_y - 1.5, badge_w, badge_h, radius=badge_r, fill=1, stroke=0
    )
    # badge
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.roundRect(
        badge_x, badge_y, badge_w, badge_h, radius=badge_r, fill=1, stroke=0
    )
    canvas_obj.setFillColor(WHITE)
    canvas_obj.setFont("Helvetica-Bold", 12)
    canvas_obj.drawCentredString(badge_x + badge_w / 2, badge_y + 6, "REPORT")

    # ── Timestamp ───────────────────────────────────────────────────────────
    canvas_obj.setFillColor(colors.HexColor("#7AAEE8"))
    canvas_obj.setFont("Helvetica", 7)
    ts = datetime.utcnow().strftime("%d %b %Y  %H:%M UTC")
    canvas_obj.drawRightString(badge_x - 6, badge_y + 7, f"Generated: {ts}")

    # ── Page-1 title block ──────────────────────────────────────────────────
    if doc.page == 1:
        title_y = h - 5 - BAND_H - 28

        canvas_obj.setFillColor(NAVY)
        canvas_obj.setFont("Helvetica-Bold", 17)
        canvas_obj.drawCentredString(w / 2, title_y, "Material Inventory Report")

        proj_line = getattr(doc, "_project_line", None)
        sub_y = title_y - 14

        if proj_line:
            canvas_obj.setFillColor(NAVY_LIGHT)
            canvas_obj.setFont("Helvetica-Bold", 9)
            canvas_obj.drawCentredString(w / 2, sub_y, proj_line)
            sub_y -= 12

        canvas_obj.setFillColor(TEXT_GREY)
        canvas_obj.setFont("Helvetica", 8.5)
        date_str = datetime.utcnow().strftime("%d %B %Y")
        canvas_obj.drawCentredString(w / 2, sub_y, f"Pune, Maharashtra  ·  {date_str}")

        # decorative rule under title
        rule_y = sub_y - 8
        canvas_obj.setStrokeColor(ORANGE)
        canvas_obj.setLineWidth(1.5)
        canvas_obj.line(18 * mm, rule_y, w - 18 * mm, rule_y)

    # ── Footer ──────────────────────────────────────────────────────────────
    # footer band
    canvas_obj.setFillColor(FOOTER_BG)
    canvas_obj.rect(0, 0, w, 18, fill=1, stroke=0)
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.rect(0, 0, w, 3, fill=1, stroke=0)

    canvas_obj.setFillColor(TEXT_GREY)
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.drawCentredString(
        w / 2,
        6,
        "Generated by Infra Pilot System  •  Confidential  •  Internal Use Only",
    )
    canvas_obj.setFillColor(NAVY)
    canvas_obj.setFont("Helvetica-Bold", 7)
    canvas_obj.drawRightString(w - 18 * mm, 6, f"Page {doc.page}")
    canvas_obj.setFont("Helvetica", 7)
    canvas_obj.setFillColor(TEXT_LIGHT)
    canvas_obj.drawString(18 * mm, 6, "INFRA PILOT  ·  Material Report")

    canvas_obj.restoreState()


# ─────────────────────────── PDF BUILDER ─────────────────────────────────────
def _build_pdf(
    file_path: str,
    rows: list,
    project_name: str | None = None,
    project_code: str | None = None,
):
    # ── Document setup ───────────────────────────────────────────────────────
    doc = BaseDocTemplate(
        file_path,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=148,  # page-1 title block needs extra space
        bottomMargin=26,
    )

    # attach project info so _draw_page can read it
    if project_name or project_code:
        parts = [
            p
            for p in (project_name, f"[{project_code}]" if project_code else None)
            if p
        ]
        doc._project_line = "   ·   ".join(parts)
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
    doc.addPageTemplates([PageTemplate(id="main", frames=frame, onPage=_draw_page)])

    s = _styles()
    DW = doc.width  # shorthand
    elements = []

    # ── CONTACT INFO BAR ─────────────────────────────────────────────────────
    ci_items = [
        ("📍", "Pune, Maharashtra"),
        ("📞", "+91 9999999999"),
        ("✉", "info@infrapilot.com"),
        ("🌐", "www.infrapilot.com"),
    ]
    ci_data = [
        [
            Paragraph(f'<font color="#163A6B"><b>{ico}</b></font>  {txt}', s["ci"])
            for ico, txt in ci_items
        ]
    ]
    ci_t = Table(ci_data, colWidths=[DW / 4] * 4)
    ci_t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), BG_LIGHT),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_CLR),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, DIVIDER_CLR),
                ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LINEABOVE", (0, 0), (-1, 0), 2, NAVY),
            ]
        )
    )
    elements.append(ci_t)
    elements.append(Spacer(1, 14))

    # ── PROCESS DATA  (all calculations unchanged) ───────────────────────────
    total_purchased = Decimal("0")
    total_used = Decimal("0")
    total_remaining = Decimal("0")
    total_value = Decimal("0")
    total_pending = Decimal("0")

    processed = []
    alerts_out = []
    alerts_low = []

    for m, sup in rows:
        purchased = m.quantity_purchased or Decimal("0")
        used = m.quantity_used or Decimal("0")
        remaining = m.remaining_stock or Decimal("0")
        total_amt = m.total_amount or Decimal("0")
        pending = m.payment_pending or Decimal("0")
        min_lvl = Decimal(str(m.minimum_stock_level or 0))

        avg_rate = total_amt / purchased if purchased > 0 else Decimal("0")
        value = remaining * avg_rate

        total_purchased += purchased
        total_used += used
        total_remaining += remaining
        total_value += value
        total_pending += pending

        _model_status = (getattr(m, "stock_status", None) or "").upper()

        if _model_status == "OUT_OF_STOCK" or (not _model_status and remaining == 0):
            status = "OUT"
            alerts_out.append((m.material_name or "").title())
        elif _model_status == "LOW_STOCK" or (
            not _model_status and remaining <= min_lvl
        ):
            status = "LOW"
            alerts_low.append((m.material_name or "").title())
        else:
            status = "OK"

        processed.append((m, sup, purchased, used, remaining, avg_rate, value, status))

    # payment totals
    total_purchase_cost = Decimal("0")
    total_advance = Decimal("0")
    for m, _ in rows:
        total_purchase_cost += m.total_amount or Decimal("0")
        total_advance += Decimal(str(getattr(m, "advance_paid", None) or 0))
    total_paid = total_purchase_cost - total_pending

    out_count = len(alerts_out)
    low_count = len(alerts_low)

    # ── EXECUTIVE SUMMARY CARDS ───────────────────────────────────────────────
    elements.append(_section("EXECUTIVE SUMMARY", s, DW))
    elements.append(Spacer(1, 8))

    card_configs = [
        ("Total\nMaterials", str(len(rows)), NAVY, ORANGE),
        ("Total\nPurchased", fmt(total_purchased, 0), NAVY, NAVY_LIGHT),
        ("Total\nUsed", fmt(total_used, 0), NAVY, NAVY_LIGHT),
        ("Stock\nValue", rs(total_value, 0), NAVY, NAVY_LIGHT),
        ("Pending\nAmount", rs(total_pending, 0), ORANGE, ORANGE_PALE),
        (
            "Low\nStock",
            str(low_count),
            AMBER_LOW if low_count > 0 else GREEN_OK,
            AMBER_BG if low_count > 0 else GREEN_BG,
        ),
        (
            "Out Of\nStock",
            str(out_count),
            RED_ALERT if out_count > 0 else GREEN_OK,
            RED_BG if out_count > 0 else GREEN_BG,
        ),
    ]

    card_w = DW / 7

    def _card(label, value, val_clr, top_clr):
        lbl_ps = ParagraphStyle(
            f"cl_{label[:4]}",
            fontName="Helvetica",
            fontSize=6.5,
            textColor=TEXT_GREY,
            leading=8,
            alignment=1,
        )
        val_ps = ParagraphStyle(
            f"cv_{label[:4]}",
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=val_clr,
            leading=14,
            alignment=1,
        )
        t = Table(
            [[Paragraph(label, lbl_ps)], [Paragraph(value, val_ps)]],
            colWidths=[card_w - 2],
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), WHITE),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("LINEABOVE", (0, 0), (-1, 0), 3, top_clr),
                ]
            )
        )
        return t

    cards_row = [[_card(*cfg) for cfg in card_configs]]
    cards_t = Table(cards_row, colWidths=[card_w] * 7)
    cards_t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_CLR),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, DIVIDER_CLR),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    elements.append(cards_t)
    elements.append(Spacer(1, 18))

    # ── MATERIAL DETAILS TABLE ────────────────────────────────────────────────
    elements.append(_section("MATERIAL DETAILS", s, DW))
    elements.append(Spacer(1, 8))

    col_widths = [
        7 * mm,  # #
        23 * mm,  # Material
        19 * mm,  # Code
        19 * mm,  # Category
        26 * mm,  # Supplier
        15 * mm,  # Purchased
        12 * mm,  # Used
        15 * mm,  # Remaining
        15 * mm,  # Avg Rate
        19 * mm,  # Value
        22 * mm,  # Status
    ]
    # total = 192mm  ≤  A4 usable (297-36=174mm … use 170mm for content)
    # Recalculate to fill DW exactly
    total_fixed = sum(col_widths)
    col_widths = [c * DW / total_fixed for c in col_widths]

    headers = [
        ("#", s["th_c"]),
        ("Material", s["th"]),
        ("Code", s["th"]),
        ("Category", s["th"]),
        ("Supplier", s["th"]),
        ("Purchased", s["th_r"]),
        ("Used", s["th_r"]),
        ("Remaining", s["th_r"]),
        ("Avg Rate", s["th_r"]),
        ("Value", s["th_r"]),
        ("Status", s["th_c"]),
    ]
    tdata = [[Paragraph(h, ps) for h, ps in headers]]

    out_rows_idx = []
    low_rows_idx = []

    for i, item in enumerate(processed):
        m, sup, purchased, used, remaining, avg_rate, value, status = item
        ridx = i + 1

        tdata.append(
            [
                Paragraph(str(ridx), s["td_c"]),
                Paragraph((m.material_name or "").title(), s["td"]),
                Paragraph(getattr(m, "material_code", None) or "—", s["td"]),
                Paragraph((getattr(m, "category", None) or "—").title(), s["td"]),
                Paragraph(sup or "N/A", s["td"]),
                Paragraph(fmt(purchased, 1), s["td_r"]),
                Paragraph(fmt(used, 1), s["td_r"]),
                Paragraph(fmt(remaining, 1), s["td_r"]),
                Paragraph(fmt(avg_rate, 2), s["td_r"]),
                Paragraph(fmt(value, 2), s["td_r"]),
                _badge(status, str(i)),
            ]
        )

        if status == "OUT":
            out_rows_idx.append(ridx)
        elif status == "LOW":
            low_rows_idx.append(ridx)

    # total row
    frow = len(tdata)
    tdata.append(
        [
            Paragraph("", s["tf"]),
            Paragraph("<b>TOTAL</b>", s["tf"]),
            Paragraph("", s["tf"]),
            Paragraph("", s["tf"]),
            Paragraph("", s["tf"]),
            Paragraph(f"<b>{fmt(total_purchased, 1)}</b>", s["tf_r"]),
            Paragraph(f"<b>{fmt(total_used, 1)}</b>", s["tf_r"]),
            Paragraph(f"<b>{fmt(total_remaining, 1)}</b>", s["tf_r"]),
            Paragraph("", s["tf"]),
            Paragraph(f"<b>{fmt(total_value, 2)}</b>", s["tf_r"]),
            Paragraph("", s["tf"]),
        ]
    )

    det_ts = TableStyle(
        [
            # header
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("LINEBELOW", (0, 0), (-1, 0), 2, ORANGE),
            # alternating data rows
            ("ROWBACKGROUNDS", (0, 1), (-1, frow - 1), [WHITE, BG_STRIPE]),
            # total row
            ("BACKGROUND", (0, frow), (-1, frow), FOOTER_BG),
            ("LINEABOVE", (0, frow), (-1, frow), 1, BORDER_CLR),
            # borders
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER_CLR),
            ("BOX", (0, 0), (-1, -1), 0.8, BORDER_CLR),
            # padding
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]
    )
    for r in out_rows_idx:
        det_ts.add("BACKGROUND", (0, r), (-1, r), RED_BG)
    for r in low_rows_idx:
        det_ts.add("BACKGROUND", (0, r), (-1, r), AMBER_BG)

    det_table = Table(tdata, colWidths=col_widths, repeatRows=1)
    det_table.setStyle(det_ts)
    elements.append(det_table)
    elements.append(Spacer(1, 18))

    # ── CRITICAL STOCK ALERTS ─────────────────────────────────────────────────
    if alerts_out or alerts_low:
        elements.append(_section("⚠  CRITICAL STOCK ALERTS", s, DW))
        elements.append(Spacer(1, 8))

        alert_th = ParagraphStyle(
            "ath",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=WHITE,
            leading=10,
        )
        alert_td = ParagraphStyle(
            "atd",
            fontName="Helvetica",
            fontSize=7.5,
            textColor=TEXT_DARK,
            leading=10,
        )
        alert_td_bold = ParagraphStyle(
            "atdb",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            textColor=TEXT_DARK,
            leading=10,
        )

        a_data = [
            [
                Paragraph("Priority", alert_th),
                Paragraph("Status", alert_th),
                Paragraph("Material Name", alert_th),
                Paragraph("Action Required", alert_th),
            ]
        ]

        for idx, name in enumerate(alerts_out, 1):
            a_data.append(
                [
                    Paragraph(
                        f"<b>P{idx}</b>",
                        ParagraphStyle(
                            f"ap{idx}",
                            fontName="Helvetica-Bold",
                            fontSize=7.5,
                            textColor=RED_ALERT,
                            leading=10,
                            alignment=1,
                        ),
                    ),
                    _badge("OUT", f"a_out_{idx}"),
                    Paragraph(name, alert_td_bold),
                    Paragraph(
                        "Immediate replenishment required. Raise purchase order.",
                        alert_td,
                    ),
                ]
            )

        for idx, name in enumerate(alerts_low, len(alerts_out) + 1):
            a_data.append(
                [
                    Paragraph(
                        f"<b>P{idx}</b>",
                        ParagraphStyle(
                            f"ap{idx}",
                            fontName="Helvetica-Bold",
                            fontSize=7.5,
                            textColor=AMBER_LOW,
                            leading=10,
                            alignment=1,
                        ),
                    ),
                    _badge("LOW", f"a_low_{idx}"),
                    Paragraph(name, alert_td_bold),
                    Paragraph("Stock below minimum level. Schedule reorder.", alert_td),
                ]
            )

        a_cw = [12 * mm, 26 * mm, DW * 0.30, DW - 12 * mm - 26 * mm - DW * 0.30]
        a_table = Table(a_data, colWidths=a_cw, repeatRows=1)
        a_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                    ("LINEBELOW", (0, 0), (-1, 0), 2, ORANGE),
                    ("GRID", (0, 0), (-1, -1), 0.3, BORDER_CLR),
                    ("BOX", (0, 0), (-1, -1), 0.8, BORDER_CLR),
                    ("ALIGN", (0, 0), (0, -1), "CENTER"),
                    ("ALIGN", (1, 0), (1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [RED_BG, AMBER_BG]),
                ]
            )
        )
        elements.append(a_table)
        elements.append(Spacer(1, 18))

    # ── TOP 5 INVENTORY VALUE MATERIALS ──────────────────────────────────────
    elements.append(_section("TOP 5 INVENTORY VALUE MATERIALS", s, DW))
    elements.append(Spacer(1, 8))

    top5 = sorted(processed, key=lambda x: x[6], reverse=True)[:5]

    t5_th = ParagraphStyle(
        "t5th", fontName="Helvetica-Bold", fontSize=7.5, textColor=WHITE, leading=10
    )
    t5_td = ParagraphStyle(
        "t5td", fontName="Helvetica", fontSize=7.5, textColor=TEXT_DARK, leading=10
    )
    t5_td_r = ParagraphStyle(
        "t5tdr",
        fontName="Helvetica",
        fontSize=7.5,
        textColor=TEXT_DARK,
        leading=10,
        alignment=2,
    )
    t5_val = ParagraphStyle(
        "t5val",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        textColor=NAVY,
        leading=10,
        alignment=2,
    )
    t5_rank = ParagraphStyle(
        "t5rank",
        fontName="Helvetica-Bold",
        fontSize=8,
        textColor=ORANGE,
        leading=10,
        alignment=1,
    )

    t5_cw = [10 * mm, DW * 0.27, 22 * mm, 22 * mm, 25 * mm, 30 * mm, 22 * mm]
    t5_cw_adj = [c * DW / sum(t5_cw) for c in t5_cw]

    t5_data = [
        [
            Paragraph("#", t5_th),
            Paragraph("Material", t5_th),
            Paragraph("Code", t5_th),
            Paragraph(
                "Remaining",
                ParagraphStyle(
                    "t5thr",
                    fontName="Helvetica-Bold",
                    fontSize=7.5,
                    textColor=WHITE,
                    leading=10,
                    alignment=2,
                ),
            ),
            Paragraph(
                "Avg Rate",
                ParagraphStyle(
                    "t5thr2",
                    fontName="Helvetica-Bold",
                    fontSize=7.5,
                    textColor=WHITE,
                    leading=10,
                    alignment=2,
                ),
            ),
            Paragraph(
                "Stock Value",
                ParagraphStyle(
                    "t5thrv",
                    fontName="Helvetica-Bold",
                    fontSize=7.5,
                    textColor=WHITE,
                    leading=10,
                    alignment=2,
                ),
            ),
            Paragraph(
                "Status",
                ParagraphStyle(
                    "t5thc",
                    fontName="Helvetica-Bold",
                    fontSize=7.5,
                    textColor=WHITE,
                    leading=10,
                    alignment=1,
                ),
            ),
        ]
    ]

    for rank, item in enumerate(top5, 1):
        m, sup, purchased, used, remaining, avg_rate, value, status = item
        t5_data.append(
            [
                Paragraph(f"<b>{rank}</b>", t5_rank),
                Paragraph((m.material_name or "").title(), t5_td),
                Paragraph(getattr(m, "material_code", None) or "—", t5_td),
                Paragraph(fmt(remaining, 1), t5_td_r),
                Paragraph(fmt(avg_rate, 2), t5_td_r),
                Paragraph(f"<b>{rs(value, 2)}</b>", t5_val),
                _badge(status, f"t5_{rank}"),
            ]
        )

    t5_table = Table(t5_data, colWidths=t5_cw_adj, repeatRows=1)
    t5_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("LINEBELOW", (0, 0), (-1, 0), 2, ORANGE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, BG_STRIPE]),
                ("GRID", (0, 0), (-1, -1), 0.3, BORDER_CLR),
                ("BOX", (0, 0), (-1, -1), 0.8, BORDER_CLR),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (6, 0), (6, -1), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                # gold medal highlight for rank 1
                ("LINEABOVE", (0, 1), (-1, 1), 0.8, ORANGE),
                ("LINEBELOW", (0, 1), (-1, 1), 0.8, ORANGE),
            ]
        )
    )
    elements.append(t5_table)
    elements.append(Spacer(1, 18))

    # ── PAYMENT SUMMARY ───────────────────────────────────────────────────────
    elements.append(_section("PAYMENT SUMMARY", s, DW))
    elements.append(Spacer(1, 8))

    pay_configs = [
        ("Total Purchase Cost", rs(total_purchase_cost, 2), NAVY, NAVY),
        ("Total Paid", rs(total_paid, 2), GREEN_OK, GREEN_OK),
        ("Total Pending", rs(total_pending, 2), RED_ALERT, RED_ALERT),
        # ("Advance Paid", rs(total_advance, 2), ORANGE, ORANGE),
    ]

    pay_card_w = DW / 4

    def _pay_card(label, value, val_clr, bar_clr):
        lbl_ps = ParagraphStyle(
            f"pl_{label[:4]}",
            fontName="Helvetica",
            fontSize=7,
            textColor=TEXT_GREY,
            leading=9,
            alignment=0,
        )
        val_ps = ParagraphStyle(
            f"pv_{label[:4]}",
            fontName="Helvetica-Bold",
            fontSize=11,
            textColor=val_clr,
            leading=14,
            alignment=0,
        )
        t = Table(
            [[Paragraph(label, lbl_ps)], [Paragraph(f"<b>{value}</b>", val_ps)]],
            colWidths=[pay_card_w - 4],
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), WHITE),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("LINEABOVE", (0, 0), (-1, 0), 3, bar_clr),
                ]
            )
        )
        return t

    pay_row = [[_pay_card(*cfg) for cfg in pay_configs]]
    pay_t = Table(pay_row, colWidths=[pay_card_w] * 4)
    pay_t.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER_CLR),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, DIVIDER_CLR),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    elements.append(pay_t)

    # ── BUILD ─────────────────────────────────────────────────────────────────
    doc.build(elements)


# ═════════════════════════════ API ENDPOINT ════════════════════════════════════
@router.get("/reports/pdf", response_class=FileResponse)
async def export_pdf(
    project_id: int = Query(...),  # Required
    category: str | None = Query(None),
    supplier_id: int | None = Query(None),
    material_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    try:
        query = (
            select(Material, Supplier.supplier_name)
            .join(Supplier, Supplier.id == Material.supplier_id, isouter=True)
            .where(Material.is_deleted == False)
        )

        if project_id:
            query = query.where(Material.project_id == project_id)
        if category:
            query = query.where(func.lower(Material.category) == category.lower())
        if supplier_id:
            query = query.where(Material.supplier_id == supplier_id)
        if material_id:
            query = query.where(Material.id == material_id)

        rows = (await db.execute(query.order_by(Material.id))).all()

        if not rows:
            raise HTTPException(status_code=404, detail="No material data found")

        project_name = None
        if project_id:
            project = await db.get(Project, project_id)
            if project:
                project_name = project.project_name

        file_path = os.path.join(
            tempfile.gettempdir(),
            f"material_report_{uuid.uuid4()}.pdf",
        )

        try:
            _build_pdf(
                file_path=file_path,
                rows=rows,
                project_name=project_name,
            )
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


# ================excel report================
from openpyxl.utils import get_column_letter


@router.get(
    "/reports/excel",
    response_class=FileResponse,
)
async def export_excel(
    project_id: int = Query(...),
    category: str | None = Query(None),
    supplier_id: int | None = Query(None),
    material_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles(MATERIAL_READ_ROLES)),
):
    try:

        import tempfile
        from datetime import datetime

        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment

        project = await db.get(Project, project_id)

        if not project:
            raise HTTPException(
                status_code=404,
                detail="Project not found",
            )

        query = (
            select(
                Material,
                Supplier.supplier_name,
            )
            .outerjoin(
                Supplier,
                Supplier.id == Material.supplier_id,
            )
            .where(
                Material.is_deleted == False,
                Material.project_id == project_id,
            )
            .order_by(Material.id)
        )

        if category:
            query = query.where(func.lower(Material.category) == category.lower())

        if supplier_id:
            query = query.where(Material.supplier_id == supplier_id)

        if material_id:
            query = query.where(Material.id == material_id)

        rows = (await db.execute(query)).all()

        if not rows:
            raise HTTPException(
                status_code=404,
                detail="No material data found",
            )

        wb = Workbook()
        ws = wb.active

        ws.title = "Material Report"

        # ==================================================
        # REPORT HEADER
        # ==================================================

        ws.merge_cells("A1:R1")

        ws["A1"] = "INFRA PILOT MATERIAL REPORT"

        ws["A1"].font = Font(
            bold=True,
            size=16,
        )

        ws["A1"].alignment = Alignment(
            horizontal="center",
        )

        ws["A3"] = f"Project : {project.project_name}"

        ws["A4"] = f"Date : " f"{datetime.now().strftime('%d-%m-%Y %H:%M:%S')}"

        # ==================================================
        # SUMMARY
        # ==================================================

        total_materials = len(rows)
        total_purchased = Decimal("0")
        total_used = Decimal("0")
        total_remaining = Decimal("0")
        total_stock_value = Decimal("0")
        total_paid = Decimal("0")
        total_pending = Decimal("0")

        in_stock = 0
        low_stock = 0
        out_stock = 0

        for material, supplier_name in rows:

            purchased = material.quantity_purchased or Decimal("0")
            used = material.quantity_used or Decimal("0")
            remaining = material.remaining_stock or Decimal("0")

            avg_rate = (
                material.total_amount / purchased if purchased > 0 else Decimal("0")
            )

            stock_value = remaining * avg_rate

            total_purchased += purchased
            total_used += used
            total_remaining += remaining

            total_stock_value += stock_value

            total_paid += material.payment_given or Decimal("0")
            total_pending += material.payment_pending or Decimal("0")

            if material.alert_type == "OUT_OF_STOCK":
                out_stock += 1

            elif material.alert_type == "LOW_STOCK":
                low_stock += 1

            else:
                in_stock += 1

        ws["A6"] = f"Materials : {total_materials}"
        ws["A7"] = f"Stock : {float(total_remaining):,.2f}"
        ws["A8"] = f"Value : ₹{float(total_stock_value):,.2f}"
        ws["A9"] = f"Paid : ₹{float(total_paid):,.2f}"
        ws["A10"] = f"Pending : ₹{float(total_pending):,.2f}"

        ws["D6"] = f"In Stock : {in_stock}"
        ws["D7"] = f"Low Stock : {low_stock}"
        ws["D8"] = f"Out Of Stock : {out_stock}"

        # ==================================================
        # TABLE HEADER
        # ==================================================

        start_row = 13

        headers = [
            "ID",
            "Material Code",
            "Material Name",
            "Category",
            "Unit",
            "Supplier",
            "Rate Type",
            "Purchase Rate",
            "Purchased Qty",
            "Used Qty",
            "Remaining Qty",
            "Avg Rate",
            "Stock Value",
            "Total Amount",
            "Payment Given",
            "Payment Pending",
            "Min Stock",
            "Alert Type",
        ]

        for col_num, header in enumerate(headers, start=1):

            cell = ws.cell(
                row=start_row,
                column=col_num,
                value=header,
            )

            cell.font = Font(bold=True)

        # ==================================================
        # DATA
        # ==================================================

        row_num = start_row + 1

        for material, supplier_name in rows:

            purchased = material.quantity_purchased or Decimal("0")

            avg_rate = (
                material.total_amount / purchased if purchased > 0 else Decimal("0")
            )

            stock_value = (material.remaining_stock or Decimal("0")) * avg_rate

            ws.cell(row_num, 1, material.id)
            ws.cell(row_num, 2, material.material_code)
            ws.cell(row_num, 3, material.material_name)
            ws.cell(row_num, 4, material.category)
            ws.cell(row_num, 5, material.unit)
            ws.cell(row_num, 6, supplier_name or "-")

            ws.cell(
                row_num,
                7,
                material.rate_type.value if material.rate_type else "",
            )

            ws.cell(
                row_num,
                8,
                float(material.purchase_rate or 0),
            )

            ws.cell(
                row_num,
                9,
                float(material.quantity_purchased or 0),
            )

            ws.cell(
                row_num,
                10,
                float(material.quantity_used or 0),
            )

            ws.cell(
                row_num,
                11,
                float(material.remaining_stock or 0),
            )

            ws.cell(
                row_num,
                12,
                float(avg_rate),
            )

            ws.cell(
                row_num,
                13,
                float(stock_value),
            )

            ws.cell(
                row_num,
                14,
                float(material.total_amount or 0),
            )

            ws.cell(
                row_num,
                15,
                float(material.payment_given or 0),
            )

            ws.cell(
                row_num,
                16,
                float(material.payment_pending or 0),
            )

            ws.cell(
                row_num,
                17,
                float(material.minimum_stock_level or 0),
            )

            ws.cell(
                row_num,
                18,
                material.alert_type,
            )

            row_num += 1

        # ==================================================
        # TOTAL ROW
        # ==================================================

        ws.cell(row_num + 1, 1, "TOTAL")

        ws.cell(row_num + 1, 9, float(total_purchased))
        ws.cell(row_num + 1, 10, float(total_used))
        ws.cell(row_num + 1, 11, float(total_remaining))
        ws.cell(row_num + 1, 13, float(total_stock_value))
        ws.cell(row_num + 1, 15, float(total_paid))
        ws.cell(row_num + 1, 16, float(total_pending))

        for col in range(1, 19):
            ws.cell(
                row_num + 1,
                col,
            ).font = Font(bold=True)

        # ==================================================
        # EXCEL SETTINGS
        # ==================================================

        ws.freeze_panes = f"A{start_row + 1}"

        ws.auto_filter.ref = f"A{start_row}:R{row_num}"

        for column in ws.columns:
            max_length = 0
            column_index = column[0].column
            column_letter = get_column_letter(column_index)

            for cell in column:
                try:
                    if cell.value:
                        max_length = max(
                            max_length,
                            len(str(cell.value)),
                        )
                except Exception:
                    pass

            ws.column_dimensions[column_letter].width = max_length + 5

        # ==================================================
        # SAVE FILE
        # ==================================================

        file_path = tempfile.NamedTemporaryFile(
            delete=False,
            suffix=".xlsx",
        ).name

        wb.save(file_path)

        return FileResponse(
            path=file_path,
            filename=f"material_report_{datetime.now().strftime('%Y%m%d')}.xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            background=BackgroundTask(
                safe_delete,
                file_path,
            ),
        )

    except HTTPException:
        raise

    except Exception as e:

        raise HTTPException(
            status_code=500,
            detail=f"Material Excel Report Error: {str(e)}",
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

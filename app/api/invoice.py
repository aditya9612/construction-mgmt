from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.expense import Expense
from app.models.final_measurement import FinalMeasurement
from app.models.labour import Labour, LabourAttendance
from app.models.project import Project
from app.db.session import get_db_session
from app.models.invoice import Invoice
from app.models.owner import OwnerTransaction
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate, InvoiceOut
from app.utils.helpers import NotFoundError, ValidationError
from decimal import Decimal
from fastapi.responses import StreamingResponse
from io import BytesIO
from app.core.logger import logger

router = APIRouter(prefix="/invoices", tags=["invoices"])

from sqlalchemy import and_


@router.post("/{type}", response_model=InvoiceOut)
async def create_invoice(
    type: str,
    payload: InvoiceCreate,
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Creating invoice type={type} project_id={payload.project_id}")

    data = payload.model_dump()

    project = await db.get(Project, data["project_id"])
    if not project:
        logger.warning(f"Project not found id={data['project_id']}")
        raise NotFoundError("Project not found")

    allowed_types = ["owner", "contractor", "labour", "material"]
    if type not in allowed_types:
        raise ValidationError("Invalid invoice type")

    if type == "owner":
        existing_invoice = await db.scalar(
            select(Invoice).where(
                Invoice.project_id == data["project_id"],
                Invoice.type == "owner",
            )
        )
        if existing_invoice:
            raise ValidationError("Owner invoice already exists")

    try:
        if type == "owner":
            measurement = await db.scalar(
                select(FinalMeasurement).where(
                    FinalMeasurement.project_id == data["project_id"]
                )
            )

            if not measurement:
                raise NotFoundError("Final measurement not found for project")

            base_amount = Decimal(measurement.total_amount)
        else:
            base_amount = Decimal(data["amount"])

        gst_percent = Decimal(data.get("gst_percent", 0))
        tax_percent = Decimal(data.get("tax_percent", 0))

        gst_amount = (base_amount * gst_percent) / Decimal(100)
        tax_amount = (base_amount * tax_percent) / Decimal(100)

        total_amount = base_amount + gst_amount + tax_amount

        obj = Invoice(
            project_id=data["project_id"],
            owner_id=project.owner_id,
            type=type,
            reference_id=data.get("reference_id"),
            amount=base_amount,
            gst_percent=gst_percent,
            gst_amount=gst_amount,
            tax_percent=tax_percent,
            tax_amount=tax_amount,
            total_amount=total_amount,
            description=data.get("description"),
        )

        db.add(obj)
        await db.flush()

        owner_transaction = OwnerTransaction(
            owner_id=project.owner_id,
            project_id=obj.project_id,
            type="credit",
            amount=total_amount,
            reference_type="invoice",
            reference_id=obj.id,
            description=f"{type} invoice created",
        )

        db.add(owner_transaction)

        await db.commit()

    except Exception:
        await db.rollback()
        logger.exception(f"Invoice creation failed type={type}")
        raise

    await db.refresh(obj)

    logger.info(f"Invoice created id={obj.id}")

    return InvoiceOut.model_validate(obj)


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(Invoice))).scalars().all()
    return [InvoiceOut.model_validate(r) for r in rows]


@router.get("/{id}", response_model=InvoiceOut)
async def get_invoice(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Invoice, id)

    if not obj:
        raise NotFoundError("Invoice not found")

    return InvoiceOut.model_validate(obj)


@router.put("/{id}", response_model=InvoiceOut)
async def update_invoice(
    id: int, payload: InvoiceUpdate, db: AsyncSession = Depends(get_db_session)
):
    logger.info(f"Updating invoice id={id}")

    obj = await db.get(Invoice, id)

    if not obj:
        logger.warning(f"Invoice not found id={id}")
        raise NotFoundError("Invoice not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Invoice update failed id={id}")
        raise

    await db.refresh(obj)

    logger.info(f"Invoice updated id={id}")

    return InvoiceOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_invoice(id: int, db: AsyncSession = Depends(get_db_session)):
    logger.info(f"Deleting invoice id={id}")

    obj = await db.get(Invoice, id)

    if not obj:
        logger.warning(f"Invoice not found id={id}")
        raise NotFoundError("Invoice not found")

    try:
        await db.delete(obj)
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Invoice delete failed id={id}")
        raise

    logger.info(f"Invoice deleted id={id}")

    return None


@router.get("/project/{project_id}")
async def get_by_project(project_id: int, db: AsyncSession = Depends(get_db_session)):
    rows = (
        (await db.execute(select(Invoice).where(Invoice.project_id == project_id)))
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


@router.get("/type/{type}")
async def get_by_type(type: str, db: AsyncSession = Depends(get_db_session)):
    rows = (
        (await db.execute(select(Invoice).where(Invoice.type == type))).scalars().all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


@router.get("/date-range")
async def get_by_date_range(
    start: date, end: date, db: AsyncSession = Depends(get_db_session)
):
    rows = (
        (
            await db.execute(
                select(Invoice).where(Invoice.created_at.between(start, end))
            )
        )
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


@router.post("/{id}/mark-paid")
async def mark_paid(id: int, db: AsyncSession = Depends(get_db_session)):
    logger.info(f"Marking invoice as paid id={id}")

    obj = await db.get(Invoice, id)

    if not obj:
        logger.warning(f"Invoice not found id={id}")
        raise NotFoundError("Invoice not found")

    obj.status = "paid"

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception(f"Mark paid failed id={id}")
        raise

    logger.info(f"Invoice marked as paid id={id}")

    return {"message": "Invoice marked as paid"}


@router.get("/{id}/pdf")
async def generate_invoice_pdf(id: int, db: AsyncSession = Depends(get_db_session)):
    logger.info(f"Generating invoice PDF id={id}")

    obj = await db.get(Invoice, id)

    if not obj:
        logger.warning(f"Invoice not found id={id}")
        raise NotFoundError("Invoice not found")

    try:
        buffer = BytesIO()

        content = f"""
        INVOICE #{obj.id}
        
        Type: {obj.type}
        Amount: {obj.amount}
        
        GST ({obj.gst_percent}%): {obj.gst_amount}
        Tax ({obj.tax_percent}%): {obj.tax_amount}
        
        Total Amount: {obj.total_amount}
        Status: {obj.status}
        """

        buffer.write(content.encode())
        buffer.seek(0)

    except Exception:
        logger.exception(f"PDF generation failed id={id}")
        raise

    logger.info(f"Invoice PDF generated id={id}")

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{obj.id}.pdf"},
    )


@router.post("/labour", response_model=InvoiceOut)
async def create_labour_invoice(
    project_id: int,
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db_session),
):

    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

    description = f"Labour invoice ({start_date} to {end_date})"

    existing_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.project_id == project_id,
            Invoice.type == "labour",
            Invoice.description == description,
        )
    )

    if existing_invoice:
        raise ValueError("Labour invoice already exists for this date range")

    result = await db.execute(
        select(LabourAttendance).where(
            LabourAttendance.project_id == project_id,
            LabourAttendance.attendance_date.between(start_date, end_date),
        )
    )
    attendances = result.scalars().all()

    if not attendances:
        raise NotFoundError("No labour attendance found for given period")

    total_amount = Decimal(0)
    attendance_ids = []

    for att in attendances:
        labour = await db.get(Labour, att.labour_id)

        if not labour:
            continue

        attendance_ids.append(att.id)  # 🔥 TRACK

        daily_rate = Decimal(labour.daily_wage_rate or 0)

        wage = daily_rate * Decimal(att.working_hours) + Decimal(
            att.overtime_rate or 0
        ) * Decimal(att.overtime_hours)

        total_amount += wage

    obj = Invoice(
        project_id=project_id,
        owner_id=project.owner_id,
        type="labour",
        reference_id=None,
        linked_expense_ids=attendance_ids,  # 🔥 NEW FIELD
        amount=total_amount,
        gst_percent=Decimal(0),
        gst_amount=Decimal(0),
        tax_percent=Decimal(0),
        tax_amount=Decimal(0),
        total_amount=total_amount,
        description=description,
    )

    db.add(obj)
    await db.flush()

    owner_txn = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=project_id,
        type="credit",
        amount=total_amount,
        reference_type="invoice",
        reference_id=obj.id,
        description="Labour invoice generated",
    )

    db.add(owner_txn)

    await db.commit()
    await db.refresh(obj)

    return InvoiceOut.model_validate(obj)


@router.post("/material", response_model=InvoiceOut)
async def create_material_invoice(
    project_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    project = await db.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found")

    result = await db.execute(
        select(Expense).where(
            Expense.project_id == project_id,
            Expense.category == "Material",
        )
    )
    expenses = result.scalars().all()

    if not expenses:
        raise NotFoundError("No material expenses found")

    total_amount = sum(Decimal(e.amount or 0) for e in expenses)

    expense_ids = [e.id for e in expenses]

    obj = Invoice(
        project_id=project_id,
        owner_id=project.owner_id,
        type="material",
        reference_id=None,
        linked_expense_ids=expense_ids,  # 🔥 NEW
        amount=total_amount,
        gst_percent=Decimal(0),
        gst_amount=Decimal(0),
        tax_percent=Decimal(0),
        tax_amount=Decimal(0),
        total_amount=total_amount,
        description="Material invoice (aggregated)",
    )

    db.add(obj)
    await db.flush()

    owner_txn = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=project_id,
        type="credit",
        amount=total_amount,
        reference_type="invoice",
        reference_id=obj.id,
        description="Material invoice generated",
    )

    db.add(owner_txn)

    await db.commit()
    await db.refresh(obj)

    return InvoiceOut.model_validate(obj)


@router.post("/from-measurement/{measurement_id}", response_model=InvoiceOut)
async def create_invoice_from_measurement(
    measurement_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    measurement = await db.get(FinalMeasurement, measurement_id)
    if not measurement:
        raise NotFoundError("Measurement not found")

    project = await db.get(Project, measurement.project_id)

    existing_invoice = await db.scalar(
        select(Invoice).where(
            Invoice.project_id == measurement.project_id,
            Invoice.type == "owner",
        )
    )
    if existing_invoice:
        raise ValueError("Owner invoice already exists")

    total_amount = Decimal(measurement.total_amount)

    obj = Invoice(
        project_id=measurement.project_id,
        owner_id=project.owner_id,
        type="owner",
        reference_id=measurement.id,
        amount=total_amount,
        gst_percent=Decimal(0),
        gst_amount=Decimal(0),
        tax_percent=Decimal(0),
        tax_amount=Decimal(0),
        total_amount=total_amount,
        description="Invoice from final measurement",
    )

    db.add(obj)
    await db.flush()

    owner_txn = OwnerTransaction(
        owner_id=project.owner_id,
        project_id=measurement.project_id,
        type="credit",
        amount=total_amount,
        reference_type="invoice",
        reference_id=obj.id,
        description="Measurement invoice generated",
    )

    db.add(owner_txn)

    await db.commit()
    await db.refresh(obj)

    return InvoiceOut.model_validate(obj)

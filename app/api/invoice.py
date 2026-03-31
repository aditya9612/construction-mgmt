from typing import Optional
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.final_measurement import FinalMeasurement
from app.models.project import Project
from app.db.session import get_db_session
from app.models.invoice import Invoice
from app.models.owner import OwnerTransaction
from app.schemas.invoice import InvoiceCreate, InvoiceUpdate, InvoiceOut
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/{type}", response_model=InvoiceOut)
async def create_invoice(
    type: str,
    payload: InvoiceCreate,
    db: AsyncSession = Depends(get_db_session),
):
    data = payload.model_dump()

    project = await db.get(Project, data["project_id"])
    if not project:
        raise NotFoundError("Project not found")

    if type == "owner":
        measurement = await db.scalar(
            select(FinalMeasurement).where(
                FinalMeasurement.project_id == data["project_id"]
            )
        )

        if not measurement:
            raise NotFoundError("Final measurement not found for project")

        base_amount = float(measurement.total_amount)

    else:
        base_amount = data["amount"]

    gst_percent = data.get("gst_percent", 0)
    tax_percent = data.get("tax_percent", 0)

    gst_amount = base_amount * gst_percent / 100
    tax_amount = base_amount * tax_percent / 100

    total_amount = base_amount + gst_amount + tax_amount

    obj = Invoice(
        project_id=data["project_id"],
        owner_id=data["owner_id"],
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
        owner_id=obj.owner_id,
        project_id=obj.project_id,
        type="credit",
        amount=total_amount,
        reference_type="invoice",
        reference_id=obj.id,
        description=f"{type} invoice created",
    )
    db.add(owner_transaction)

    await db.commit()
    await db.refresh(obj)

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
    obj = await db.get(Invoice, id)

    if not obj:
        raise NotFoundError("Invoice not found")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)

    return InvoiceOut.model_validate(obj)


@router.delete("/{id}", status_code=204)
async def delete_invoice(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Invoice, id)

    if not obj:
        raise NotFoundError("Invoice not found")

    await db.delete(obj)
    await db.commit()

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
    obj = await db.get(Invoice, id)

    if not obj:
        raise NotFoundError("Invoice not found")

    obj.status = "paid"

    await db.commit()

    return {"message": "Invoice marked as paid"}


@router.get("/pending")
async def pending_invoices(db: AsyncSession = Depends(get_db_session)):
    rows = (
        (await db.execute(select(Invoice).where(Invoice.status == "pending")))
        .scalars()
        .all()
    )

    return [InvoiceOut.model_validate(r) for r in rows]


from fastapi.responses import StreamingResponse
from io import BytesIO


@router.get("/{id}/pdf")
async def generate_invoice_pdf(id: int, db: AsyncSession = Depends(get_db_session)):
    obj = await db.get(Invoice, id)

    if not obj:
        raise NotFoundError("Invoice not found")

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

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=invoice_{obj.id}.pdf"},
    )

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.owner import Owner, OwnerTransaction
from app.schemas.owner import (
    OwnerCreate,
    OwnerUpdate,
    OwnerOut,
    OwnerLedgerResponse,
    OwnerTransactionOut,
)
from app.utils.helpers import NotFoundError
from fastapi.responses import StreamingResponse
from io import BytesIO , StringIO
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
import csv
from fastapi.responses import StreamingResponse


router = APIRouter(
    prefix="/owners",
    tags=["owners"],
)


@router.post("", response_model=OwnerOut)
async def create_owner(
    payload: OwnerCreate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = Owner(**payload.model_dump())

    try:
        db.add(obj)
        await db.flush()
        await db.commit()
        await db.refresh(obj)
    except IntegrityError:
        await db.rollback()
        raise ValueError("Mobile number already exists")
    except Exception:
        await db.rollback()
        raise

    return OwnerOut.model_validate(obj)


@router.get("", response_model=list[OwnerOut])
async def list_owners(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(Owner)

    if search:
        query = query.where(Owner.owner_name.ilike(f"%{search}%"))

    result = await db.execute(query)
    owners = result.scalars().all()

    return [OwnerOut.model_validate(o) for o in owners]


@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    return OwnerOut.model_validate(obj)


@router.put("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: int,
    payload: OwnerUpdate,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    try:
        await db.flush()
        await db.commit()
        await db.refresh(obj)
    except IntegrityError:
        await db.rollback()
        raise ValueError("Mobile number already exists")
    except Exception:
        await db.rollback()
        raise

    return OwnerOut.model_validate(obj)


@router.delete("/{owner_id}", status_code=204)
async def delete_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    try:
        await db.delete(obj)
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return None


@router.get("/{owner_id}/payments")
async def get_owner_payments(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    owner = await db.get(Owner, owner_id)
    if not owner:
        raise NotFoundError("Owner not found")

    result = await db.execute(
        select(OwnerTransaction).where(OwnerTransaction.owner_id == owner_id)
    )
    rows = result.scalars().all()

    return [OwnerTransactionOut.model_validate(r) for r in rows]


@router.get("/{owner_id}/ledger", response_model=OwnerLedgerResponse)
async def get_owner_ledger(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    owner = await db.get(Owner, owner_id)
    if not owner:
        raise NotFoundError("Owner not found")

    result = await db.execute(
        select(OwnerTransaction).where(OwnerTransaction.owner_id == owner_id)
    )
    transactions = result.scalars().all()

    total_credit = sum(float(t.amount) for t in transactions if t.type == "credit")
    total_debit = sum(float(t.amount) for t in transactions if t.type == "debit")

    return OwnerLedgerResponse(
        total_credit=total_credit,
        total_debit=total_debit,
        balance=total_credit - total_debit,
        transactions=[OwnerTransactionOut.model_validate(t) for t in transactions],
    )


@router.get("/{owner_id}/ledger/pdf")
async def export_owner_ledger_pdf(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    owner = await db.get(Owner, owner_id)
    if not owner:
        raise NotFoundError("Owner not found")

    result = await db.execute(
        select(OwnerTransaction)
        .where(OwnerTransaction.owner_id == owner_id)
        .order_by(OwnerTransaction.created_at.desc())
    )
    transactions = result.scalars().all()

    total_credit = sum(float(t.amount) for t in transactions if t.type == "credit")
    total_debit = sum(float(t.amount) for t in transactions if t.type == "debit")

    buffer = BytesIO()

    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()

    elements = []

    # Title
    elements.append(Paragraph("OWNER LEDGER REPORT", styles["Title"]))
    elements.append(Spacer(1, 10))

    # Owner info
    elements.append(Paragraph(f"Owner: {owner.owner_name}", styles["Normal"]))
    elements.append(Spacer(1, 10))

    # Summary
    elements.append(Paragraph(f"Total Credit: {total_credit}", styles["Normal"]))
    elements.append(Paragraph(f"Total Debit: {total_debit}", styles["Normal"]))
    elements.append(Paragraph(f"Balance: {total_credit - total_debit}", styles["Normal"]))
    elements.append(Spacer(1, 15))

    # Table data
    data = [["Date", "Type", "Amount", "Reference", "Description"]]

    for t in transactions:
        data.append([
            str(t.created_at),
            t.type,
            float(t.amount),
            f"{t.reference_type} ({t.reference_id})",
            t.description or ""
        ])

    table = Table(data)

    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 1, colors.black),
    ]))

    elements.append(table)

    doc.build(elements)

    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=owner_ledger_{owner_id}.pdf"
        },
    )


@router.get("/{owner_id}/ledger/excel")
async def export_owner_ledger_excel(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    owner = await db.get(Owner, owner_id)
    if not owner:
        raise NotFoundError("Owner not found")

    result = await db.execute(
        select(OwnerTransaction)
        .where(OwnerTransaction.owner_id == owner_id)
        .order_by(OwnerTransaction.created_at.desc())
    )
    transactions = result.scalars().all()

    string_buffer = StringIO()
    writer = csv.writer(string_buffer)

    # HEADER
    writer.writerow([
        "Date",
        "Type",
        "Amount",
        "Reference Type",
        "Reference ID",
        "Description"
    ])

    # DATA
    for t in transactions:
        writer.writerow([
            str(t.created_at),
            t.type,
            float(t.amount),
            t.reference_type,
            t.reference_id,
            t.description or "",
        ])

    byte_buffer = BytesIO()
    byte_buffer.write(string_buffer.getvalue().encode("utf-8"))
    byte_buffer.seek(0)

    return StreamingResponse(
        byte_buffer,
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=owner_ledger_{owner_id}.csv"
        },
    )
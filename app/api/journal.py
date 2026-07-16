from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
import io, csv
import uuid
from datetime import date, timedelta
from fastapi import Query, UploadFile, File

from app.db.session import get_db_session
from app.models.accountant import JournalEntry, JournalLine, RecurringJournal
from app.models.approval import Approval
from app.models.user import User
from app.core.dependencies import get_current_user
from app.schemas.journal import (
    JournalManualCreate,
    JournalAdjustmentCreate,
    JournalEntryExtendedOut,
    RecurringJournalCreate,
    RecurringJournalOut
)
# Ensure we have roles if needed, or just rely on get_current_user for now

router = APIRouter(prefix="/journal", tags=["Journal Entries"])

def _generate_journal_number() -> str:
    # Just a simple generator for uniqueness
    return f"JRN-{uuid.uuid4().hex[:8].upper()}"

@router.post("/manual", response_model=JournalEntryExtendedOut)
async def create_manual_journal(
    payload: JournalManualCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    total_debit = sum(line.debit for line in payload.lines)
    total_credit = sum(line.credit for line in payload.lines)
    if total_debit != total_credit:
        raise HTTPException(status_code=400, detail="Debit and Credit must be equal")

    journal = JournalEntry(
        journal_number=_generate_journal_number(),
        entry_date=payload.entry_date,
        description=payload.description,
        status="Pending",
        entry_type="Manual",
        created_by=current_user.id
    )
    db.add(journal)
    await db.flush()

    for line in payload.lines:
        if line.debit == 0 and line.credit == 0:
            raise HTTPException(status_code=400, detail="Line cannot have both debit and credit = 0")
        db.add(JournalLine(entry_id=journal.id, account_id=line.account_id, debit=line.debit, credit=line.credit))

    approval = Approval(
        entity_type="journal_entry",
        entity_id=journal.id,
        status="Pending",
        requested_by=current_user.id
    )
    db.add(approval)
    await db.commit()
    await db.refresh(journal)
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.id == journal.id)
    return await db.scalar(query)

@router.get("/manual", response_model=List[JournalEntryExtendedOut])
async def get_manual_journals(db: AsyncSession = Depends(get_db_session)):
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.entry_type == "Manual").order_by(JournalEntry.created_at.desc())
    return (await db.scalars(query)).all()

@router.get("/manual/{id}", response_model=JournalEntryExtendedOut)
async def get_manual_journal_details(id: int, db: AsyncSession = Depends(get_db_session)):
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.id == id, JournalEntry.entry_type == "Manual")
    result = await db.scalar(query)
    if not result:
        raise HTTPException(status_code=404, detail="Journal entry not found")
    return result

@router.post("/adjustment", response_model=JournalEntryExtendedOut)
async def create_adjustment_journal(
    payload: JournalAdjustmentCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    total_debit = sum(line.debit for line in payload.lines)
    total_credit = sum(line.credit for line in payload.lines)
    if total_debit != total_credit:
        raise HTTPException(status_code=400, detail="Debit and Credit must be equal")

    journal = JournalEntry(
        journal_number=_generate_journal_number(),
        entry_date=payload.entry_date,
        description=payload.description,
        status="Pending",
        entry_type="Adjustment",
        created_by=current_user.id
    )
    db.add(journal)
    await db.flush()

    for line in payload.lines:
        if line.debit == 0 and line.credit == 0:
            raise HTTPException(status_code=400, detail="Line cannot have both debit and credit = 0")
        db.add(JournalLine(entry_id=journal.id, account_id=line.account_id, debit=line.debit, credit=line.credit))

    approval = Approval(
        entity_type="journal_entry",
        entity_id=journal.id,
        status="Pending",
        requested_by=current_user.id
    )
    db.add(approval)
    await db.commit()
    await db.refresh(journal)
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.id == journal.id)
    return await db.scalar(query)

@router.get("/adjustment", response_model=List[JournalEntryExtendedOut])
async def get_adjustment_journals(
    search: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db_session)
):
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.entry_type == "Adjustment")
    if search:
        query = query.where(JournalEntry.journal_number.ilike(f"%{search}%") | JournalEntry.description.ilike(f"%{search}%"))
    if status:
        query = query.where(JournalEntry.status == status)
    if from_date:
        query = query.where(JournalEntry.entry_date >= from_date)
    if to_date:
        query = query.where(JournalEntry.entry_date <= to_date)
        
    query = query.order_by(JournalEntry.created_at.desc())
    return (await db.scalars(query)).all()

@router.get("/adjustment/export")
async def export_adjustment_journals(db: AsyncSession = Depends(get_db_session)):
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.entry_type == "Adjustment").order_by(JournalEntry.created_at.desc())
    journals = (await db.scalars(query)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Adjustment No", "Date", "Reason", "Amount", "Status"])
    for j in journals:
        amount = sum(l.debit for l in j.lines)
        writer.writerow([
            j.journal_number or "",
            j.entry_date.isoformat() if j.entry_date else "",
            j.description or "",
            amount,
            j.status or "Pending"
        ])
    
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=adjustments_export.csv"},
    )

@router.post("/adjustment/import")
async def import_adjustment_journals(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session)
):
    import csv
    from datetime import date
    from app.models.accountant import Account

    content = await file.read()
    text = content.decode('utf-8')
    lines = text.splitlines()
    
    valid = 0
    errors = []
    
    reader = csv.reader(lines)
    try:
        next(reader)
    except StopIteration:
        pass

    journal_lines = []
    total_debit = 0.0
    total_credit = 0.0
    entry_date = date.today()

    for i, parts in enumerate(reader, start=2):
        if not parts or not any(parts):
            continue
        if len(parts) < 4:
            errors.append(f"Line {i}: Invalid format")
            continue
            
        date_str = parts[0].strip()
        account_id_str = parts[1].strip()
        debit_str = parts[2].strip() or '0'
        credit_str = parts[3].strip() or '0'

        try:
            row_date = date.fromisoformat(date_str)
            if valid == 0:
                entry_date = row_date
        except ValueError:
            errors.append(f"Line {i}: Invalid date format, expected YYYY-MM-DD")
            continue

        try:
            account_id = int(account_id_str)
        except ValueError:
            errors.append(f"Line {i}: Invalid account ID '{account_id_str}'")
            continue

        try:
            debit = float(debit_str)
            credit = float(credit_str)
        except ValueError:
            errors.append(f"Line {i}: Invalid debit/credit amount")
            continue

        acc = await db.get(Account, account_id)
        if not acc:
            errors.append(f"Line {i}: Account ID {account_id} not found")
            continue

        journal_lines.append(JournalLine(
            account_id=account_id,
            debit=debit,
            credit=credit
        ))
        total_debit += debit
        total_credit += credit
        valid += 1

    if valid > 0 and len(errors) == 0:
        if abs(total_debit - total_credit) > 0.01:
            errors.append(f"Debit and Credit must match. Debits: {total_debit}, Credits: {total_credit}")
        else:
            je = JournalEntry(
                entry_date=entry_date,
                description="Imported Adjustment Journal",
                entry_type="Adjustment",
                created_by=current_user.id
            )
            db.add(je)
            await db.flush()

            for jl in journal_lines:
                jl.entry_id = je.id
                db.add(jl)

            je.journal_number = f"ADJ-{je.id}"
            await db.commit()
    
    if len(errors) > 0:
        await db.rollback()
        
    return {
        "valid_records": valid if len(errors) == 0 else 0,
        "errors": errors,
        "message": "Import successful" if len(errors) == 0 else "Import failed due to errors"
    }

@router.get("/adjustment/{id}", response_model=JournalEntryExtendedOut)
async def get_adjustment_journal_details(id: int, db: AsyncSession = Depends(get_db_session)):
    query = select(JournalEntry).options(selectinload(JournalEntry.lines)).where(JournalEntry.id == id, JournalEntry.entry_type == "Adjustment")
    result = await db.scalar(query)
    if not result:
        raise HTTPException(status_code=404, detail="Adjustment journal entry not found")
    return result

@router.post("/recurring", response_model=RecurringJournalOut)
async def create_recurring_journal(
    payload: RecurringJournalCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    recurring = RecurringJournal(
        template_name=payload.template_name,
        frequency=payload.frequency,
        next_run_date=payload.next_run_date,
        template_data=payload.template_data,
        status="Active",
        created_by=current_user.id
    )
    db.add(recurring)
    await db.commit()
    await db.refresh(recurring)
    return recurring

@router.get("/recurring", response_model=List[RecurringJournalOut])
async def get_recurring_journals(db: AsyncSession = Depends(get_db_session)):
    query = select(RecurringJournal).order_by(RecurringJournal.created_at.desc())
    return (await db.scalars(query)).all()

@router.get("/recurring/export")
async def export_recurring_journals(db: AsyncSession = Depends(get_db_session)):
    query = select(RecurringJournal).order_by(RecurringJournal.created_at.desc())
    journals = (await db.scalars(query)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Template Name", "Frequency", "Next Run Date", "Amount", "Status"])
    for j in journals:
        amount = sum(line.get('debit', 0) for line in j.template_data.get('lines', [])) if j.template_data else 0
        writer.writerow([
            j.template_name,
            j.frequency,
            j.next_run_date.isoformat() if j.next_run_date else "",
            amount,
            j.status
        ])
    
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=recurring_export.csv"},
    )

@router.post("/recurring/run-due")
async def run_due_recurring_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    today = date.today()
    query = select(RecurringJournal).where(
        RecurringJournal.status == "Active",
        RecurringJournal.next_run_date <= today
    )
    due_journals = (await db.scalars(query)).all()
    
    generated = 0
    for r in due_journals:
        j_num = f"REC-{r.id}-{r.next_run_date.strftime('%Y%m%d')}"
        
        existing = await db.scalar(select(JournalEntry).where(JournalEntry.journal_number == j_num))
        if existing:
            continue
            
        data = r.template_data or {}
        
        j = JournalEntry(
            journal_number=j_num,
            entry_date=today,
            description=data.get('description', f"Recurring: {r.template_name}"),
            status="Pending",
            entry_type="Recurring",
            created_by=current_user.id
        )
        db.add(j)
        await db.flush()
        
        lines = data.get('lines', [])
        for line in lines:
            db.add(JournalLine(
                entry_id=j.id,
                account_id=line.get('account_id'),
                debit=line.get('debit', 0.0),
                credit=line.get('credit', 0.0)
            ))
            
        approval = Approval(
            entity_type="journal_entry",
            entity_id=j.id,
            status="Pending",
            requested_by=current_user.id
        )
        db.add(approval)
        
        if r.frequency == "Daily":
            r.next_run_date = r.next_run_date + timedelta(days=1)
        elif r.frequency == "Weekly":
            r.next_run_date = r.next_run_date + timedelta(days=7)
        elif r.frequency == "Monthly":
            month = r.next_run_date.month % 12 + 1
            year = r.next_run_date.year + (r.next_run_date.month // 12)
            try:
                r.next_run_date = r.next_run_date.replace(year=year, month=month)
            except ValueError:
                r.next_run_date = r.next_run_date.replace(year=year, month=month, day=28)
        elif r.frequency == "Yearly":
            r.next_run_date = r.next_run_date.replace(year=r.next_run_date.year + 1)
            
        generated += 1
        
    await db.commit()
    return {"message": f"Successfully generated {generated} recurring journals"}

@router.post("/recurring/{recurring_id}/toggle", response_model=RecurringJournalOut)
async def toggle_recurring_journal(
    recurring_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    recurring = await db.get(RecurringJournal, recurring_id)
    if not recurring:
        raise HTTPException(status_code=404, detail="Recurring Journal not found")
    
    recurring.status = "Paused" if recurring.status == "Active" else "Active"
    await db.commit()
    await db.refresh(recurring)
    return recurring

@router.get("/export")
async def export_journals(db: AsyncSession = Depends(get_db_session)):
    # Stream CSV
    query = select(JournalEntry).order_by(JournalEntry.created_at.desc())
    journals = (await db.scalars(query)).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Journal ID", "Journal Number", "Entry Date", "Type", "Status", "Description", "Created At"])
    for j in journals:
        writer.writerow([
            j.id,
            j.journal_number or "",
            j.entry_date or "",
            j.entry_type or "Auto",
            j.status or "Posted",
            j.description or "",
            j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else ""
        ])
    
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=journals_export.csv"},
    )

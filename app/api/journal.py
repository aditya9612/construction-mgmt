from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from typing import List, Optional
import io
import csv
import uuid
import logging
from datetime import date, timedelta
from decimal import Decimal

from app.db.session import get_db_session
from app.models.accountant import JournalEntry, JournalLine, RecurringJournal, Account
from app.models.approval import Approval
from app.models.user import User
from app.core.dependencies import require_permission
from app.schemas.journal import (
    JournalManualCreate,
    JournalAdjustmentCreate,
    JournalEntryExtendedOut,
    RecurringJournalCreate,
    RecurringJournalOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/journal", tags=["Journal Entries"])


# ---------------------------------------------------------------------------
# Helper: generate unique journal number
# ---------------------------------------------------------------------------
def _generate_journal_number() -> str:
    return f"JRN-{uuid.uuid4().hex[:8].upper()}"


# ---------------------------------------------------------------------------
# Helper: validate account ownership and return (account, company_id)
# ---------------------------------------------------------------------------
async def _validate_and_get_account(
    db: AsyncSession,
    account_id: int,
    expected_company_id: Optional[int],
    line_label: str = "account",
) -> Account:
    """
    Fetch account and verify it exists and belongs to the expected company.
    Raises HTTPException 404 if account does not exist or belongs to a foreign company.
    If expected_company_id is None (Super Admin establishing ownership), just verifies existence.
    """
    acc = await db.get(Account, account_id)
    if not acc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{line_label} ID {account_id} not found.",
        )
    if expected_company_id is not None and acc.company_id != expected_company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{line_label} ID {account_id} not found.",
        )
    return acc


# ---------------------------------------------------------------------------
# Helper: validate line amounts
# ---------------------------------------------------------------------------
def _validate_line_amounts(debit: Decimal, credit: Decimal, line_index: int):
    if debit < 0 or credit < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Line {line_index}: debit and credit must be non-negative.",
        )
    if debit == 0 and credit == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Line {line_index}: line cannot have both debit and credit equal to zero.",
        )


# ---------------------------------------------------------------------------
# Helper: build tenant filter clause for JournalEntry
# Since JournalEntry has no direct company_id, we scope via created_by -> User
# For list queries, we do a subquery on users that belong to the company.
# ---------------------------------------------------------------------------
async def _get_company_user_ids(db: AsyncSession, company_id: int) -> List[int]:
    res = await db.execute(
        select(User.id).where(User.company_id == company_id)
    )
    return list(res.scalars().all())


# ---------------------------------------------------------------------------
# Helper: check if a journal entry belongs to a given company
# by verifying created_by user's company or any of its journal lines' account company
# We use journal lines as the authoritative financial ownership boundary.
# ---------------------------------------------------------------------------
async def _get_journal_company_id(db: AsyncSession, journal: JournalEntry) -> Optional[int]:
    """
    Determine the owning company of a JournalEntry by inspecting its JournalLines.
    Returns the company_id if all lines belong to the same company, None if no lines,
    or raises if mixed.
    """
    if not journal.lines:
        # Fall back to creator's company
        if journal.created_by:
            creator = await db.get(User, journal.created_by)
            if creator:
                return creator.company_id
        return None

    company_ids: set = set()
    for line in journal.lines:
        acc = await db.get(Account, line.account_id)
        if acc:
            company_ids.add(acc.company_id)

    if len(company_ids) == 1:
        return next(iter(company_ids))
    return None  # mixed or missing


# ---------------------------------------------------------------------------
# Helper: fetch own-company journal entry (with lines loaded)
# ---------------------------------------------------------------------------
async def _get_own_journal_or_404(
    db: AsyncSession,
    journal_id: int,
    company_id: Optional[int],
    entry_type: Optional[str] = None,
) -> JournalEntry:
    """
    Fetch a JournalEntry with its lines. For non-SA, scope to company. 404 if not found/foreign.
    """
    query = (
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.id == journal_id)
    )
    if entry_type:
        query = query.where(JournalEntry.entry_type == entry_type)
    journal = await db.scalar(query)
    if not journal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journal entry not found.")

    if company_id is not None:
        # Verify ownership via lines' account company or creator's company
        owned = await _is_journal_owned_by_company(db, journal, company_id)
        if not owned:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Journal entry not found.")
    return journal


# ---------------------------------------------------------------------------
# Helper: check if a journal belongs to a company
# ---------------------------------------------------------------------------
async def _is_journal_owned_by_company(
    db: AsyncSession, journal: JournalEntry, company_id: int
) -> bool:
    """
    Returns True if the journal is owned by the given company.
    Checks lines' accounts first; falls back to creator's company.
    """
    if journal.lines:
        for line in journal.lines:
            acc = await db.get(Account, line.account_id)
            if acc and acc.company_id != company_id:
                return False
            if acc and acc.company_id == company_id:
                return True
        # If all lines checked but none matched (shouldn't happen), fall through
    # Fallback: creator's company
    if journal.created_by:
        creator = await db.get(User, journal.created_by)
        if creator and creator.company_id == company_id:
            return True
    return False


# ===========================================================================
# 1. POST /api/v1/journal/manual  →  journal.create
# ===========================================================================
@router.post("/manual", response_model=JournalEntryExtendedOut)
async def create_manual_journal(
    payload: JournalManualCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    if not payload.lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Journal entry must have at least one line.")

    # Validate all line amounts and determine owning company
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    target_company_id: Optional[int] = None if is_sa else current_user.company_id

    for i, line in enumerate(payload.lines, start=1):
        debit = Decimal(str(line.debit))
        credit = Decimal(str(line.credit))
        _validate_line_amounts(debit, credit, i)
        total_debit += debit
        total_credit += credit

    # Validate accounts and enforce single-company constraint
    resolved_company_id: Optional[int] = target_company_id
    for i, line in enumerate(payload.lines, start=1):
        acc = await _validate_and_get_account(db, line.account_id, target_company_id, f"Line {i} account")
        if is_sa:
            # For SA: determine company from first account, then enforce consistency
            if resolved_company_id is None:
                resolved_company_id = acc.company_id
            elif acc.company_id != resolved_company_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All journal lines must belong to the same company. Mixed-company accounts are not allowed.",
                )

    if abs(total_debit - total_credit) > Decimal("0.001"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Total debit ({total_debit}) must equal total credit ({total_credit}).",
        )

    try:
        journal = JournalEntry(
            journal_number=_generate_journal_number(),
            entry_date=payload.entry_date,
            description=payload.description,
            status="Pending",
            entry_type="Manual",
            created_by=current_user.id,
        )
        db.add(journal)
        await db.flush()

        for line in payload.lines:
            db.add(JournalLine(
                entry_id=journal.id,
                account_id=line.account_id,
                debit=Decimal(str(line.debit)),
                credit=Decimal(str(line.credit)),
            ))

        approval = Approval(
            entity_type="journal_entry",
            entity_id=journal.id,
            status="Pending",
            requested_by=current_user.id,
        )
        db.add(approval)
        await db.commit()

        result = await db.scalar(
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.id == journal.id)
        )
        return result

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error creating manual journal entry.")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 2. GET /api/v1/journal/manual  →  journal.view
# ===========================================================================
@router.get("/manual", response_model=List[JournalEntryExtendedOut])
async def get_manual_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    query = (
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.entry_type == "Manual")
        .order_by(JournalEntry.created_at.desc())
    )

    if not is_sa:
        company_user_ids = await _get_company_user_ids(db, current_user.company_id)
        query = query.where(JournalEntry.created_by.in_(company_user_ids))

    return (await db.scalars(query)).all()


# ===========================================================================
# 3. GET /api/v1/journal/manual/{id}  →  journal.view
# ===========================================================================
@router.get("/manual/{id}", response_model=JournalEntryExtendedOut)
async def get_manual_journal_details(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    company_id = None if is_sa else current_user.company_id
    return await _get_own_journal_or_404(db, id, company_id, entry_type="Manual")


# ===========================================================================
# 4. POST /api/v1/journal/adjustment  →  journal.create
# ===========================================================================
@router.post("/adjustment", response_model=JournalEntryExtendedOut)
async def create_adjustment_journal(
    payload: JournalAdjustmentCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    if not payload.lines:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Journal entry must have at least one line.")

    total_debit = Decimal("0")
    total_credit = Decimal("0")
    target_company_id: Optional[int] = None if is_sa else current_user.company_id

    for i, line in enumerate(payload.lines, start=1):
        debit = Decimal(str(line.debit))
        credit = Decimal(str(line.credit))
        _validate_line_amounts(debit, credit, i)
        total_debit += debit
        total_credit += credit

    resolved_company_id: Optional[int] = target_company_id
    for i, line in enumerate(payload.lines, start=1):
        acc = await _validate_and_get_account(db, line.account_id, target_company_id, f"Line {i} account")
        if is_sa:
            if resolved_company_id is None:
                resolved_company_id = acc.company_id
            elif acc.company_id != resolved_company_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All journal lines must belong to the same company. Mixed-company accounts are not allowed.",
                )

    if abs(total_debit - total_credit) > Decimal("0.001"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Total debit ({total_debit}) must equal total credit ({total_credit}).",
        )

    try:
        journal = JournalEntry(
            journal_number=_generate_journal_number(),
            entry_date=payload.entry_date,
            description=payload.description,
            status="Pending",
            entry_type="Adjustment",
            created_by=current_user.id,
        )
        db.add(journal)
        await db.flush()

        for line in payload.lines:
            db.add(JournalLine(
                entry_id=journal.id,
                account_id=line.account_id,
                debit=Decimal(str(line.debit)),
                credit=Decimal(str(line.credit)),
            ))

        approval = Approval(
            entity_type="journal_entry",
            entity_id=journal.id,
            status="Pending",
            requested_by=current_user.id,
        )
        db.add(approval)
        await db.commit()

        result = await db.scalar(
            select(JournalEntry)
            .options(selectinload(JournalEntry.lines))
            .where(JournalEntry.id == journal.id)
        )
        return result

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error creating adjustment journal entry.")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 5. GET /api/v1/journal/adjustment  →  journal.view
# ===========================================================================
@router.get("/adjustment", response_model=List[JournalEntryExtendedOut])
async def get_adjustment_journals(
    search: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    query = (
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.entry_type == "Adjustment")
    )

    if not is_sa:
        company_user_ids = await _get_company_user_ids(db, current_user.company_id)
        query = query.where(JournalEntry.created_by.in_(company_user_ids))

    if search:
        query = query.where(
            JournalEntry.journal_number.ilike(f"%{search}%") | JournalEntry.description.ilike(f"%{search}%")
        )
    if status:
        query = query.where(JournalEntry.status == status)
    if from_date:
        query = query.where(JournalEntry.entry_date >= from_date)
    if to_date:
        query = query.where(JournalEntry.entry_date <= to_date)

    query = query.order_by(JournalEntry.created_at.desc())
    return (await db.scalars(query)).all()


# ===========================================================================
# 6. GET /api/v1/journal/adjustment/export  →  journal.export
# ===========================================================================
@router.get("/adjustment/export")
async def export_adjustment_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    query = (
        select(JournalEntry)
        .options(selectinload(JournalEntry.lines))
        .where(JournalEntry.entry_type == "Adjustment")
        .order_by(JournalEntry.created_at.desc())
    )

    if not is_sa:
        company_user_ids = await _get_company_user_ids(db, current_user.company_id)
        query = query.where(JournalEntry.created_by.in_(company_user_ids))

    try:
        journals = (await db.scalars(query)).all()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Adjustment No", "Date", "Reason", "Amount", "Status"])
        for j in journals:
            amount = sum(Decimal(str(l.debit)) for l in j.lines)
            writer.writerow([
                j.journal_number or "",
                j.entry_date.isoformat() if j.entry_date else "",
                j.description or "",
                amount,
                j.status or "Pending",
            ])

        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=adjustments_export.csv"},
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error exporting adjustment journals.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 7. POST /api/v1/journal/adjustment/import  →  journal.create
# ===========================================================================
@router.post("/adjustment/import")
async def import_adjustment_journals(
    file: UploadFile = File(...),
    current_user: User = Depends(require_permission("journal.create")),
    db: AsyncSession = Depends(get_db_session),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only CSV files are allowed.")

    try:
        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file too large (max 5MB).")

        text = content.decode("utf-8")
        lines_text = text.splitlines()

        valid = 0
        errors: list = []
        reader = csv.reader(lines_text)

        try:
            next(reader)  # skip header
        except StopIteration:
            return {"valid_records": 0, "errors": ["CSV file is empty."], "message": "Import failed due to errors."}

        journal_lines_to_insert: list = []
        total_debit = Decimal("0")
        total_credit = Decimal("0")
        entry_date = date.today()
        resolved_company_id: Optional[int] = None if is_sa else current_user.company_id

        for i, parts in enumerate(reader, start=2):
            if not parts or not any(parts):
                continue
            if len(parts) < 4:
                errors.append(f"Line {i}: Invalid format, expected at least 4 columns (Date, AccountID, Debit, Credit).")
                continue

            date_str = parts[0].strip()
            account_id_str = parts[1].strip()
            debit_str = parts[2].strip() or "0"
            credit_str = parts[3].strip() or "0"

            try:
                row_date = date.fromisoformat(date_str)
                if valid == 0:
                    entry_date = row_date
            except ValueError:
                errors.append(f"Line {i}: Invalid date format '{date_str}', expected YYYY-MM-DD.")
                continue

            try:
                account_id = int(account_id_str)
            except ValueError:
                errors.append(f"Line {i}: Invalid account ID '{account_id_str}'.")
                continue

            try:
                debit = Decimal(debit_str)
                credit = Decimal(credit_str)
            except Exception:
                errors.append(f"Line {i}: Invalid debit/credit amount.")
                continue

            if debit < 0 or credit < 0:
                errors.append(f"Line {i}: Debit and credit must be non-negative.")
                continue

            if debit == 0 and credit == 0:
                errors.append(f"Line {i}: Line cannot have both debit and credit equal to zero.")
                continue

            # Tenant-scoped account verification
            acc = await db.get(Account, account_id)
            if not acc:
                errors.append(f"Line {i}: Account ID {account_id} not found.")
                continue

            if is_sa:
                if resolved_company_id is None:
                    resolved_company_id = acc.company_id
                elif acc.company_id != resolved_company_id:
                    errors.append(f"Line {i}: Account ID {account_id} belongs to a different company. Mixed-company imports are not allowed.")
                    continue
            else:
                if acc.company_id != current_user.company_id:
                    errors.append(f"Line {i}: Account ID {account_id} not found.")
                    continue

            journal_lines_to_insert.append({"account_id": account_id, "debit": debit, "credit": credit})
            total_debit += debit
            total_credit += credit
            valid += 1

        if errors:
            return {"valid_records": 0, "errors": errors, "message": "Import failed due to errors."}

        if valid == 0:
            return {"valid_records": 0, "errors": ["No valid rows found in CSV."], "message": "Import failed due to errors."}

        if abs(total_debit - total_credit) > Decimal("0.01"):
            return {
                "valid_records": 0,
                "errors": [f"Debit and Credit must match. Debits: {total_debit}, Credits: {total_credit}"],
                "message": "Import failed due to errors.",
            }

        # Commit the import
        je = JournalEntry(
            entry_date=entry_date,
            description="Imported Adjustment Journal",
            entry_type="Adjustment",
            status="Pending",
            created_by=current_user.id,
        )
        db.add(je)
        await db.flush()

        for jl_data in journal_lines_to_insert:
            db.add(JournalLine(
                entry_id=je.id,
                account_id=jl_data["account_id"],
                debit=jl_data["debit"],
                credit=jl_data["credit"],
            ))

        je.journal_number = f"ADJ-{je.id}"
        await db.commit()

        return {"valid_records": valid, "errors": [], "message": "Import successful."}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error importing adjustment journals.")
        try:
            await db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 8. GET /api/v1/journal/adjustment/{id}  →  journal.view
# ===========================================================================
@router.get("/adjustment/{id}", response_model=JournalEntryExtendedOut)
async def get_adjustment_journal_details(
    id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    company_id = None if is_sa else current_user.company_id
    return await _get_own_journal_or_404(db, id, company_id, entry_type="Adjustment")


# ===========================================================================
# 9. POST /api/v1/journal/recurring  →  journal.create
# ===========================================================================
ALLOWED_FREQUENCIES = {"Daily", "Weekly", "Monthly", "Yearly"}


@router.post("/recurring", response_model=RecurringJournalOut)
async def create_recurring_journal(
    payload: RecurringJournalCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    if payload.frequency not in ALLOWED_FREQUENCIES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid frequency '{payload.frequency}'. Allowed: {sorted(ALLOWED_FREQUENCIES)}",
        )

    # Validate template_data accounts
    template_data = payload.template_data
    if not isinstance(template_data, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="template_data must be a JSON object.")

    template_lines = template_data.get("lines", [])
    if not isinstance(template_lines, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="template_data.lines must be a list.")

    target_company_id: Optional[int] = None if is_sa else current_user.company_id
    resolved_company_id: Optional[int] = target_company_id

    for i, tl in enumerate(template_lines, start=1):
        if not isinstance(tl, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"template_data.lines[{i}] must be an object.")
        tid = tl.get("account_id")
        if tid is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"template_data.lines[{i}] missing account_id.")
        acc = await _validate_and_get_account(db, int(tid), target_company_id, f"Template line {i} account")
        if is_sa:
            if resolved_company_id is None:
                resolved_company_id = acc.company_id
            elif acc.company_id != resolved_company_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All template lines must belong to the same company.",
                )

    try:
        recurring = RecurringJournal(
            template_name=payload.template_name,
            frequency=payload.frequency,
            next_run_date=payload.next_run_date,
            template_data=payload.template_data,
            status="Active",
            created_by=current_user.id,
        )
        db.add(recurring)
        await db.commit()
        await db.refresh(recurring)
        return recurring

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error creating recurring journal.")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 10. GET /api/v1/journal/recurring  →  journal.view
# ===========================================================================
@router.get("/recurring", response_model=List[RecurringJournalOut])
async def get_recurring_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    query = select(RecurringJournal).order_by(RecurringJournal.created_at.desc())

    if not is_sa:
        company_user_ids = await _get_company_user_ids(db, current_user.company_id)
        query = query.where(RecurringJournal.created_by.in_(company_user_ids))

    return (await db.scalars(query)).all()


# ===========================================================================
# 11. GET /api/v1/journal/recurring/export  →  journal.export
# ===========================================================================
@router.get("/recurring/export")
async def export_recurring_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    query = select(RecurringJournal).order_by(RecurringJournal.created_at.desc())

    if not is_sa:
        company_user_ids = await _get_company_user_ids(db, current_user.company_id)
        query = query.where(RecurringJournal.created_by.in_(company_user_ids))

    try:
        journals = (await db.scalars(query)).all()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["Template Name", "Frequency", "Next Run Date", "Amount", "Status"])
        for j in journals:
            amount = sum(
                line.get("debit", 0)
                for line in j.template_data.get("lines", [])
            ) if j.template_data else 0
            writer.writerow([
                j.template_name,
                j.frequency,
                j.next_run_date.isoformat() if j.next_run_date else "",
                amount,
                j.status,
            ])

        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=recurring_export.csv"},
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error exporting recurring journals.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 12. POST /api/v1/journal/recurring/run-due  →  journal.create
# ===========================================================================
@router.post("/recurring/run-due")
async def run_due_recurring_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    today = date.today()

    try:
        query = select(RecurringJournal).where(
            RecurringJournal.status == "Active",
            RecurringJournal.next_run_date <= today,
        )

        if not is_sa:
            company_user_ids = await _get_company_user_ids(db, current_user.company_id)
            query = query.where(RecurringJournal.created_by.in_(company_user_ids))

        due_journals = (await db.scalars(query)).all()

        generated = 0
        for r in due_journals:
            j_num = f"REC-{r.id}-{r.next_run_date.strftime('%Y%m%d')}"

            existing = await db.scalar(
                select(JournalEntry).where(JournalEntry.journal_number == j_num)
            )
            if existing:
                continue

            data = r.template_data or {}
            template_lines = data.get("lines", [])

            # Validate template account ownership before creating any JournalEntry
            template_company_id: Optional[int] = None
            valid_template = True
            for tl in template_lines:
                tid = tl.get("account_id")
                if tid is None:
                    valid_template = False
                    break
                acc = await db.get(Account, int(tid))
                if not acc:
                    valid_template = False
                    break
                if template_company_id is None:
                    template_company_id = acc.company_id
                elif acc.company_id != template_company_id:
                    valid_template = False
                    break

            if not valid_template:
                logger.warning(f"Recurring journal {r.id} has invalid template accounts. Skipping.")
                continue

            j = JournalEntry(
                journal_number=j_num,
                entry_date=today,
                description=data.get("description", f"Recurring: {r.template_name}"),
                status="Pending",
                entry_type="Recurring",
                created_by=current_user.id,
            )
            db.add(j)
            await db.flush()

            for tl in template_lines:
                db.add(JournalLine(
                    entry_id=j.id,
                    account_id=int(tl.get("account_id")),
                    debit=Decimal(str(tl.get("debit", 0))),
                    credit=Decimal(str(tl.get("credit", 0))),
                ))

            approval = Approval(
                entity_type="journal_entry",
                entity_id=j.id,
                status="Pending",
                requested_by=current_user.id,
            )
            db.add(approval)

            # Advance next_run_date
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
        return {"message": f"Successfully generated {generated} recurring journals."}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error running due recurring journals.")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 13. POST /api/v1/journal/recurring/{recurring_id}/toggle  →  journal.edit
# ===========================================================================
@router.post("/recurring/{recurring_id}/toggle", response_model=RecurringJournalOut)
async def toggle_recurring_journal(
    recurring_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.edit")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    try:
        recurring = await db.get(RecurringJournal, recurring_id)
        if not recurring:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring journal not found.")

        if not is_sa:
            # Verify tenant ownership via creator's company
            if recurring.created_by:
                creator = await db.get(User, recurring.created_by)
                if not creator or creator.company_id != current_user.company_id:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring journal not found.")
            else:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurring journal not found.")

        recurring.status = "Paused" if recurring.status == "Active" else "Active"
        await db.commit()
        await db.refresh(recurring)
        return recurring

    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error toggling recurring journal.")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")


# ===========================================================================
# 14. GET /api/v1/journal/export  →  journal.export
# ===========================================================================
@router.get("/export")
async def export_journals(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("journal.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User does not belong to any company.")

    query = select(JournalEntry).order_by(JournalEntry.created_at.desc())

    if not is_sa:
        company_user_ids = await _get_company_user_ids(db, current_user.company_id)
        query = query.where(JournalEntry.created_by.in_(company_user_ids))

    try:
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
                j.created_at.strftime("%Y-%m-%d %H:%M:%S") if j.created_at else "",
            ])

        buffer.seek(0)
        return StreamingResponse(
            iter([buffer.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=journals_export.csv"},
        )
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error exporting journals.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An internal error occurred.")

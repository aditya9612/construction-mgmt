from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from decimal import Decimal
from datetime import datetime, date
import io, csv

from app.db.session import get_db_session
from app.core.dependencies import require_permission, require_feature
from app.core.logger import logger
from app.models.user import UserRole
from app.models.user import User
from app.models.project import Project
from app.models.invoice import Transaction
from app.models.accountant import JournalEntry, JournalLine, Account, BankAccount
from app.models.billing import RABill
from app.models.invoice import Invoice
from app.models.labour import Labour, LabourAttendance, LabourPayroll, PayrollStatus
from app.schemas.payroll import (
    StaffSalaryProcessRequest,
    StaffSalaryRegisterOut,
)
from app.utils.accounting import get_payroll_account, get_primary_cash_account

router = APIRouter(
    prefix="/accountant/payroll",
    tags=["Accountant Payroll"],
    dependencies=[Depends(require_feature("payroll", "Payroll Module"))],
)


def get_allowed_staff_roles():
    return [
        UserRole.ADMIN.value,
        UserRole.PROJECT_MANAGER.value,
        UserRole.SITE_ENGINEER.value,
        UserRole.ACCOUNTANT.value,
    ]


@router.get("/summary", deprecated=True)
async def payroll_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    pending_stmt = select(
        func.sum(LabourPayroll.total_wage - LabourPayroll.paid_amount)
    ).where(LabourPayroll.status == PayrollStatus.PENDING)
    if not is_sa:
        pending_stmt = pending_stmt.join(
            Project, LabourPayroll.project_id == Project.id
        ).where(Project.company_id == current_user.company_id)

    pending_payroll_query = await db.scalar(pending_stmt)
    pending_payroll = float(pending_payroll_query or 0.0)

    paid_stmt = select(
        func.sum(LabourPayroll.paid_amount)
    ).where(LabourPayroll.status == PayrollStatus.PAID)
    if not is_sa:
        paid_stmt = paid_stmt.join(
            Project, LabourPayroll.project_id == Project.id
        ).where(Project.company_id == current_user.company_id)

    paid_payroll_query = await db.scalar(paid_stmt)
    paid_payroll = float(paid_payroll_query or 0.0)

    adv_stmt = select(
        func.sum(LabourPayroll.advance_adjusted)
    )
    if not is_sa:
        adv_stmt = adv_stmt.join(
            Project, LabourPayroll.project_id == Project.id
        ).where(Project.company_id == current_user.company_id)

    advance_given_query = await db.scalar(adv_stmt)
    advance_given = float(advance_given_query or 0.0)

    contractor_payment = 0.0  # Placeholder logic for Contractor payment

    return {
        "pending_payroll": pending_payroll,
        "paid_payroll": paid_payroll,
        "advance_given": advance_given,
        "contractor_payment": contractor_payment,
    }


@router.get("/payslip/export", deprecated=True)
async def export_payslips(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = (
        select(LabourPayroll)
        .options(selectinload(LabourPayroll.labour))
        .order_by(LabourPayroll.created_at.desc())
    )
    if not is_sa:
        stmt = stmt.join(
            Project, LabourPayroll.project_id == Project.id
        ).where(Project.company_id == current_user.company_id)

    result = await db.execute(stmt)
    payrolls = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Employee/Labour Name", "Period", "Gross Pay", "Deduction", "Net Pay", "Status", "Payment Date"])

    for p in payrolls:
        period = f"{p.month}/{p.year}"
        name = p.labour.labour_name if p.labour else "Unknown"
        net_pay = float(p.total_wage or 0) - float(p.advance_adjusted or 0)
        writer.writerow([
            name,
            period,
            float(p.total_wage or 0),
            float(p.advance_adjusted or 0),
            net_pay,
            p.status.value if hasattr(p.status, "value") else str(p.status),
            p.updated_at.strftime("%Y-%m-%d") if p.updated_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payslips_export.csv"},
    )


# ================= STAFF SALARY =================

@router.get("/staff/register")
async def get_staff_register(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = select(User).where(User.role.in_(get_allowed_staff_roles()))
    if not is_sa:
        stmt = stmt.where(User.company_id == current_user.company_id)

    result = await db.execute(stmt)
    users = result.scalars().all()

    return [{"user_id": u.id, "full_name": u.full_name, "role": u.role, "designation": u.designation} for u in users]


@router.post("/staff/process")
async def process_staff_salary(
    payload: StaffSalaryProcessRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.create")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    # 1. Verify User (tenant scoped, masked 404 for foreign or non-existent)
    staff_stmt = select(User).where(User.id == payload.user_id)
    if not is_sa:
        staff_stmt = staff_stmt.where(User.company_id == current_user.company_id)
    staff = await db.scalar(staff_stmt)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff user not found")

    if staff.role not in get_allowed_staff_roles():
        raise HTTPException(status_code=400, detail="Invalid staff user")

    # 2. Verify Project (tenant scoped, masked 404 for foreign or non-existent)
    proj_stmt = select(Project).where(Project.id == payload.project_id)
    if not is_sa:
        proj_stmt = proj_stmt.where(Project.company_id == current_user.company_id)
    proj = await db.scalar(proj_stmt)
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    # 3. Duplicate Protection
    linked_id = f"STAFF-SALARY:{payload.user_id}:{payload.month_year}"
    existing = await db.scalar(select(Transaction).where(Transaction.linked_to == linked_id))
    if existing:
        raise HTTPException(status_code=400, detail="Salary already processed for this month")

    # 4. Get Payroll Account Mapping
    try:
        staff_acc = await get_payroll_account(db, "staff_salary_account_id")
    except ValueError:
        raise HTTPException(status_code=400, detail="Payroll account mapping not configured")

    # 5. Resolve Payment Account
    if payload.payment_mode == "cash":
        try:
            pay_acc = await get_primary_cash_account(db)
        except ValueError:
            raise HTTPException(status_code=400, detail="Primary cash account not configured")
    else:
        if not payload.bank_account_id:
            raise HTTPException(status_code=400, detail="Bank account required for bank payment")
        bank_stmt = (
            select(BankAccount, Account)
            .join(Account, BankAccount.account_id == Account.id)
            .where(BankAccount.id == payload.bank_account_id)
        )
        if not is_sa:
            bank_stmt = bank_stmt.where(Account.company_id == current_user.company_id)
        bank_res = await db.execute(bank_stmt)
        bank_row = bank_res.first()
        if not bank_row:
            raise HTTPException(status_code=404, detail="Bank account not found")
        bank, pay_acc = bank_row

    try:
        # 6. Create Transaction
        serialized_ref = f"gross:{payload.gross_salary}|deduct:{payload.deductions}"
        txn = Transaction(
            project_id=payload.project_id,
            type="payment",
            amount=payload.net_salary,
            mode=payload.payment_mode,
            reference=serialized_ref,
            linked_to=linked_id,
            created_by=current_user.id,
        )
        db.add(txn)

        # 7. Create Journal Entry and Lines
        je = JournalEntry(
            description=f"Staff Salary Payment for {payload.month_year}",
            created_by=current_user.id,
        )
        db.add(je)
        await db.flush()

        txn.journal_entry_id = je.id

        db.add(JournalLine(entry_id=je.id, account_id=staff_acc.id, debit=payload.net_salary, credit=0))
        db.add(JournalLine(entry_id=je.id, account_id=pay_acc.id, debit=0, credit=payload.net_salary))

        await db.commit()
        return {"message": "Staff salary processed successfully"}
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        logger.exception("Failed to process staff salary")
        raise HTTPException(status_code=500, detail="Failed to process staff salary")


@router.get("/staff/history")
async def get_staff_history(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = (
        select(Transaction)
        .where(Transaction.linked_to.like("STAFF-SALARY:%"))
        .order_by(Transaction.created_at.desc())
    )
    if not is_sa:
        stmt = (
            select(Transaction)
            .join(Project, Transaction.project_id == Project.id)
            .where(
                Transaction.linked_to.like("STAFF-SALARY:%"),
                Project.company_id == current_user.company_id,
            )
            .order_by(Transaction.created_at.desc())
        )

    result = await db.execute(stmt)
    return result.scalars().all()


# ================= LABOUR PAYROLL =================

@router.get("/labour/wages", deprecated=True)
async def get_labour_wages(
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = select(Labour)
    if not is_sa:
        stmt = stmt.where(Labour.company_id == current_user.company_id)

    labours = (await db.execute(stmt)).scalars().all()

    results = []
    for labour in labours:
        att_stmt = select(LabourAttendance).where(
            LabourAttendance.labour_id == labour.id,
            LabourAttendance.attendance_date >= start_date,
            LabourAttendance.attendance_date <= end_date,
        )
        attendances = (await db.execute(att_stmt)).scalars().all()

        total_hours = sum(a.working_hours or 0 for a in attendances)
        wage = total_hours * 50

        results.append({
            "labour_id": labour.id,
            "full_name": labour.labour_name,
            "total_hours": total_hours,
            "calculated_wage": wage,
        })
    return results


# ================= CONTRACTOR PAYROLL =================

@router.get("/contractor/bills", deprecated=True)
async def get_contractor_bills(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = select(RABill).where(RABill.status != "Paid").order_by(RABill.created_at.desc())
    if not is_sa:
        stmt = (
            select(RABill)
            .join(Project, RABill.project_id == Project.id)
            .where(
                RABill.status != "Paid",
                Project.company_id == current_user.company_id,
            )
            .order_by(RABill.created_at.desc())
        )

    return (await db.execute(stmt)).scalars().all()


@router.get("/staff/export")
async def export_staff_payroll(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = (
        select(Transaction)
        .where(Transaction.linked_to.like("STAFF-SALARY:%"))
        .order_by(Transaction.created_at.desc())
    )
    user_stmt = select(User)
    if not is_sa:
        stmt = (
            select(Transaction)
            .join(Project, Transaction.project_id == Project.id)
            .where(
                Transaction.linked_to.like("STAFF-SALARY:%"),
                Project.company_id == current_user.company_id,
            )
            .order_by(Transaction.created_at.desc())
        )
        user_stmt = user_stmt.where(User.company_id == current_user.company_id)

    txns = (await db.execute(stmt)).scalars().all()
    users = (await db.execute(user_stmt)).scalars().all()
    user_map = {u.id: u for u in users}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Staff Name", "Role", "Department", "Designation", "Month", "Gross Salary", "Deductions", "Net Salary", "Payment Status", "Payment Date"])

    for t in txns:
        parts = t.linked_to.split(":")
        if len(parts) >= 3:
            uid = int(parts[1])
            month = parts[2]
            u = user_map.get(uid)

            gross = 0.0
            deduct = 0.0
            if t.reference:
                for ref_part in t.reference.split("|"):
                    if ref_part.startswith("gross:"):
                        gross = float(ref_part.split(":")[1])
                    if ref_part.startswith("deduct:"):
                        deduct = float(ref_part.split(":")[1])

            writer.writerow([
                u.full_name if u else "Unknown",
                u.role if u else "",
                u.department if u else "",
                u.designation if u else "",
                month,
                gross,
                deduct,
                float(t.amount),
                "PAID",
                t.created_at.date().isoformat() if t.created_at else "",
            ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=staff_salary.csv"},
    )


@router.get("/contractor/export", deprecated=True)
async def export_contractor_payroll(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = select(RABill).order_by(RABill.created_at.desc())
    if not is_sa:
        stmt = (
            select(RABill)
            .join(Project, RABill.project_id == Project.id)
            .where(Project.company_id == current_user.company_id)
            .order_by(RABill.created_at.desc())
        )

    bills = (await db.execute(stmt)).scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Contractor", "Project", "Bill Number", "Gross Amount", "Deductions", "Net Payable", "Payment Status", "Payment Date"])

    for b in bills:
        writer.writerow([
            f"Contractor {b.contractor_id}" if b.contractor_id else "Unknown",
            f"Project {b.project_id}" if b.project_id else "Unknown",
            b.bill_number,
            float(b.gross_amount) if b.gross_amount else 0.0,
            float(b.deductions) if b.deductions else 0.0,
            float(b.net_amount) if b.net_amount else 0.0,
            b.status,
            b.bill_date.isoformat() if b.bill_date else "",
        ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=contractor_payments.csv"},
    )


@router.get("/register/export", deprecated=True)
async def export_payroll_register(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.export")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = select(Transaction).where(
        Transaction.linked_to.like("STAFF-SALARY:%") |
        Transaction.linked_to.like("LABOUR-WAGE:%") |
        Transaction.linked_to.like("CONTRACTOR-PAY:%")
    ).order_by(Transaction.created_at.desc())

    user_stmt = select(User)
    labour_stmt = select(Labour)

    if not is_sa:
        stmt = (
            select(Transaction)
            .join(Project, Transaction.project_id == Project.id)
            .where(
                (
                    Transaction.linked_to.like("STAFF-SALARY:%") |
                    Transaction.linked_to.like("LABOUR-WAGE:%") |
                    Transaction.linked_to.like("CONTRACTOR-PAY:%")
                ),
                Project.company_id == current_user.company_id,
            )
            .order_by(Transaction.created_at.desc())
        )
        user_stmt = user_stmt.where(User.company_id == current_user.company_id)
        labour_stmt = labour_stmt.where(Labour.company_id == current_user.company_id)

    txns = (await db.execute(stmt)).scalars().all()
    users = {u.id: u for u in (await db.execute(user_stmt)).scalars().all()}
    labours = {l.id: l for l in (await db.execute(labour_stmt)).scalars().all()}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Name", "Payroll Type", "Period", "Gross", "Deduction", "Net Amount", "Status", "Payment Date"])

    for t in txns:
        name = "Unknown"
        p_type = "Unknown"
        period = ""
        gross = 0.0
        deduct = 0.0
        net = float(t.amount)
        status = "PAID"
        p_date = t.created_at.date().isoformat() if t.created_at else ""

        parts = (t.linked_to or "").split(":")
        if t.linked_to.startswith("STAFF-SALARY:") and len(parts) >= 3:
            p_type = "Staff Salary"
            u = users.get(int(parts[1]))
            name = u.full_name if u else "Unknown Staff"
            period = parts[2]
        elif t.linked_to.startswith("LABOUR-WAGE:") and len(parts) >= 3:
            p_type = "Labour Wage"
            l = labours.get(int(parts[1]))
            name = l.labour_name if l else "Unknown Labour"
            period = parts[2]
        elif t.linked_to.startswith("CONTRACTOR-PAY:") and len(parts) >= 2:
            p_type = "Contractor Payment"
            name = "Contractor"
            period = "N/A"

        if t.reference:
            for ref_part in t.reference.split("|"):
                if ref_part.startswith("gross:"):
                    gross = float(ref_part.split(":")[1])
                if ref_part.startswith("deduct:"):
                    deduct = float(ref_part.split(":")[1])

        writer.writerow([name, p_type, period, gross, deduct, net, status, p_date])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payroll_register.csv"},
    )


# ================= PAYROLL REGISTER =================

@router.get("/register", deprecated=True)
async def get_payroll_register(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_permission("payroll.view")),
):
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(
            status_code=403,
            detail="User does not belong to any company",
        )

    stmt = select(Transaction).where(
        Transaction.linked_to.like("STAFF-SALARY:%") |
        Transaction.linked_to.like("LABOUR-WAGE:%") |
        Transaction.linked_to.like("CONTRACTOR-PAY:%")
    ).order_by(Transaction.created_at.desc())

    if not is_sa:
        stmt = (
            select(Transaction)
            .join(Project, Transaction.project_id == Project.id)
            .where(
                (
                    Transaction.linked_to.like("STAFF-SALARY:%") |
                    Transaction.linked_to.like("LABOUR-WAGE:%") |
                    Transaction.linked_to.like("CONTRACTOR-PAY:%")
                ),
                Project.company_id == current_user.company_id,
            )
            .order_by(Transaction.created_at.desc())
        )

    return (await db.execute(stmt)).scalars().all()







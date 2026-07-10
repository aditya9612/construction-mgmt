from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from decimal import Decimal
from datetime import datetime, date
import io, csv

from app.db.session import get_db_session
from app.core.dependencies import get_current_active_user
from app.models.user import UserRole
from app.models.user import User
from app.models.invoice import Transaction
from app.models.accountant import JournalEntry, JournalLine, Account, BankAccount
from app.models.billing import RABill
from app.models.invoice import Invoice
from app.models.labour import Labour, LabourAttendance, LabourPayroll
from app.schemas.payroll import (
    StaffSalaryProcessRequest,
    StaffSalaryRegisterOut,
    LabourWageGenerateRequest,
    ContractorPayRequest
)
from app.utils.accounting import get_payroll_account, get_primary_cash_account

router = APIRouter(prefix="/accountant/payroll", tags=["Accountant Payroll"])

def get_allowed_staff_roles():
    return [
        UserRole.ADMIN.value,
        UserRole.PROJECT_MANAGER.value,
        UserRole.SITE_ENGINEER.value,
        UserRole.ACCOUNTANT.value
    ]

@router.get("/summary")
async def payroll_summary(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    from sqlalchemy import func
    from app.models.labour import PayrollStatus
    
    pending_payroll_query = await db.scalar(
        select(func.sum(LabourPayroll.total_wage - LabourPayroll.paid_amount))
        .where(LabourPayroll.status == PayrollStatus.PENDING)
    )
    pending_payroll = float(pending_payroll_query or 0.0)

    paid_payroll_query = await db.scalar(
        select(func.sum(LabourPayroll.paid_amount))
        .where(LabourPayroll.status == PayrollStatus.PAID)
    )
    paid_payroll = float(paid_payroll_query or 0.0)

    advance_given_query = await db.scalar(
        select(func.sum(LabourPayroll.advance_adjusted))
    )
    advance_given = float(advance_given_query or 0.0)
    
    contractor_payment = 0.0 # Placeholder logic for Contractor payment
    
    return {
        "pending_payroll": pending_payroll,
        "paid_payroll": paid_payroll,
        "advance_given": advance_given,
        "contractor_payment": contractor_payment
    }

@router.get("/payslip/export")
async def export_payslips(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(LabourPayroll).options(selectinload(LabourPayroll.labour)).order_by(LabourPayroll.created_at.desc())
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
            p.status.value if hasattr(p.status, 'value') else str(p.status),
            p.updated_at.strftime("%Y-%m-%d") if p.updated_at else ""
        ])
        
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=payslips_export.csv"}
    )

# ================= STAFF SALARY =================

@router.get("/staff/register")
async def get_staff_register(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    stmt = select(User).where(User.role.in_(get_allowed_staff_roles()))
    result = await db.execute(stmt)
    users = result.scalars().all()
    
    return [{"user_id": u.id, "full_name": u.full_name, "role": u.role, "designation": u.designation} for u in users]

@router.post("/staff/process")
async def process_staff_salary(
    payload: StaffSalaryProcessRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    # 1. Verify User
    staff = await db.get(User, payload.user_id)
    if not staff or staff.role not in get_allowed_staff_roles():
        raise HTTPException(status_code=400, detail="Invalid staff user")
        
    # 2. Duplicate Protection
    linked_id = f"STAFF-SALARY:{payload.user_id}:{payload.month_year}"
    existing = await db.scalar(select(Transaction).where(Transaction.linked_to == linked_id))
    if existing:
        raise HTTPException(status_code=400, detail="Salary already processed for this month")
        
    # 3. Get Account
    try:
        staff_acc = await get_payroll_account(db, "staff_salary_account_id")
    except ValueError:
        raise HTTPException(status_code=400, detail="Payroll account mapping not configured")
    
    if payload.payment_mode == "cash":
        try:
            pay_acc = await get_primary_cash_account(db)
        except ValueError:
            raise HTTPException(status_code=400, detail="Primary cash account not configured")
    else:
        if not payload.bank_account_id:
            raise HTTPException(status_code=400, detail="Bank account required for bank payment")
        bank = await db.get(BankAccount, payload.bank_account_id)
        if not bank:
            raise HTTPException(status_code=400, detail="Bank account not found")
        pay_acc = await db.get(Account, bank.account_id) # Need Account model import

    # 4. Create Transaction
    serialized_ref = f"gross:{payload.gross_salary}|deduct:{payload.deductions}"
    txn = Transaction(
        project_id=payload.project_id,
        type="payment",
        amount=payload.net_salary,
        mode=payload.payment_mode,
        reference=serialized_ref,
        linked_to=linked_id,
        created_by=current_user.id
    )
    db.add(txn)
    
    # 5. Create Journal
    je = JournalEntry(description=f"Staff Salary Payment for {payload.month_year}")
    db.add(je)
    await db.flush()
    
    db.add(JournalLine(entry_id=je.id, account_id=staff_acc.id, debit=payload.net_salary, credit=0))
    db.add(JournalLine(entry_id=je.id, account_id=pay_acc.id, debit=0, credit=payload.net_salary))
    
    await db.commit()
    return {"message": "Staff salary processed successfully"}

@router.get("/staff/history")
async def get_staff_history(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(Transaction).where(Transaction.linked_to.like("STAFF-SALARY:%"))
    result = await db.execute(stmt)
    return result.scalars().all()

# ================= LABOUR PAYROLL =================

@router.get("/labour/wages")
async def get_labour_wages(
    start_date: date,
    end_date: date,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    # Calculates daily/weekly/monthly wages dynamically
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(Labour)
    labours = (await db.execute(stmt)).scalars().all()
    
    results = []
    for labour in labours:
        att_stmt = select(LabourAttendance).where(
            LabourAttendance.labour_id == labour.id,
            LabourAttendance.attendance_date >= start_date,
            LabourAttendance.attendance_date <= end_date
        )
        attendances = (await db.execute(att_stmt)).scalars().all()
        
        # very simplified logic for demonstration of aggregation
        total_hours = sum(a.working_hours or 0 for a in attendances)
        # Assuming effective wage is stored. Here we just mock it using total hours.
        wage = total_hours * 50 # Example logic
        
        results.append({
            "labour_id": labour.id,
            "full_name": labour.labour_name,
            "total_hours": total_hours,
            "calculated_wage": wage
        })
    return results

@router.post("/labour/pay")
async def pay_labour_wages(
    payload: LabourWageGenerateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    labour = await db.get(Labour, payload.labour_id)
    if not labour:
        raise HTTPException(status_code=404, detail="Labour not found")
        
    try:
        wages_acc = await get_payroll_account(db, "wages_account_id")
    except ValueError:
        raise HTTPException(status_code=400, detail="Payroll account mapping not configured")
        
    if payload.payment_mode == "cash":
        try:
            pay_acc = await get_primary_cash_account(db)
        except ValueError:
            raise HTTPException(status_code=400, detail="Primary cash account not configured")
    else:
        if not payload.bank_account_id:
            raise HTTPException(status_code=400, detail="Bank account required for bank payment")
        bank = await db.get(BankAccount, payload.bank_account_id)
        if not bank:
            raise HTTPException(status_code=404, detail="Bank account not found")
        pay_acc = await db.get(Account, bank.account_id)

    amount = 500.00 # Placeholder for calculated wage

    txn = Transaction(
        project_id=payload.project_id,
        type="payment",
        amount=amount,
        mode=payload.payment_mode,
        reference=f"wages:{payload.start_date}_to_{payload.end_date}",
        linked_to=f"LABOUR-WAGE:{payload.labour_id}:{payload.start_date}",
        created_by=current_user.id
    )
    db.add(txn)
    
    je = JournalEntry(
        description=f"Labour Wages {payload.start_date}",
        entry_date=payload.start_date,
        created_by=current_user.id
    )
    db.add(je)
    await db.flush()
    
    db.add(JournalLine(entry_id=je.id, account_id=wages_acc.id, debit=amount, credit=0))
    db.add(JournalLine(entry_id=je.id, account_id=pay_acc.id, debit=0, credit=amount))
    
    await db.commit()
    return {"message": "Labour wages processed successfully"}

# ================= CONTRACTOR PAYROLL =================

@router.get("/contractor/bills")
async def get_contractor_bills(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(RABill).where(RABill.status != "Paid")
    return (await db.execute(stmt)).scalars().all()

@router.get("/staff/export")
async def export_staff_payroll(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(Transaction).where(Transaction.linked_to.like("STAFF-SALARY:%"))
    txns = (await db.execute(stmt)).scalars().all()
    
    users = (await db.execute(select(User))).scalars().all()
    user_map = {u.id: u for u in users}
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Staff Name", "Role", "Department", "Designation", "Month", "Gross Salary", "Deductions", "Net Salary", "Payment Status", "Payment Date"])
    
    for t in txns:
        parts = t.linked_to.split(':')
        if len(parts) >= 3:
            uid = int(parts[1])
            month = parts[2]
            u = user_map.get(uid)
            
            gross = 0.0
            deduct = 0.0
            if t.reference:
                for ref_part in t.reference.split('|'):
                    if ref_part.startswith('gross:'): gross = float(ref_part.split(':')[1])
                    if ref_part.startswith('deduct:'): deduct = float(ref_part.split(':')[1])
                    
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
                t.created_at.date().isoformat() if t.created_at else ""
            ])
            
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=staff_salary.csv"})

@router.get("/contractor/export")
async def export_contractor_payroll(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(RABill)
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
            b.bill_date.isoformat() if b.bill_date else ""
        ])
        
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=contractor_payments.csv"})

@router.get("/register/export")
async def export_payroll_register(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(Transaction).where(
        Transaction.linked_to.like("STAFF-SALARY:%") |
        Transaction.linked_to.like("LABOUR-WAGE:%") |
        Transaction.linked_to.like("CONTRACTOR-PAY:%")
    ).order_by(Transaction.created_at.desc())
    
    txns = (await db.execute(stmt)).scalars().all()
    
    users = {u.id: u for u in (await db.execute(select(User))).scalars().all()}
    labours = {l.id: l for l in (await db.execute(select(Labour))).scalars().all()}
    
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
        
        parts = (t.linked_to or "").split(':')
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
            for ref_part in t.reference.split('|'):
                if ref_part.startswith('gross:'): gross = float(ref_part.split(':')[1])
                if ref_part.startswith('deduct:'): deduct = float(ref_part.split(':')[1])
                
        writer.writerow([name, p_type, period, gross, deduct, net, status, p_date])
        
    return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=payroll_register.csv"})
@router.post("/contractor/pay")
async def pay_contractor_bill(
    payload: ContractorPayRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    rabill = await db.get(RABill, payload.rabill_id)
    if not rabill:
        raise HTTPException(status_code=404, detail="RA Bill not found")
        
    invoice_stmt = select(Invoice).where(Invoice.reference_id == rabill.id, Invoice.source_type == "contractor")
    invoice = await db.scalar(invoice_stmt)
    
    try:
        contractor_acc = await get_payroll_account(db, "contractor_expense_account_id")
    except ValueError:
        raise HTTPException(status_code=400, detail="Payroll account mapping not configured")
        
    if payload.payment_mode == "cash":
        try:
            pay_acc = await get_primary_cash_account(db)
        except ValueError:
            raise HTTPException(status_code=400, detail="Primary cash account not configured")
    else:
        bank = await db.get(BankAccount, payload.bank_account_id)
        pay_acc = await db.get(Account, bank.account_id)
        
    # Update deductions on RABill without modifying gross amount
    rabill.deductions = payload.total_deductions
    rabill.status = "Paid"
    
    if invoice:
        invoice.paid_amount = (invoice.paid_amount or 0) + payload.paid_amount
        invoice.pending_amount = max(0, invoice.total_amount - invoice.paid_amount)
        if invoice.pending_amount <= 0:
            invoice.status = "Paid"

    txn = Transaction(
        project_id=rabill.project_id,
        invoice_id=invoice.id if invoice else None,
        type="payment",
        amount=payload.paid_amount,
        mode=payload.payment_mode,
        reference=f"rabill:{rabill.bill_number}",
        linked_to=f"CONTRACTOR-PAY:{payload.rabill_id}",
        created_by=current_user.id
    )
    db.add(txn)
    
    je = JournalEntry(
        description=f"Contractor Payment RA Bill {rabill.bill_number}",
        entry_date=date.today(),
        created_by=current_user.id
    )
    db.add(je)
    await db.flush()
    
    db.add(JournalLine(entry_id=je.id, account_id=contractor_acc.id, debit=payload.paid_amount, credit=0))
    db.add(JournalLine(entry_id=je.id, account_id=pay_acc.id, debit=0, credit=payload.paid_amount))
    
    await db.commit()
    return {"message": "Contractor payment successful"}

# ================= PAYROLL REGISTER =================

@router.get("/register")
async def get_payroll_register(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user)
):
    if current_user.role not in [UserRole.ADMIN.value, UserRole.ACCOUNTANT.value]:
        raise HTTPException(status_code=403, detail="Not authorized")
        
    stmt = select(Transaction).where(
        Transaction.linked_to.like("STAFF-SALARY:%") |
        Transaction.linked_to.like("LABOUR-WAGE:%") |
        Transaction.linked_to.like("CONTRACTOR-PAY:%")
    ).order_by(Transaction.created_at.desc())
    
    return (await db.execute(stmt)).scalars().all()






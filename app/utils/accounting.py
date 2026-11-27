from decimal import Decimal
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.accountant import Account, JournalEntry, JournalLine

async def auto_post_journal(
    db: AsyncSession,
    amount: float,
    debit_code: str,
    credit_code: str,
    description: str
) -> Optional[JournalEntry]:
    """
    Automatically creates a balanced journal entry if both accounts are found.
    """
    if amount <= 0:
        return None

    # Fetch accounts by code
    debit_acc = await db.scalar(select(Account).where(Account.code == debit_code))
    credit_acc = await db.scalar(select(Account).where(Account.code == credit_code))

    if not debit_acc or not credit_acc:
        # Cannot post if accounts don't exist
        return None

    # Create Journal Entry
    je = JournalEntry(description=description)
    db.add(je)
    await db.flush()

    # Create Lines
    dr_line = JournalLine(
        entry_id=je.id,
        account_id=debit_acc.id,
        debit=Decimal(str(amount)),
        credit=Decimal("0.0")
    )
    
    cr_line = JournalLine(
        entry_id=je.id,
        account_id=credit_acc.id,
        debit=Decimal("0.0"),
        credit=Decimal(str(amount))
    )
    
    db.add_all([dr_line, cr_line])
    await db.flush()
    
    return je

async def get_primary_cash_account(
    db: AsyncSession, company_id: Optional[int] = None
) -> Account:
    """
    Resolves the primary cash account for the system.
    1. Checks CompanySettings.primary_cash_account_id (filtered by company_id if provided)
    2. Falls back to Account with code 'CASH'
    3. Raises ValueError if missing
    """
    from app.models.settings import CompanySettings

    stmt = select(CompanySettings)
    if company_id is not None:
        stmt = stmt.where(CompanySettings.company_id == company_id)

    settings = await db.scalar(stmt)

    if settings and settings.primary_cash_account_id:
        cash_acc = await db.get(Account, settings.primary_cash_account_id)
        if cash_acc and (company_id is None or cash_acc.company_id == company_id):
            return cash_acc

    # Fallback to standard Cash account by code (strictly scoped when company_id is provided)
    cash_by_code_stmt = select(Account).where(Account.code == "CASH")
    if company_id is not None:
        cash_by_code_stmt = cash_by_code_stmt.where(Account.company_id == company_id)
    cash_acc = await db.scalar(cash_by_code_stmt)
    if cash_acc:
        return cash_acc

    raise ValueError("Primary Cash Account is not configured.")


async def get_petty_cash_account(db: AsyncSession) -> Account:
    """
    Resolves the petty cash account for the system.
    1. Checks CompanySettings.petty_cash_account_id
    2. Raises ValueError if missing
    """
    from app.models.settings import CompanySettings
    
    settings = await db.scalar(select(CompanySettings))
    
    if settings and settings.petty_cash_account_id:
        cash_acc = await db.get(Account, settings.petty_cash_account_id)
        if cash_acc:
            return cash_acc

    raise ValueError("Petty cash account not configured.")

async def get_payroll_account(db: AsyncSession, account_field_name: str) -> Account:
    """
    Resolves a payroll account from CompanySettings.
    Raises ValueError if missing.
    """
    from app.models.settings import CompanySettings
    
    settings = await db.scalar(select(CompanySettings))
    if not settings:
        raise ValueError("CompanySettings not configured.")
        
    account_id = getattr(settings, account_field_name, None)
    if not account_id:
        raise ValueError(f"Payroll account '{account_field_name}' not configured in CompanySettings.")
        
    account = await db.get(Account, account_id)
    if not account:
        raise ValueError(f"Account ID {account_id} for '{account_field_name}' not found.")
        
    return account

from fastapi import HTTPException


async def _get_or_create_company_account(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    account_type,
    company_id: Optional[int],
) -> Account:
    """
    Resolves or creates a company-owned ledger account.
    Fails closed when company_id is None to prevent cross-tenant posting.
    """
    if company_id is None:
        raise ValueError(
            f"company_id is required to resolve or create '{code}' account."
        )

    acc = await db.scalar(
        select(Account).where(
            Account.code == code,
            Account.company_id == company_id,
        )
    )
    if not acc:
        acc = Account(
            name=name,
            code=code,
            type=account_type,
            company_id=company_id,
        )
        db.add(acc)
        await db.flush()
    return acc


async def resolve_tax_accounts(
    db: AsyncSession,
    account_type_name: str,
    company_id: Optional[int] = None,
) -> Account:
    """
    Dynamically resolves a company-scoped tax account (Input GST, Output GST, TDS Payable)
    using CompanySettings or standard company account code.
    If missing for that company, automatically creates / seeds it under that company_id.
    Fails closed when company_id is None.
    """
    from app.models.settings import CompanySettings
    from app.core.enums import AccountType

    tax_specs = {
        "tds_payable": ("TDS Payable", "TDS_PAYABLE", AccountType.LIABILITY),
        "input_gst": ("Input GST", "INPUT_GST", AccountType.ASSET),
        "output_gst": ("Output GST", "OUTPUT_GST", AccountType.LIABILITY),
    }
    if account_type_name not in tax_specs:
        raise HTTPException(
            status_code=400, detail=f"Unknown tax account type request: {account_type_name}"
        )

    if company_id is None:
        raise ValueError(
            f"company_id is required to resolve or create '{account_type_name}' account."
        )

    if account_type_name == "tds_payable":
        settings = await db.scalar(
            select(CompanySettings).where(CompanySettings.company_id == company_id)
        )
        if settings and getattr(settings, "tds_payable_account_id", None):
            acc = await db.get(Account, settings.tds_payable_account_id)
            if acc and acc.company_id == company_id:
                return acc

    acc_name, acc_code, acc_type = tax_specs[account_type_name]
    return await _get_or_create_company_account(
        db,
        code=acc_code,
        name=acc_name,
        account_type=acc_type,
        company_id=company_id,
    )


async def get_accounts_receivable(
    db: AsyncSession,
    company_id: Optional[int] = None,
) -> Account:
    from app.core.enums import AccountType

    return await _get_or_create_company_account(
        db,
        code="ACCOUNTS_RECEIVABLE",
        name="Accounts Receivable",
        account_type=AccountType.ASSET,
        company_id=company_id,
    )


async def get_revenue_account(
    db: AsyncSession,
    company_id: Optional[int] = None,
) -> Account:
    from app.core.enums import AccountType

    return await _get_or_create_company_account(
        db,
        code="SALES_REVENUE",
        name="Sales Revenue",
        account_type=AccountType.INCOME,
        company_id=company_id,
    )


STANDARD_SYSTEM_ACCOUNTS = [
    # Assets
    {"name": "Main Bank Account", "code": "BANK", "type": "ASSET"},
    {"name": "Primary Cash", "code": "CASH", "type": "ASSET"},
    {"name": "Petty Cash", "code": "PETTY_CASH", "type": "ASSET"},
    {"name": "Input GST", "code": "INPUT_GST", "type": "ASSET"},
    {"name": "Accounts Receivable", "code": "ACCOUNTS_RECEIVABLE", "type": "ASSET"},
    {"name": "Cash / Bank (1001)", "code": "1001", "type": "ASSET"},
    {"name": "Accounts Receivable (1200)", "code": "1200", "type": "ASSET"},

    # Liabilities
    {"name": "Vendor Payable", "code": "VENDOR_PAYABLE", "type": "LIABILITY"},
    {"name": "Contractor Payable", "code": "CONTRACTOR_PAYABLE", "type": "LIABILITY"},
    {"name": "Wages Payable", "code": "WAGES_PAYABLE", "type": "LIABILITY"},
    {"name": "Output GST", "code": "OUTPUT_GST", "type": "LIABILITY"},
    {"name": "TDS Payable", "code": "TDS_PAYABLE", "type": "LIABILITY"},
    {"name": "Retention Payable", "code": "RETENTION_PAYABLE", "type": "LIABILITY"},

    # Income
    {"name": "Sales Revenue", "code": "SALES_REVENUE", "type": "INCOME"},

    # Expenses
    {"name": "General Expense", "code": "GENERAL_EXPENSE", "type": "EXPENSE"},
    {"name": "Operating Expense", "code": "EXPENSE", "type": "EXPENSE"},
    {"name": "Labour Expense", "code": "LABOUR_EXPENSE", "type": "EXPENSE"},
    {"name": "Wages Expense", "code": "WAGES_EXPENSE", "type": "EXPENSE"},
    {"name": "Staff Salary Expense", "code": "SALARY_EXPENSE", "type": "EXPENSE"},
    {"name": "Contractor Expense", "code": "CONTRACTOR_EXPENSE", "type": "EXPENSE"},
]


async def seed_company_chart_of_accounts(db: AsyncSession, company_id: int) -> dict:
    """
    Seeds the standard Chart of Accounts for a company idempotently,
    and links the default accounts to CompanySettings.
    """
    from app.core.enums import AccountType
    from app.models.settings import CompanySettings

    # 1. Fetch existing accounts for this company
    existing = await db.scalars(
        select(Account).where(Account.company_id == company_id)
    )
    acc_map = {a.code: a for a in existing.all()}

    # 2. Insert missing standard accounts
    for item in STANDARD_SYSTEM_ACCOUNTS:
        if item["code"] not in acc_map:
            acc_type = AccountType[item["type"]]
            new_acc = Account(
                company_id=company_id,
                name=item["name"],
                code=item["code"],
                type=acc_type,
            )
            db.add(new_acc)
            await db.flush()
            acc_map[item["code"]] = new_acc

    # 3. Connect default account IDs into CompanySettings
    settings = await db.scalar(
        select(CompanySettings).where(CompanySettings.company_id == company_id)
    )
    if settings:
        if not settings.primary_cash_account_id and "CASH" in acc_map:
            settings.primary_cash_account_id = acc_map["CASH"].id
        if not settings.petty_cash_account_id and "PETTY_CASH" in acc_map:
            settings.petty_cash_account_id = acc_map["PETTY_CASH"].id
        if not settings.wages_account_id and "WAGES_EXPENSE" in acc_map:
            settings.wages_account_id = acc_map["WAGES_EXPENSE"].id
        if not settings.staff_salary_account_id and "SALARY_EXPENSE" in acc_map:
            settings.staff_salary_account_id = acc_map["SALARY_EXPENSE"].id
        if not settings.contractor_expense_account_id and "CONTRACTOR_EXPENSE" in acc_map:
            settings.contractor_expense_account_id = acc_map["CONTRACTOR_EXPENSE"].id
        if not settings.tds_payable_account_id and "TDS_PAYABLE" in acc_map:
            settings.tds_payable_account_id = acc_map["TDS_PAYABLE"].id
        if not settings.retention_payable_account_id and "RETENTION_PAYABLE" in acc_map:
            settings.retention_payable_account_id = acc_map["RETENTION_PAYABLE"].id
        await db.flush()

    return acc_map


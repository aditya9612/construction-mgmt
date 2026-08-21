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

async def get_primary_cash_account(db: AsyncSession) -> Account:
    """
    Resolves the primary cash account for the system.
    1. Checks CompanySettings.primary_cash_account_id
    2. Raises ValueError if missing
    """
    from app.models.settings import CompanySettings
    
    settings = await db.scalar(select(CompanySettings))
    
    if settings and settings.primary_cash_account_id:
        cash_acc = await db.get(Account, settings.primary_cash_account_id)
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

async def resolve_tax_accounts(db: AsyncSession, account_type_name: str) -> Account:
    """
    Dynamically resolves a tax account (e.g., Input GST, Output GST, TDS Payable)
    using pattern matching or CompanySettings if available to avoid hardcoding exact names.
    account_type_name could be 'input_gst', 'output_gst', or 'tds_payable'.
    """
    from app.models.settings import CompanySettings
    from app.core.enums import AccountType
    
    settings = await db.scalar(select(CompanySettings))
    
    if account_type_name == 'tds_payable':
        if settings and getattr(settings, 'tds_payable_account_id', None):
            acc = await db.get(Account, settings.tds_payable_account_id)
            if acc: return acc
        raise ValueError("TDS Payable account not configured.")
        
    elif account_type_name == 'input_gst':
        acc = await db.scalar(select(Account).where(Account.code == 'INPUT_GST'))
        if acc: return acc
        raise ValueError("Input GST account not configured.")
        
    elif account_type_name == 'output_gst':
        acc = await db.scalar(select(Account).where(Account.code == 'OUTPUT_GST'))
        if acc: return acc
        raise ValueError("Output GST account not configured.")

    raise ValueError(f"Unknown tax account type request: {account_type_name}")


async def get_accounts_receivable(db: AsyncSession) -> Account:
    from app.core.enums import AccountType
    acc = await db.scalar(select(Account).where(Account.code == 'ACCOUNTS_RECEIVABLE'))
    if not acc:
        raise ValueError("Accounts Receivable account not configured.")
    return acc

async def get_revenue_account(db: AsyncSession) -> Account:
    from app.core.enums import AccountType
    acc = await db.scalar(select(Account).where(Account.code == 'SALES_REVENUE'))
    if not acc:
        raise ValueError("Revenue account not configured.")
    return acc

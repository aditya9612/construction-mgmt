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

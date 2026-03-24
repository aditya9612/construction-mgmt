from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError


async def generate_business_id(db, model, column_name: str, prefix: str):
    """
    Safe business ID generator with DB-level consistency.
    Example: PRJ-0001, EXP-0001
    """

    # 🔥 Get max existing number directly from DB
    result = await db.execute(
        select(func.max(getattr(model, column_name)))
    )
    last_id = result.scalar_one_or_none()

    if last_id:
        try:
            last_number = int(last_id.split("-")[-1])
        except Exception:
            last_number = 0
    else:
        last_number = 0

    # 🔥 Try next IDs safely
    for _ in range(5):
        new_number = last_number + 1
        new_id = f"{prefix}-{str(new_number).zfill(4)}"

        # Check uniqueness
        exists = await db.execute(
            select(model).where(getattr(model, column_name) == new_id)
        )

        if not exists.scalar_one_or_none():
            return new_id

        last_number += 1  # retry next number

    raise Exception("Failed to generate unique business ID after retries")
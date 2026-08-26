import asyncio
import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.core.db import AsyncSessionLocal as async_session_maker
import app.main  # Ensures all models are loaded via FastAPI router definitions
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from sqlalchemy import select


async def bootstrap_super_admin():
    print("=== Super Admin Bootstrap ===")
    email = input("Enter Super Admin email: ").strip()
    full_name = input("Enter full name: ").strip()
    mobile = input("Enter mobile number (optional): ").strip() or "0000000000"
    
    import getpass
    password = getpass.getpass("Enter secure password: ")
    confirm_password = getpass.getpass("Confirm password: ")
    
    if password != confirm_password:
        print("Passwords do not match. Aborting.")
        return

    async with async_session_maker() as db:
        # Check if email already exists
        existing = await db.scalar(select(User).where(User.email == email))
        if existing:
            print(f"Error: User with email '{email}' already exists.")
            return
            
        # Create Super Admin
        user = User(
            email=email,
            full_name=full_name,
            mobile=mobile,
            hashed_password=get_password_hash(password),
            role=UserRole.ADMIN.value,  # They technically have Admin role but is_super_admin defines their actual global power
            is_active=True,
            is_super_admin=True,
            company_id=None, # Global access, no tenant restriction
        )
        db.add(user)
        await db.commit()
        print(f"Successfully created Super Admin: {email}")


if __name__ == "__main__":
    asyncio.run(bootstrap_super_admin())

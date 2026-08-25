import pytest
from sqlalchemy import text
from app.db.session import get_db_session
import asyncio

@pytest.mark.asyncio
async def test_legacy_user_insert():
    # Simulate old application insert without is_super_admin or company_id
    async for db in get_db_session():
        try:
            # We use an email that is unique to avoid conflicts
            email = "legacy_test_user@example.com"
            
            # Clean up first if it exists from a previous run
            await db.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
            await db.commit()

            # The old app inserts with just basic fields
            insert_sql = text("""
                INSERT INTO users (email, hashed_password, full_name, mobile, role, is_active, is_deleted, created_at, updated_at)
                VALUES (:email, 'hash', 'Legacy User', '1234567890', 'Client', 1, 0, NOW(), NOW())
            """)
            
            await db.execute(insert_sql, {"email": email})
            await db.commit()

            # Now fetch it
            result = await db.execute(text("SELECT is_super_admin, company_id FROM users WHERE email = :email"), {"email": email})
            row = result.fetchone()

            assert row is not None
            # It should have defaulted to 0 (False)
            assert row[0] == 0 
            # It should have defaulted to 1 (Default Company)
            assert row[1] == 1 

        finally:
            # Cleanup
            await db.execute(text("DELETE FROM users WHERE email = :email"), {"email": email})
            await db.commit()

@pytest.mark.asyncio
async def test_legacy_project_insert():
    # Simulate old application insert without company_id
    async for db in get_db_session():
        try:
            # Create a mock owner first for FK constraint
            await db.execute(text("INSERT IGNORE INTO owners (id, owner_code, owner_name, email, mobile, created_at, updated_at) VALUES (999, 'OW-999', 'Test Owner', 'owner@example.com', '1234567890', NOW(), NOW())"))
            await db.commit()
            
            project_name = "Legacy Test Project"
            await db.execute(text("DELETE FROM projects WHERE project_name = :name"), {"name": project_name})
            await db.commit()

            insert_sql = text("""
                INSERT INTO projects (project_name, business_id, status, owner_id, grace_period_minutes, created_at, updated_at)
                VALUES (:name, 'TEST-PRJ-999', 'PLANNED', 999, 15, NOW(), NOW())
            """)
            
            await db.execute(insert_sql, {"name": project_name})
            await db.commit()

            result = await db.execute(text("SELECT company_id FROM projects WHERE project_name = :name"), {"name": project_name})
            row = result.fetchone()

            assert row is not None
            # It should have defaulted to 1 (Default Company)
            assert row[0] == 1 

        finally:
            await db.execute(text("DELETE FROM projects WHERE project_name = :name"), {"name": project_name})
            await db.commit()

"""backfill_default_company

Revision ID: 016d34b4816c
Revises: a53fa727c188
Create Date: 2026-08-25 20:59:28.636629
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '016d34b4816c'
down_revision = 'a53fa727c188'
branch_labels = None
depends_on = None


def upgrade():
    # Fetch existing company settings to see if there's a name we can use
    conn = op.get_bind()
    
    # 1. Create Default Company
    import datetime
    now = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    
    # Check if a company already exists
    res = conn.execute(sa.text("SELECT id FROM companies LIMIT 1")).fetchone()
    if not res:
        conn.execute(
            sa.text(
                f"INSERT INTO companies (name, is_active, created_at, updated_at) "
                f"VALUES ('Default Company', 1, '{now}', '{now}')"
            )
        )
    
    company_res = conn.execute(sa.text("SELECT id FROM companies ORDER BY id ASC LIMIT 1")).fetchone()
    if company_res:
        default_company_id = company_res[0]
        
        # 2. Backfill Users (set company_id)
        conn.execute(sa.text(f"UPDATE users SET company_id = {default_company_id} WHERE company_id IS NULL AND is_super_admin = 0"))
        
        # 3. Backfill Projects
        conn.execute(sa.text(f"UPDATE projects SET company_id = {default_company_id} WHERE company_id IS NULL"))
        
        # 4. Backfill CompanySettings
        conn.execute(sa.text(f"UPDATE company_settings SET company_id = {default_company_id} WHERE company_id IS NULL"))
        
    # 5. Enforce NOT NULL for users (optional, but requested only where safe - Super Admin might have NULL, so DO NOT enforce NOT NULL on users.company_id)
    # 6. Enforce NOT NULL for projects (skipped due to MySQL FK constraints; leaving nullable is safer for now)
    pass


def downgrade():
    pass


"""add rbac and mobile otp

Revision ID: add_rbac_mobile
Revises: 22ba70897509
Create Date: 2026-03-20

Adds mobile column for OTP login, makes email/hashed_password nullable for OTP-only users,
and updates user_role enum to exact RBAC values: Admin, ProjectManager, SiteEngineer, Contractor, Accountant.
"""

from alembic import op
import sqlalchemy as sa


revision = "add_rbac_mobile"
down_revision = "22ba70897509"
branch_labels = None
depends_on = None

# Mapping from old enum values (Python names or old values) to new exact RBAC values
OLD_TO_NEW_ROLE = {
    "ADMIN": "Admin",
    "Admin": "Admin",
    "PROJECT_MANAGER": "ProjectManager",
    "Project Manager": "ProjectManager",
    "ProjectManager": "ProjectManager",
    "SITE_ENGINEER": "SiteEngineer",
    "Site Engineer": "SiteEngineer",
    "SiteEngineer": "SiteEngineer",
    "CONTRACTOR": "Contractor",
    "Contractor": "Contractor",
    "ACCOUNTANT": "Accountant",
    "Accountant": "Accountant",
}

NEW_ROLES = ("Admin", "ProjectManager", "SiteEngineer", "Contractor", "Accountant")


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = [c["name"] for c in insp.get_columns("users")]

    # Add mobile column (nullable, unique) if not exists
    if "mobile" not in cols:
        op.add_column("users", sa.Column("mobile", sa.String(20), nullable=True))
        op.create_index(op.f("ix_users_mobile"), "users", ["mobile"], unique=True)

    # Make email and hashed_password nullable (for OTP-only users)
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(255),
        nullable=True,
    )
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(255),
        nullable=True,
    )

    # Update user_role enum to exact RBAC values.
    # MySQL: convert to VARCHAR, update values, convert to new ENUM (avoids case-insensitive duplicate issue).
    if conn.dialect.name == "mysql":
        # Step 1: Convert role column to VARCHAR temporarily
        op.execute("ALTER TABLE users MODIFY COLUMN role VARCHAR(50) NOT NULL DEFAULT 'SiteEngineer'")

        # Step 2: Update each row from old value to new value
        for old_val, new_val in OLD_TO_NEW_ROLE.items():
            if old_val != new_val:
                conn.execute(
                    sa.text("UPDATE users SET role = :new WHERE role = :old"),
                    {"new": new_val, "old": old_val},
                )

        # Step 3: Convert back to new ENUM
        enum_str = ",".join(repr(r) for r in NEW_ROLES)
        op.execute(
            f"ALTER TABLE users MODIFY COLUMN role ENUM({enum_str}) NOT NULL DEFAULT 'SiteEngineer'"
        )
    else:
        result = conn.execute(sa.text("SELECT id, role FROM users"))
        for row in result:
            uid, old_role = row
            new_role = OLD_TO_NEW_ROLE.get(old_role, old_role)
            if new_role != old_role:
                conn.execute(sa.text("UPDATE users SET role = :r WHERE id = :i"), {"r": new_role, "i": uid})


def downgrade():
    op.alter_column("users", "email", nullable=False)
    op.alter_column("users", "hashed_password", nullable=False)
    op.drop_index(op.f("ix_users_mobile"), table_name="users")
    op.drop_column("users", "mobile")
    # Reverting enum to original is complex; leaving as-is for simplicity.

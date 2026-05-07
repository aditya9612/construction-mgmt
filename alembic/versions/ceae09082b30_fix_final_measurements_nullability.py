"""chat system + fixes

Revision ID: ceae09082b30
Revises: 267efff250ed
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "ceae09082b30"
down_revision = "267efff250ed"
branch_labels = None
depends_on = None


def upgrade():
    # ================= FINAL MEASUREMENTS FIX =================
    op.execute("UPDATE final_measurements SET extra_area = 0 WHERE extra_area IS NULL")
    op.execute("UPDATE final_measurements SET extra_rate = 0 WHERE extra_rate IS NULL")

    op.alter_column(
        "final_measurements",
        "extra_area",
        existing_type=sa.DECIMAL(18, 2),
        nullable=False,
        server_default="0",
    )

    op.alter_column(
        "final_measurements",
        "extra_rate",
        existing_type=sa.DECIMAL(18, 2),
        nullable=False,
        server_default="0",
    )

    # ================= CHAT SESSION =================
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.Enum("PRIVATE", "GROUP", name="chattype"), nullable=False),
        sa.Column("name", sa.String(255), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_message", sa.Text(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )

    # ================= CHAT MEMBERS =================
    op.create_table(
        "chat_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "role", sa.Enum("ADMIN", "MEMBER", name="memberrole"), nullable=False
        ),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["chat_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_index("idx_chat_members_user", "chat_members", ["user_id"])

    # ================= CHAT MESSAGES =================
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.Integer(), nullable=False),
        sa.Column("sender_id", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_edited", sa.Boolean(), nullable=False, default=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, default=False),
        sa.Column(
            "status",
            sa.Enum("SENT", "DELIVERED", "READ", name="messagestatus"),
            nullable=False,
            server_default="SENT",
        ),
        sa.Column("attachment_url", sa.String(500), nullable=True),
        sa.ForeignKeyConstraint(["chat_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["chat_messages.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"]),
    )

    op.create_index(
        "idx_messages_chat_created", "chat_messages", ["chat_id", "created_at"]
    )
    op.create_index(
        "idx_messages_chat_sender", "chat_messages", ["chat_id", "sender_id"]
    )
    op.create_index("idx_messages_parent", "chat_messages", ["parent_id"])

    # ================= MESSAGE REACTIONS =================
    op.create_table(
        "message_reactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reaction", sa.String(10), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.UniqueConstraint(
            "message_id", "user_id", name="uq_message_user"
        ),  # ✅ MERGED
    )

    # ================= MESSAGE READS =================
    op.create_table(
        "message_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )

    op.create_index(
        "idx_message_reads_msg_user", "message_reads", ["message_id", "user_id"]
    )

    # ================= SAFETY =================

    op.add_column(
        "safety_incidents",
        sa.Column(
            "safety_checklist_status",
            sa.Enum("COMPLETED", "PENDING", "FAILED", name="safetycheckliststatus"),
            nullable=False,
            server_default="PENDING",
        ),
    )

    op.add_column(
        "safety_incidents",
        sa.Column(
            "ppe_compliance", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )

    # ================= CLEANUP =================
    # safe drop (only if exists handled by MySQL silently in most cases)
    op.drop_index(op.f("uq_project_aadhaar"), table_name="labour")


def downgrade():
    op.create_index(
        op.f("uq_project_aadhaar"), "labour", ["aadhaar_number"], unique=True
    )

    op.drop_index("idx_message_reads_msg_user", table_name="message_reads")
    op.drop_table("message_reads")

    op.drop_table("message_reactions")

    op.drop_index("idx_messages_parent", table_name="chat_messages")
    op.drop_index("idx_messages_chat_sender", table_name="chat_messages")
    op.drop_index("idx_messages_chat_created", table_name="chat_messages")
    op.drop_table("chat_messages")

    op.drop_index("idx_chat_members_user", table_name="chat_members")
    op.drop_table("chat_members")

    op.drop_table("chat_sessions")

    op.alter_column("final_measurements", "extra_area", nullable=True)
    op.alter_column("final_measurements", "extra_rate", nullable=True)

    op.drop_column("safety_incidents", "ppe_compliance")
    op.drop_column("safety_incidents", "safety_checklist_status")

    sa.Enum(name="safetycheckliststatus").drop(op.get_bind(), checkfirst=True)

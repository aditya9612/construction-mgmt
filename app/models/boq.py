from decimal import Decimal
from typing import Optional

from sqlalchemy import DECIMAL, ForeignKey, Integer, String, Text, Index, Boolean, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.request_context import get_current_user_id
from app.models.base import Base, TimestampMixin


class BOQ(Base, TimestampMixin):
    __tablename__ = "boq_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )

    boq_group_id: Mapped[int] = mapped_column(Integer, nullable=False)

    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    is_latest: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1", index=True
    )

    item_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    category: Mapped[str] = mapped_column(String(100), nullable=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3), nullable=False, default=0, server_default="0"
    )

    unit: Mapped[str] = mapped_column(
        String(50), nullable=False, default="unit", server_default="unit"
    )

    unit_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, default=0, server_default="0"
    )

    total_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, default=0, server_default="0"
    )

    actual_quantity: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 3), nullable=False, default=0, server_default="0"
    )

    actual_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, default=0, server_default="0"
    )

    variance_cost: Mapped[Decimal] = mapped_column(
        DECIMAL(18, 2), nullable=False, default=0, server_default="0"
    )

    is_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="Active",
        server_default="Active",
        index=True,
    )

    project = relationship("Project")

    audit_logs = relationship(
        "BOQAudit", back_populates="boq", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_boq_project", "project_id"),
        Index("idx_boq_group", "boq_group_id"),
        Index("idx_boq_status", "status"),
        Index("idx_boq_latest", "is_latest"),
    )


class BOQAudit(Base, TimestampMixin):
    __tablename__ = "boq_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    boq_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("boq_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    action: Mapped[str] = mapped_column(String(50), nullable=False)

    message: Mapped[str] = mapped_column(String(255), nullable=False)

    changes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    boq = relationship("BOQ", back_populates="audit_logs")

    __table_args__ = (
        Index("idx_boq_audit_boq", "boq_id"),
        Index("idx_boq_audit_boq_created", "boq_id", "created_at"),
    )


# ------------------ AUTO AUDIT ------------------

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from decimal import Decimal

# 🔹 Ignore noisy/system fields
IGNORE_FIELDS = {"updated_at", "created_at", "total_cost", "variance_cost"}


def serialize_value(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


@event.listens_for(Session, "before_flush")
def auto_audit(session, flush_context, instances):

    # 🔹 UPDATE + SOFT DELETE
    for obj in session.dirty:
        if isinstance(obj, BOQ):
            if obj.id is None:
                continue

            insp = inspect(obj)
            changes = {}

            for attr in insp.attrs:
                if attr.key in IGNORE_FIELDS:
                    continue  #  ignore noisy fields

                if attr.history.has_changes():
                    changes[attr.key] = {
                        "old": (
                            serialize_value(attr.history.deleted[0])
                            if attr.history.deleted
                            else None
                        ),
                        "new": (
                            serialize_value(attr.history.added[0])
                            if attr.history.added
                            else None
                        ),
                    }

            if changes:
                # 🔹 sort changes (clean UI)
                changes = dict(sorted(changes.items()))

                # 🔹 detect delete vs update
                if "status" in changes and changes["status"]["new"] == "Deleted":
                    action = "DELETE"
                    message = "Soft delete"
                else:
                    action = "UPDATE"
                    message = f"{len(changes)} fields updated"  #  readable message

                session.add(
                    BOQAudit(
                        boq_id=obj.id,
                        action=action,
                        message=message,
                        changes=changes,
                        user_id=get_current_user_id(),
                    )
                )

    # 🔹 HARD DELETE
    for obj in session.deleted:
        if isinstance(obj, BOQ):
            if obj.id is None:
                continue

            session.add(
                BOQAudit(
                    boq_id=obj.id,
                    action="DELETE",
                    message="Hard delete",
                    user_id=get_current_user_id(),
                )
            )


@event.listens_for(Session, "after_flush")
def auto_audit_create(session, flush_context):

    # 🔹 CREATE
    for obj in session.new:
        if isinstance(obj, BOQ):
            if obj.id is None:
                continue

            session.add(
                BOQAudit(
                    boq_id=obj.id,
                    action="CREATE",
                    message="Item created",
                    user_id=get_current_user_id(),
                )
            )
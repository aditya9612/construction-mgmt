from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from enum import Enum

from sqlalchemy import (
    DECIMAL,
    Date,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# =====================================================
# ENUMS
# =====================================================


class WorkUpdateStatus(str, Enum):
    DRAFT = "Draft"
    SUBMITTED = "Submitted"


class WorkUpdateImageType(str, Enum):
    BEFORE = "Before"
    AFTER = "After"


# =====================================================
# WORK UPDATE
# =====================================================


class WorkUpdate(Base, TimestampMixin):
    __tablename__ = "work_updates"

    __table_args__ = (
        Index("idx_work_updates_project_date", "project_id", "work_date"),
        Index("idx_work_updates_status", "status"),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    business_id: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    activity_type_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("activity_types.id"),
        nullable=False,
        index=True,
    )

    created_by_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    work_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    before_remarks: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    after_remarks: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    work_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        index=True,
    )

    start_time: Mapped[time] = mapped_column(
        Time,
        nullable=False,
    )

    end_time: Mapped[time | None] = mapped_column(
        Time,
        nullable=True,
    )

    total_hours: Mapped[Decimal | None] = mapped_column(
        DECIMAL(5, 2),
        nullable=True,
    )

    location: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    status: Mapped[WorkUpdateStatus] = mapped_column(
        SAEnum(
            WorkUpdateStatus,
            values_callable=lambda x: [e.value for e in x],
        ),
        default=WorkUpdateStatus.DRAFT,
        nullable=False,
    )

    # ================= RELATIONSHIPS =================

    project = relationship("Project")

    task = relationship("Task")

    activity_type = relationship("ActivityType")

    created_by = relationship(
        "User",
        foreign_keys=[created_by_id],
    )

    images = relationship(
        "WorkUpdateImage",
        back_populates="work_update",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


# =====================================================
# WORK UPDATE IMAGES
# =====================================================


class WorkUpdateImage(Base, TimestampMixin):
    __tablename__ = "work_update_images"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    work_update_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("work_updates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    image_type: Mapped[WorkUpdateImageType] = mapped_column(
        SAEnum(
            WorkUpdateImageType,
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        index=True,
    )

    image_url: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    display_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    work_update = relationship(
        "WorkUpdate",
        back_populates="images",
    )

# app/models/document.py

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    Boolean,
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.core.enums import DocumentStatus


class Document(Base, TimestampMixin):
    __tablename__ = "document_management"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )

    document_type: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )  # Drawing, Invoice, etc.

    file_url: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    file_size: Mapped[Optional[int]] = mapped_column(
        Integer,
        nullable=True,
    )  # in bytes

    version: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        default="v1.0",
    )

    status: Mapped[DocumentStatus] = mapped_column(
        SAEnum(DocumentStatus),
        default=DocumentStatus.PENDING,
        nullable=False,
    )

    is_folder: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
    )

    # IMPORTANT: changed from "documents.id" to "document_management.id"
    parent_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("document_management.id", ondelete="CASCADE"),
        nullable=True,
    )

    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    remarks: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
    )

    # Relationships
    project = relationship("Project")
    uploader = relationship("User")
    children = relationship(
        "Document",
        backref="parent",
        remote_side=[id],
    )

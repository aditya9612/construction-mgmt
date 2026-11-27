"""
Database sequence models for atomic, race-condition free business code generation.
"""

from datetime import datetime
from sqlalchemy import String, BigInteger, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BusinessSequence(Base):
    """
    Persistent atomic sequence table for business identifiers (e.g., MAT, PRJ, CNT).
    Updated using database-level row locking (SELECT ... FOR UPDATE) to guarantee
    safe sequential allocation across multiple processes and concurrent transactions.
    """
    __tablename__ = "business_sequences"

    sequence_name: Mapped[str] = mapped_column(
        String(50),
        primary_key=True,
        index=True,
        comment="Sequence identifier, e.g. MAT, PRJ, CNT",
    )
    current_value: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        comment="Highest allocated sequence number for this identifier",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<BusinessSequence(sequence_name='{self.sequence_name}', current_value={self.current_value})>"

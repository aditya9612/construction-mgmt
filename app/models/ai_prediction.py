from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AIPrediction(Base, TimestampMixin):
    __tablename__ = "ai_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    module_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Store generic prediction payload (placeholder for now).
    prediction: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    predicted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=func.now(),
    )


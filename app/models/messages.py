from sqlalchemy import Column, Index, Integer, String, ForeignKey, DateTime, Text
from sqlalchemy.orm import relationship
from datetime import datetime
from app.models.base import Base
from sqlalchemy import Enum
import enum


class MessageStatus(str, enum.Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)

    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)

    message = Column(Text, nullable=False)      

    status = Column(
        Enum(MessageStatus, name="message_status"),
        default=MessageStatus.SENT,
        nullable=False
    )

    parent_id = Column(Integer, ForeignKey("messages.id"), nullable=True)

    attachment_url = Column(String(500), nullable=True)

    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    parent = relationship("Message", remote_side=[id])

    __table_args__ = (
        Index("idx_message_project", "project_id"),
        Index("idx_message_created", "created_at"),
    )
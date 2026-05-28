from sqlalchemy import Boolean, ForeignKey, Index, String, Text, Integer, DateTime, Enum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
import enum

from app.models.base import Base

class MessageStatus(str, enum.Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"

class ChatType(str, enum.Enum):
    PRIVATE = "private"
    GROUP = "group"

class MemberRole(str, enum.Enum):
    ADMIN = "admin"
    MEMBER = "member"


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    __table_args__ = (
        Index("idx_chat_last_message", "last_message_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[ChatType] = mapped_column(Enum(ChatType))
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    members = relationship("ChatMember", back_populates="chat")


class ChatMember(Base):
    __tablename__ = "chat_members"

    __table_args__ = (
        Index("idx_chat_members_user", "user_id"),
        Index("idx_member_last_read", "last_read_message_id"),
        UniqueConstraint("chat_id", "user_id", name="uq_chat_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    chat_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    role: Mapped[MemberRole] = mapped_column(
        Enum(MemberRole),
        default=MemberRole.MEMBER
    )

    is_muted: Mapped[bool] = mapped_column(default=False)
    is_archived: Mapped[bool] = mapped_column(default=False)

    last_read_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id"),
        nullable=True
    )

    last_read_at: Mapped[datetime | None] = mapped_column(nullable=True)

    joined_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    chat = relationship("ChatSession", back_populates="members")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    __table_args__ = (
        Index("idx_messages_chat_created", "chat_id", "created_at"),
        Index("idx_messages_chat_sender", "chat_id", "sender_id"),
        Index("idx_messages_parent", "parent_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    chat_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    message: Mapped[str] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    is_deleted: Mapped[bool] = mapped_column(default=False)

    is_edited: Mapped[bool] = mapped_column(default=False)
    is_pinned: Mapped[bool] = mapped_column(default=False)

    is_forwarded: Mapped[bool] = mapped_column(Boolean, default=False)

    forwarded_from_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id"),
        nullable=True
    )

    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus),
        default=MessageStatus.SENT
    )

    attachments = relationship(
        "MessageAttachment",
        back_populates="message",
        cascade="all, delete-orphan"
    )

    chat = relationship("ChatSession")
    sender = relationship("User")


class MessageRead(Base):
    __tablename__ = "message_reads"

    __table_args__ = (
        Index("idx_message_reads_msg_user", "message_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    read_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class MessageReaction(Base):
    __tablename__ = "message_reactions"

    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reaction: Mapped[str] = mapped_column(String(20))


class MessageAttachment(Base):
    __tablename__ = "message_attachments"

    __table_args__ = (
        Index("idx_attachment_message", "message_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="CASCADE"),
        nullable=True
    )

    file_url: Mapped[str] = mapped_column(String(500))

    file_type: Mapped[str | None] = mapped_column(String(100))
    file_name: Mapped[str | None] = mapped_column(String(255))

    file_size: Mapped[int | None]

    thumbnail_url: Mapped[str | None] = mapped_column(String(500))

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    message = relationship("ChatMessage", back_populates="attachments")
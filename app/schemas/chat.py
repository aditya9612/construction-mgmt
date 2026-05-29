from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime

from app.models.chat import MessageStatus


class CreateChat(BaseModel):
    user_id: int


class SendMessage(BaseModel):
    message: Optional[str] = None
    parent_id: Optional[int] = None
    attachment_ids: list[int] = Field(default_factory=list)


class AttachmentOut(BaseModel):
    id: int
    file_url: str
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    file_size: Optional[int] = None
    thumbnail_url: Optional[str] = None

    class Config:
        from_attributes = True


class SenderOut(BaseModel):
    id: int
    name: Optional[str] = None

    class Config:
        from_attributes = True


class ReactionOut(BaseModel):
    user_id: int
    reaction: str

    class Config:
        from_attributes = True


class ParentMessageOut(BaseModel):
    id: int
    message: str

    class Config:
        from_attributes = True


class MessageOut(BaseModel):
    id: int
    chat_id: int
    message: str
    sender_id: int
    created_at: datetime

    status: Optional[MessageStatus] = None

    parent_id: Optional[int] = None

    is_deleted: bool = False
    is_edited: bool = False
    is_pinned: bool = False

    sender: Optional[SenderOut] = None

    parent: Optional[ParentMessageOut] = None

    attachments: list[AttachmentOut] = Field(default_factory=list)

    reactions: list[ReactionOut] = Field(default_factory=list)

    read_by: list[int] = Field(default_factory=list)

    reply_count: int = 0

    class Config:
        from_attributes = True


class ChatListOut(BaseModel):
    id: int

    name: Optional[str] = None

    last_message: Optional[str] = None

    last_message_at: Optional[datetime] = None

    unread_count: int = 0

    class Config:
        from_attributes = True


class CreateGroup(BaseModel):
    name: str
    member_ids: list[int]


class ReplyOut(BaseModel):
    id: int

    message: str

    created_at: datetime

    attachments: list[AttachmentOut] = Field(default_factory=list)

    is_deleted: bool = False

    is_edited: bool = False

    sender: Optional[SenderOut] = None

    class Config:
        from_attributes = True


class ChatInfoOut(BaseModel):
    id: int

    type: str

    name: Optional[str] = None

    avatar_url: Optional[str] = None

    created_by: int

    created_at: datetime

    member_count: int = 0

    last_message: Optional[str] = None

    last_message_at: Optional[datetime] = None

    is_muted: bool = False

    is_archived: bool = False

    class Config:
        from_attributes = True


class ChatUserOut(BaseModel):
    user_id: int

    full_name: Optional[str] = None

    role: Optional[str] = None

    designation: Optional[str] = None

    profile_image: Optional[str] = None

    mobile_number: Optional[str] = None

    is_online: bool = False

    last_seen: Optional[str] = None

    class Config:
        from_attributes = True


class ChatUserSearchOut(BaseModel):
    user_id: int

    full_name: Optional[str] = None

    role: Optional[str] = None

    profile_image: Optional[str] = None

    class Config:
        from_attributes = True


class ChatMemberAddPayload(BaseModel):
    member_ids: list[int] = Field(default_factory=list)


class ChatListEnhancedOut(BaseModel):
    id: int

    type: str

    name: Optional[str] = None

    avatar_url: Optional[str] = None

    other_user_id: Optional[int] = None

    other_user_name: Optional[str] = None

    other_user_avatar: Optional[str] = None

    last_message: Optional[str] = None

    last_message_at: Optional[datetime] = None

    unread_count: int = 0

    class Config:
        from_attributes = True
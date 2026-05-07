from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CreateChat(BaseModel):
    user_id: int  # for private


class SendMessage(BaseModel):
    message: str
    parent_id: Optional[int] = None
    attachment_url: Optional[str] = None


class MessageOut(BaseModel):
    id: int
    message: str
    sender_id: int
    created_at: datetime

    class Config:
        from_attributes = True
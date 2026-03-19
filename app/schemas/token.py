from datetime import datetime
from typing import Any, Dict, Optional

from app.schemas.base import BaseSchema


class Token(BaseSchema):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseSchema):
    sub: str
    role: str
    exp: datetime


class AuthResponse(BaseSchema):
    token: Token
    user_id: int


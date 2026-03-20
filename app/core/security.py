from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings


pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_BCRYPT_MAX_SECRET_BYTES = 72


def _truncate_secret_to_bcrypt_limit(secret: str) -> bytes:
    """
    bcrypt limits the secret to 72 bytes (UTF-8 encoded) before hashing.
    Newer `passlib` versions raise instead of implicitly truncating.
    """
    return secret.encode("utf-8")[:_BCRYPT_MAX_SECRET_BYTES]


def get_password_hash(password: str) -> str:
    # Ensure we never pass >72-byte secrets to passlib/bcrypt.
    return pwd_context.hash(_truncate_secret_to_bcrypt_limit(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    # Verify using the same truncated secret bytes as hashing.
    return pwd_context.verify(_truncate_secret_to_bcrypt_limit(plain_password), hashed_password)


def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = dict(data)
    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])


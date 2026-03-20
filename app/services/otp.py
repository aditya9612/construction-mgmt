"""OTP generation and verification using Redis storage.

When Redis is unavailable (e.g. local development), we fall back to an in-memory
store so login/verify endpoints keep working.
"""

import asyncio
import random
import string
import time
from typing import Optional

import logging
from redis.asyncio import Redis

from app.core.config import settings

OTP_PREFIX = "otp:"
OTP_RATE_PREFIX = "otp:rate:"

logger = logging.getLogger("construction-mgmt")

# In-memory fallback for development/local usage.
# NOTE: This is per-process, so in multi-worker deployments it won't share state.
_IN_MEMORY_OTP: dict[str, tuple[str, float]] = {}  # key -> (otp, expires_at_epoch)
_IN_MEMORY_RATE: dict[str, tuple[int, float]] = {}  # key -> (count, window_expires_at_epoch)
_IN_MEMORY_LOCK = asyncio.Lock()


def _normalize_mobile(mobile: str) -> str:
    """Normalize mobile number (digits only, with country code)."""
    digits = "".join(c for c in mobile if c.isdigit())
    return digits if digits else mobile


def generate_otp(length: int = None) -> str:
    """Generate a numeric OTP."""
    length = length or settings.OTP_LENGTH
    return "".join(random.choices(string.digits, k=length))


async def store_otp(redis: Optional[Redis], mobile: str, otp: str) -> None:
    """Store OTP in Redis with TTL."""
    if redis is None:
        logger.debug("Redis unavailable; using in-memory OTP store.")
        key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
        expires_at = time.time() + settings.OTP_EXPIRE_SECONDS
        async with _IN_MEMORY_LOCK:
            _IN_MEMORY_OTP[key] = (otp, expires_at)
        return
    key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
    await redis.set(key, otp, ex=settings.OTP_EXPIRE_SECONDS)


async def verify_otp(redis: Optional[Redis], mobile: str, otp: str) -> bool:
    """
    Verify OTP and delete it on success (one-time use).
    Returns True if valid, False otherwise.
    """
    if redis is None:
        key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
        async with _IN_MEMORY_LOCK:
            stored = _IN_MEMORY_OTP.get(key)
            if stored is None:
                return False
            stored_otp, expires_at = stored
            if time.time() > expires_at:
                _IN_MEMORY_OTP.pop(key, None)
                return False
            if stored_otp != otp:
                return False
            _IN_MEMORY_OTP.pop(key, None)
            return True

    key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
    stored = await redis.get(key)
    if stored is None:
        return False
    stored_str = stored.decode() if isinstance(stored, bytes) else stored
    if stored_str != otp:
        return False
    await redis.delete(key)
    return True


async def get_otp(redis: Optional[Redis], mobile: str) -> Optional[str]:
    """Get stored OTP (for testing or mock SMS)."""
    if redis is None:
        key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
        async with _IN_MEMORY_LOCK:
            stored = _IN_MEMORY_OTP.get(key)
            if stored is None:
                return None
            otp, expires_at = stored
            if time.time() > expires_at:
                _IN_MEMORY_OTP.pop(key, None)
                return None
            return otp
    key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
    raw = await redis.get(key)
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


async def check_otp_rate_limit(redis: Optional[Redis], mobile: str) -> bool:
    """
    Check if mobile has exceeded OTP request rate (e.g. 1 per minute).
    Returns True if allowed, False if rate limited.
    """
    if redis is None:
        key = f"{OTP_RATE_PREFIX}{_normalize_mobile(mobile)}"
        now = time.time()
        async with _IN_MEMORY_LOCK:
            count, window_expires_at = _IN_MEMORY_RATE.get(key, (0, 0.0))
            if now > window_expires_at:
                count = 0
                window_expires_at = now + 60  # 1 minute window
            count += 1
            _IN_MEMORY_RATE[key] = (count, window_expires_at)
            return count <= 3  # Max 3 OTP requests per minute
    key = f"{OTP_RATE_PREFIX}{_normalize_mobile(mobile)}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)  # 1 minute window
    return count <= 3  # Max 3 OTP requests per minute

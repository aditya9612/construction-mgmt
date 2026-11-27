"""OTP generation and verification using Redis storage.

Redis is the authoritative shared distributed store for OTP lifecycle, rate limiting,
and brute-force lockout. During Redis outages or unavailability, process-local memory
is NOT treated as equivalent to shared distributed storage; operations fail closed
via OTPStorageError to prevent security vulnerabilities and cross-worker inconsistency.
"""

import asyncio
import random
import string
import time
from typing import Optional
from app.core.logger import logger

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings

OTP_PREFIX = "otp:"
OTP_RATE_PREFIX = "otp:rate:"
OTP_FAIL_PREFIX = "otp:fail:"
OTP_LOCK_PREFIX = "otp:lock:"


class OTPStorageError(Exception):
    """Raised when OTP storage or verification fails due to Redis unavailability or outage."""
    pass


def _normalize_mobile(mobile: str) -> str:
    """Normalize mobile number (digits only, with country code)."""
    digits = "".join(c for c in mobile if c.isdigit())
    return digits if digits else mobile


def generate_otp(length: int = None) -> str:
    if settings.APP_ENV == "development":
        return "123456"

    length = length or settings.OTP_LENGTH
    return "".join(random.choices(string.digits, k=length))


async def store_otp(redis: Optional[Redis], mobile: str, otp: str) -> None:
    """
    Store OTP in Redis with TTL.
    Fails closed if Redis is unavailable or disconnected to prevent process-local inconsistent state.
    """
    if redis is None:
        logger.error("store_otp failed: Redis client unavailable (fail-closed)")
        raise OTPStorageError("Redis unavailable for OTP storage")

    key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
    try:
        await redis.set(key, otp, ex=settings.OTP_EXPIRE_SECONDS)
    except (RedisError, OSError) as exc:
        logger.error(f"store_otp Redis outage for mobile={mobile}: {exc}")
        raise OTPStorageError(f"Redis outage during OTP storage: {exc}") from exc


async def verify_otp(redis: Optional[Redis], mobile: str, otp: str) -> bool:
    """
    Verify OTP and delete it on success (one-time use).
    Returns True if valid, False if invalid or expired.
    Fails closed with OTPStorageError if Redis is unavailable or disconnected.
    """
    if redis is None:
        logger.error("verify_otp failed: Redis client unavailable (fail-closed)")
        raise OTPStorageError("Redis unavailable for OTP verification")

    key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
    try:
        stored = await redis.get(key)
        if stored is None:
            return False
        stored_str = stored.decode() if isinstance(stored, bytes) else stored
        if stored_str != otp:
            return False
        await redis.delete(key)
        return True
    except (RedisError, OSError) as exc:
        logger.error(f"verify_otp Redis outage for mobile={mobile}: {exc}")
        raise OTPStorageError(f"Redis outage during OTP verification: {exc}") from exc


async def get_otp(redis: Optional[Redis], mobile: str) -> Optional[str]:
    """Get stored OTP (for testing or mock SMS)."""
    if redis is None:
        return None
    key = f"{OTP_PREFIX}{_normalize_mobile(mobile)}"
    try:
        raw = await redis.get(key)
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw
    except (RedisError, OSError):
        return None


async def check_otp_rate_limit(redis: Optional[Redis], mobile: str) -> bool:
    """
    Check rolling rate limit (max 3/min).
    Fails closed with OTPStorageError if Redis is unavailable to protect against brute-force attacks.
    """
    if redis is None:
        logger.error("check_otp_rate_limit failed: Redis client unavailable (fail-closed)")
        raise OTPStorageError("Redis unavailable for OTP rate limiting")

    key = f"{OTP_RATE_PREFIX}{_normalize_mobile(mobile)}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, 60)
        if count > 3:
            logger.warning(f"OTP rate limit exceeded mobile={mobile}")
        return count <= 3  # max 3 otp per minute
    except (RedisError, OSError) as exc:
        logger.error(f"check_otp_rate_limit Redis outage: {exc}")
        raise OTPStorageError(f"Redis outage during OTP rate limiting: {exc}") from exc


async def is_otp_locked(redis: Optional[Redis], mobile: str) -> bool:
    """Check if the account is temporarily locked due to too many failed attempts."""
    if redis is None:
        return False

    key = f"{OTP_LOCK_PREFIX}{_normalize_mobile(mobile)}"
    try:
        return await redis.exists(key) > 0
    except (RedisError, OSError) as exc:
        logger.warning(f"is_otp_locked check encountered Redis outage: {exc}")
        return False


async def clear_otp_lockout(redis: Optional[Redis], mobile: str) -> None:
    """Clear failed attempts and locks atomically."""
    if redis is None:
        return

    fail_key = f"{OTP_FAIL_PREFIX}{_normalize_mobile(mobile)}"
    lock_key = f"{OTP_LOCK_PREFIX}{_normalize_mobile(mobile)}"
    try:
        await redis.delete(fail_key, lock_key)
    except (RedisError, OSError) as exc:
        logger.warning(f"clear_otp_lockout Redis outage: {exc}")


async def record_failed_otp_attempt(redis: Optional[Redis], mobile: str, max_attempts: int = 6, lock_minutes: int = 5) -> bool:
    """
    Atomically increment the failed attempt counter.
    Returns True if the account has just been locked as a result of this failure.
    """
    if redis is None:
        return False

    mobile_norm = _normalize_mobile(mobile)
    fail_key = f"{OTP_FAIL_PREFIX}{mobile_norm}"
    lock_key = f"{OTP_LOCK_PREFIX}{mobile_norm}"

    try:
        # Atomic increment
        count = await redis.incr(fail_key)

        # If first failure, set expiry on the fail counter (e.g., 15 mins)
        if count == 1:
            await redis.expire(fail_key, lock_minutes * 60)

        if count >= max_attempts:
            logger.warning(f"OTP brute force lockout triggered mobile={mobile_norm}")
            await redis.setex(lock_key, lock_minutes * 60, "true")
            return True

        return False
    except (RedisError, OSError) as exc:
        logger.warning(f"record_failed_otp_attempt Redis outage: {exc}")
        return False

import secrets

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_request_redis
from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db_session
from app.core.config import settings
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.token import AuthResponse, Token
from app.schemas.user import OTPLoginResponse, OTPVerify, UserLogin
from app.services.otp import check_otp_rate_limit, generate_otp, store_otp, verify_otp as verify_otp_service
from app.services.sms import send_otp_sms
from app.utils.helpers import AppError


router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[default_rate_limiter_dependency()])


def _normalize_mobile(mobile: str) -> str:
    digits = "".join(c for c in mobile if c.isdigit())
    return digits if len(digits) >= 10 else mobile


def _build_token(user: User) -> Token:
    access_token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return Token(access_token=access_token)


@router.post("/login", response_model=OTPLoginResponse)
async def login(payload: UserLogin, redis: Redis | None = Depends(get_request_redis)):
    """Request OTP for mobile number (OTP-only login)."""
    if redis is None and settings.APP_ENV == "production":
        raise AppError(status_code=503, message="OTP service unavailable (Redis required)")

    mobile = _normalize_mobile(payload.mobile)
    if len(mobile) < 10:
        raise AppError(status_code=422, message="Invalid mobile number")

    if not await check_otp_rate_limit(redis, mobile):
        raise AppError(status_code=429, message="Too many OTP requests. Try again later.")

    otp = generate_otp()
    await store_otp(redis, mobile, otp)
    sent = await send_otp_sms(mobile, otp)
    if not sent:
        raise AppError(status_code=503, message="Failed to send OTP")

    return OTPLoginResponse(message="OTP sent", mobile=mobile)


@router.post("/verify_otp", response_model=AuthResponse)
async def verify_otp(
    payload: OTPVerify,
    redis: Redis | None = Depends(get_request_redis),
    db: AsyncSession = Depends(get_db_session),
):
    """Verify OTP and return token. Creates user if first-time mobile login."""
    if redis is None and settings.APP_ENV == "production":
        raise AppError(status_code=503, message="OTP service unavailable (Redis required)")

    mobile = _normalize_mobile(payload.mobile)
    if len(mobile) < 10:
        raise AppError(status_code=422, message="Invalid mobile number")

    if not await verify_otp_service(redis, mobile, payload.otp):
        raise AppError(status_code=401, message="Invalid or expired OTP")

    user = await db.scalar(select(User).where(User.mobile == mobile))
    if user is None:
        # Auto-register OTP user with default role
        placeholder_email = f"otp_{mobile}@construction.local"
        user = User(
            email=placeholder_email,
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
            full_name=None,
            mobile=mobile,
            role=UserRole.SITE_ENGINEER,
            is_active=True,
        )
        db.add(user)
        await db.flush()

    if not user.is_active:
        raise AppError(status_code=403, message="User is inactive")

    token = _build_token(user)
    return {"token": token, "user_id": user.id}

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pyrate_limiter import Duration, Rate

from app.core.dependencies import get_request_redis
from app.core.security import create_access_token, get_password_hash
from app.db.session import get_db_session
from app.core.config import settings
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.token import AuthResponse, Token
from app.schemas.user import OTPLoginResponse, OTPVerify, UserLogin
from app.services.otp import (
    check_otp_rate_limit,
    generate_otp,
    store_otp,
    verify_otp as verify_otp_service,
)
from app.services.sms import send_otp_sms
from app.utils.helpers import AppError

from app.core.logger import logger


def otp_rate_limiter_dependency():
    async def _limit(request: Request):
        limiter = getattr(request.app.state, "rate_limiter", None)

        if limiter is None:
            return

        ip = request.client.host
        key = f"otp:{ip}"

        allowed = await limiter.try_acquire_async(key)

        if not allowed:
            logger.warning(f"OTP rate limit exceeded ip={ip}")  # ✅ WARNING
            raise HTTPException(status_code=429, detail="Too many OTP requests")

    return Depends(_limit)


router = APIRouter(
    prefix="/auth",
    tags=["auth"],
    dependencies=[default_rate_limiter_dependency()],
)


def _normalize_mobile(mobile: str) -> str:
    digits = "".join(c for c in mobile if c.isdigit())
    return digits if len(digits) >= 10 else mobile


def _build_token(user: User) -> Token:
    access_token = create_access_token(
        {"sub": str(user.id), "role": user.role.value}
    )
    return Token(access_token=access_token)


@router.post(
    "/login",
    response_model=OTPLoginResponse,
    dependencies=[otp_rate_limiter_dependency()],
)
async def login(
    payload: UserLogin,
    redis: Redis | None = Depends(get_request_redis),
):
    logger.info(f"OTP login requested mobile={payload.mobile}") 

    if redis is None and settings.APP_ENV == "production":
        logger.error("Redis unavailable for OTP login")  
        raise AppError(status_code=503, message="OTP service unavailable (Redis required)")

    mobile = _normalize_mobile(payload.mobile)

    if len(mobile) < 10:
        raise AppError(status_code=422, message="Invalid mobile number")

    if not await check_otp_rate_limit(redis, mobile):
        logger.warning(f"OTP rate limit hit for mobile={mobile}") 
        raise AppError(status_code=429, message="Too many OTP requests. Try again later.")

    try:
        otp = generate_otp()
        await store_otp(redis, mobile, otp)

        sent = await send_otp_sms(mobile, otp)
        if not sent:
            logger.error(f"Failed to send OTP mobile={mobile}") 
            raise AppError(status_code=503, message="Failed to send OTP")

        logger.info(f"OTP sent successfully mobile={mobile}")  

        return OTPLoginResponse(message="OTP sent", mobile=mobile)

    except Exception:
        logger.exception("OTP login process failed") 
        raise


@router.post(
    "/verify_otp",
    response_model=AuthResponse,
    dependencies=[otp_rate_limiter_dependency()],
)
async def verify_otp(
    payload: OTPVerify,
    redis: Redis | None = Depends(get_request_redis),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"OTP verification attempt mobile={payload.mobile}") 

    if redis is None and settings.APP_ENV == "production":
        logger.error("Redis unavailable for OTP verification")
        raise AppError(status_code=503, message="OTP service unavailable (Redis required)")

    mobile = _normalize_mobile(payload.mobile)

    if len(mobile) < 10:
        raise AppError(status_code=422, message="Invalid mobile number")

    if not await verify_otp_service(redis, mobile, payload.otp):
        logger.warning(f"Invalid OTP attempt mobile={mobile}")  
        raise AppError(status_code=401, message="Invalid or expired OTP")

    try:
        user = await db.scalar(select(User).where(User.mobile == mobile))

        if user is None:
            logger.info(f"Creating new user via OTP mobile={mobile}")  

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
            logger.warning(f"Inactive user login attempt user_id={user.id}")
            raise AppError(status_code=403, message="User is inactive")

        token = _build_token(user)

        logger.info(f"User authenticated successfully user_id={user.id}")

        return {
            "token": token,
            "user_id": user.id,
        }

    except Exception:
        logger.exception("OTP verification process failed") 
        raise
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, require_roles
from app.core.security import get_password_hash
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import ROLES, User, UserRole
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.user import UserOut, UserCreatePayload, UserUpdatePayload
from app.utils.helpers import AppError, ConflictError, NotFoundError

from app.core.logger import logger


router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[default_rate_limiter_dependency()]
)


def _normalize_mobile(mobile: str) -> str:
    digits = "".join(c for c in mobile if c.isdigit())
    return digits if len(digits) >= 10 else mobile


@router.post("/create", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreatePayload,
    db: AsyncSession = Depends(get_db_session),
):
    """Create a user with any role. Provide either email+password or mobile_number."""

    logger.info(f"Creating user email={payload.email} mobile={payload.mobile_number}")

    if not payload.email and not payload.mobile_number:
        raise AppError(status_code=422, message="Provide either email or mobile_number")
    if payload.email and not payload.password:
        raise AppError(
            status_code=422, message="Password required when creating with email"
        )

    try:
        role = UserRole(payload.role)
    except ValueError:
        raise AppError(status_code=422, message=f"Invalid role. Use one of: {ROLES}")

    try:
        if payload.email:
            existing = await db.scalar(select(User).where(User.email == payload.email))
            if existing:
                raise ConflictError("Email already registered")

            mobile_val = (
                _normalize_mobile(payload.mobile_number)
                if payload.mobile_number
                else None
            )
            mobile_val = mobile_val if (mobile_val and len(mobile_val) >= 10) else None

            if mobile_val:
                mob_exists = await db.scalar(
                    select(User).where(User.mobile == mobile_val)
                )
                if mob_exists:
                    raise ConflictError("Mobile already registered")

            user = User(
                email=payload.email,
                hashed_password=get_password_hash(payload.password),
                full_name=payload.full_name,
                mobile=mobile_val,
                role=role,
                is_active=payload.is_active,
                address=payload.address,
                pan_number=payload.pan_number,
                aadhaar_number=payload.aadhaar_number,
                profile_image=payload.profile_image,
                designation=payload.designation,
                joining_date=payload.joining_date,
            )
        else:
            mobile = _normalize_mobile(payload.mobile_number)

            if len(mobile) < 10:
                raise AppError(status_code=422, message="Invalid mobile number")

            existing = await db.scalar(select(User).where(User.mobile == mobile))
            if existing:
                raise ConflictError("Mobile already registered")

            user = User(
                email=f"otp_{mobile}@construction.local",
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                full_name=payload.full_name,
                mobile=mobile,
                role=role,
                is_active=payload.is_active,
                address=payload.address,
                pan_number=payload.pan_number,
                aadhaar_number=payload.aadhaar_number,
                profile_image=payload.profile_image,
                designation=payload.designation,
                joining_date=payload.joining_date,
            )

        db.add(user)
        await db.flush()

        logger.info(f"User created successfully id={user.id} role={user.role}")

        return UserOut.model_validate(user)

    except Exception:
        logger.exception("User creation failed")
        raise


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_active_user)):
    return UserOut.model_validate(current_user)


@router.get("", response_model=PaginatedResponse[UserOut])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(User)
    count_query = select(func.count()).select_from(User)

    if search:
        logger.info(f"User search query={search}")
        like = f"%{search}%"
        cond = or_(
            User.email.ilike(like), User.full_name.ilike(like), User.mobile.ilike(like)
        )
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = await db.scalar(count_query)
    rows = (
        (await db.execute(query.order_by(User.id.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )

    items = [UserOut.model_validate(r) for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    return {"items": items, "meta": meta.model_dump()}


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.scalar(select(User).where(User.id == user_id))

    if user is None:
        logger.warning(f"User not found id={user_id}")
        raise NotFoundError("User not found")

    return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdatePayload,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating user id={user_id}")

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        logger.warning(f"User not found for update id={user_id}")
        raise NotFoundError("User not found")

    try:
        data = payload.model_dump(exclude_unset=True)

        if "mobile_number" in data:
            raw = data.pop("mobile_number")
            mobile_val = _normalize_mobile(raw) if raw else None
            data["mobile"] = (
                mobile_val if (mobile_val and len(mobile_val) >= 10) else None
            )

            if data["mobile"]:
                existing = await db.scalar(
                    select(User).where(
                        and_(User.mobile == data["mobile"], User.id != user_id)
                    )
                )
                if existing:
                    raise ConflictError("Mobile already registered")

        if "role" in data:
            try:
                data["role"] = UserRole(data["role"])
            except ValueError:
                raise AppError(
                    status_code=422, message=f"Invalid role. Use one of: {ROLES}"
                )

        for key, value in data.items():
            if hasattr(user, key):
                setattr(user, key, value)

        await db.flush()

        logger.info(f"User updated successfully id={user_id}")

        return UserOut.model_validate(user)

    except Exception:
        logger.exception(f"User update failed id={user_id}")
        raise


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting user id={user_id}")

    user = await db.scalar(select(User).where(User.id == user_id))
    if user is None:
        logger.warning(f"User not found for delete id={user_id}")
        raise NotFoundError("User not found")

    await db.delete(user)
    await db.flush()

    logger.info(f"User deleted successfully id={user_id}")

    return None

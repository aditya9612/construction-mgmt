from datetime import date, datetime, time
import secrets
from typing import Optional
from pydantic import ValidationError
from sqlalchemy import String, and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.cache.redis import cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.core.security import get_password_hash
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import ROLES, ActivityLog, User, UserAuditLog, UserRole
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.user import UserAuditOut, UserOut, UserCreatePayload, UserUpdatePayload
from app.utils.helpers import AppError, ConflictError, NotFoundError
from app.core.logger import logger
from fastapi import Depends, File, Request, UploadFile, Query, APIRouter
import os, shutil
from uuid import uuid4

MAX_IMAGE_SIZE = 5 * 1024 * 1024

UPLOAD_DIR = "uploads/profile"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def save_profile_image(file: UploadFile) -> str:
    ext = file.filename.split(".")[-1].lower()
    filename = f"{uuid4()}.{ext}"

    path = os.path.join(UPLOAD_DIR, filename)

    with open(path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    #  return URL path instead of system path
    return f"/uploads/profile/{filename}"


async def validate_and_save_image(profile_image: UploadFile) -> str:
    if not profile_image.content_type.startswith("image/"):
        raise AppError(status_code=400, message="Only image files allowed")

    if not profile_image.filename or "." not in profile_image.filename:
        raise AppError(status_code=400, message="Invalid file name")

    allowed_extensions = {"jpg", "jpeg", "png", "webp"}
    ext = profile_image.filename.split(".")[-1].lower()

    if ext not in allowed_extensions:
        raise AppError(status_code=400, message="Invalid image format")

    content = await profile_image.read()
    if len(content) > MAX_IMAGE_SIZE:
        raise AppError(status_code=400, message="Image too large")

    profile_image.file.seek(0)

    return save_profile_image(profile_image)


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await get_current_active_user(request=request, db=db)
    except Exception:
        return None


AUDIT_ALLOWED_FIELDS = {
    "full_name",
    "email",
    "mobile",
    "role",
    "address",
    "pan_number",
    "aadhaar_number",
    "designation",
    "joining_date",
    "profile_image",
}


async def log_user_changes(
    db, user, data: dict, current_user_id: int, change_group_id: str
):
    logs = []

    for field, new_value in data.items():
        if field not in AUDIT_ALLOWED_FIELDS:
            continue

        if hasattr(user, field):
            old_value = getattr(user, field)

            if old_value != new_value:
                logs.append(
                    UserAuditLog(
                        user_id=user.id,
                        field_name=field,
                        old_value=str(old_value) if old_value is not None else None,
                        new_value=str(new_value) if new_value is not None else None,
                        changed_by=current_user_id,
                        change_group_id=change_group_id,
                    )
                )

    if logs:
        db.add_all(logs)


async def log_activity(
    db,
    action: str,
    entity: str,
    entity_id: int,
    performed_by: Optional[int],
    details: dict | None = None,
):
    log = ActivityLog(
        action=action,
        entity=entity,
        entity_id=entity_id,
        performed_by=performed_by,
        details=details,
    )
    db.add(log)


router = APIRouter(
    prefix="/users", tags=["users"], dependencies=[default_rate_limiter_dependency()]
)


@router.post("/create", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreatePayload = Depends(),
    profile_image: UploadFile = File(None),
    current_user: Optional[User] = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a user with any role. Provide either email+password or mobile_number."""

    logger.info(f"Creating user email={payload.email} mobile={payload.mobile_number}")

    try:
        payload = UserCreatePayload(**payload.model_dump())
    except ValidationError as e:
        raise AppError(422, str(e))

    # ------------------------
    # VALIDATION (KEEP OLD BEHAVIOR)
    # ------------------------
    try:
        # role = UserRole(payload.role)
        role = UserRole(payload.role).value
    except ValueError:
        raise AppError(422, f"Invalid role. Use one of: {ROLES}")

    try:
        # ------------------------
        # ACTOR (AUDIT SUPPORT)
        # ------------------------
        creator_id = current_user.id if current_user else None

        # ------------------------
        # IMAGE
        # ------------------------
        image_path = None
        if profile_image:
            image_path = await validate_and_save_image(profile_image)

        # ------------------------
        # NORMALIZATION (KEEP OLD LOGIC SAFE)
        # ------------------------
        mobile_val = payload.mobile_number.strip() if payload.mobile_number else None

        email = payload.email.strip().lower() if payload.email else None

        # ------------------------
        # EMAIL FLOW (UNCHANGED + FIXED)
        # ------------------------
        if email:
            # EMAIL UNIQUE CHECK
            existing_email = await db.scalar(select(User).where(User.email == email))
            if existing_email:
                raise ConflictError("Email already registered")

            # MOBILE NORMALIZATION
            if mobile_val:
                mobile_val = mobile_val if len(mobile_val) >= 10 else None

            # MOBILE UNIQUE CHECK
            if mobile_val:
                existing_mobile = await db.scalar(
                    select(User).where(User.mobile == mobile_val)
                )
                if existing_mobile:
                    raise ConflictError("Mobile already registered")

            user = User(
                email=email,
                hashed_password=get_password_hash( payload.password or secrets.token_urlsafe(32) ),
                full_name=payload.full_name,
                mobile=mobile_val,
                role=role,
                is_active=payload.is_active,
                address=payload.address,
                pan_number=payload.pan_number,
                aadhaar_number=payload.aadhaar_number,
                profile_image=image_path,
                designation=payload.designation,
                joining_date=payload.joining_date,
                created_by=creator_id,
            )

        # ------------------------
        # MOBILE-ONLY FLOW (UNCHANGED + FIXED)
        # ------------------------
        else:
            mobile = payload.mobile_number.strip()

            if not mobile or len(mobile) < 10:
                raise AppError(422, "Invalid mobile number")

            # MOBILE UNIQUE CHECK
            existing_mobile = await db.scalar(select(User).where(User.mobile == mobile))
            if existing_mobile:
                raise ConflictError("Mobile already registered")

            # GENERATED EMAIL (IMPORTANT FIX)
            final_email = f"otp_{mobile}@construction.local"

            existing_email = await db.scalar(
                select(User).where(User.email == final_email)
            )
            if existing_email:
                raise ConflictError("Email already registered")

            user = User(
                email=final_email,
                hashed_password=get_password_hash(secrets.token_urlsafe(32)),
                full_name=payload.full_name,
                mobile=mobile,
                role=role,
                is_active=payload.is_active,
                address=payload.address,
                pan_number=payload.pan_number,
                aadhaar_number=payload.aadhaar_number,
                profile_image=image_path,
                designation=payload.designation,
                joining_date=payload.joining_date,
                created_by=creator_id,
            )

        # ------------------------
        # SAVE
        # ------------------------
        db.add(user)
        await db.flush()

        # ------------------------
        # ACTIVITY LOG (NEW BUT SAFE)
        # ------------------------
        await log_activity(
            db,
            action="CREATE_USER",
            entity="USER",
            entity_id=user.id,
            performed_by=creator_id,
        )

        logger.info(f"User created successfully id={user.id} role={user.role}")

        return UserOut.model_validate(user)

    except Exception:
        logger.exception("User creation failed")
        raise


@router.get("/me", response_model=UserOut)
async def me(current_user: User = Depends(get_current_active_user)):
    return UserOut.model_validate(current_user)


# @router.get("", response_model=PaginatedResponse[UserOut])
# async def list_users(
#     limit: int = Query(20, ge=1, le=100),
#     offset: int = Query(0, ge=0),
#     search: Optional[str] = None,
#     current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
#     db: AsyncSession = Depends(get_db_session),
# ):
#     query = select(User).where(User.is_deleted == False)
#     count_query = select(func.count()).select_from(User).where(User.is_deleted == False)

#     if search:
#         logger.info(f"User search query={search}")
#         like = f"%{search}%"
#         cond = or_(
#             User.email.ilike(like),
#             User.full_name.ilike(like),
#             User.mobile.cast(String).ilike(like),
#         )
#         query = query.where(cond)
#         count_query = count_query.where(cond)

#     total = await db.scalar(count_query)
#     rows = (
#         (await db.execute(query.order_by(User.id.desc()).limit(limit).offset(offset)))
#         .scalars()
#         .all()
#     )

#     items = [UserOut.model_validate(r) for r in rows]
#     meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
#     return {"items": items, "meta": meta.model_dump()}

VERSION_KEY = "cache_version:users"

@router.get("", response_model=PaginatedResponse[UserOut])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)

    cache_key = (
        f"cache:users:list:{version}:"
        f"{current_user.id}:{current_user.role}:"
        f"{limit}:{offset}:{search}"
    )

    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[UserOut].model_validate(cached)

    query = select(User).where(User.is_deleted == False)
    count_query = select(func.count()).select_from(User).where(
        User.is_deleted == False
    )

    if search:
        like = f"%{search}%"
        cond = or_(
            User.email.ilike(like),
            User.full_name.ilike(like),
            User.mobile.cast(String).ilike(like),
        )
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = await db.scalar(count_query)

    rows = (
        (
            await db.execute(
                query.order_by(User.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    items = [UserOut.model_validate(r) for r in rows]
    meta = PaginationMeta(
        total=int(total or 0),
        limit=limit,
        offset=offset,
    )

    result = PaginatedResponse[UserOut](
        items=items,
        meta=meta,
    )

    await cache_set_json(redis, cache_key, result.model_dump())

    return result

@router.get("/role-counts")
async def get_role_counts(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_active_user),
):
    rows = (
        await db.execute(
            select(User.role, func.count(User.id))
            .where(
                User.is_deleted == False,
                User.is_active == True,
            )
            .group_by(User.role)
        )
    ).all()

    # Initialize all roles with 0
    result = {role: 0 for role in ROLES}

    # Fill actual counts
    for role, count in rows:
        result[role] = count

    return result

@router.get("/roles")
async def list_roles_by_status(
    status: str = Query(
        "all",
        pattern="^(all|active|inactive)$",
        description="Filter roles by user status: all, active, inactive"
    ),
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Return unique roles based on user active/inactive status.
    """

    # Base query
    query = (
        select(
            User.role,
            func.count(User.id).label("user_count")
        )
        .where(User.is_deleted == False)
    )

    # Apply status filter
    if status == "active":
        query = query.where(User.is_active == True)

    elif status == "inactive":
        query = query.where(User.is_active == False)

    # Group by role
    query = query.group_by(User.role).order_by(User.role)

    # Execute query
    rows = (await db.execute(query)).all()

    # Format response
    items = [
        {
            "role": role,
            "user_count": count
        }
        for role, count in rows
    ]

    return {
        "items": items
    }


@router.put("/roles/{role}/status")
async def update_role_status(
    role: str,
    is_active: bool = Query(
        ...,
        description="true = activate role users, false = deactivate role users"
    ),
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Activate or deactivate all users of a given role.

    Authorization:
        - Only Admin users can call this endpoint.

    Business Rules:
        - Admin can activate/deactivate all roles except Admin itself.
        - Admin role cannot be deactivated because at least one active
          administrator must always exist to manage the system.
    """

    # Validate role
    if role not in ROLES:
        raise AppError(
            status_code=404,
            message=f"Invalid role. Available roles: {ROLES}"
        )

    # Prevent deactivation of Admin role
    if role == UserRole.ADMIN.value and not is_active:
        raise AppError(
            status_code=400,
            message=(
                "Admin role cannot be deactivated because at least one "
                "active administrator is required to manage the system."
            )
        )

    # Fetch all non-deleted users with this role
    result = await db.execute(
        select(User).where(
            User.role == role,
            User.is_deleted == False
        )
    )
    users = result.scalars().all()

    if not users:
        raise NotFoundError(f"No users found for role '{role}'")

    # Update only users whose status is changing
    updated_count = 0

    for user in users:
        if user.is_active != is_active:
            user.is_active = is_active
            user.updated_by = current_user.id
            updated_count += 1

    await db.flush()

    # Activity log
    await log_activity(
        db,
        action="UPDATE_ROLE_STATUS",
        entity="ROLE",
        entity_id=0,
        performed_by=current_user.id,
        details={
            "role": role,
            "is_active": is_active,
            "updated_users": updated_count,
        },
    )

    return {
        "message": "Role status updated successfully",
        "role": role,
        "is_active": is_active,
        "updated_users": updated_count,
    }


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.scalar(
        select(User).where(User.id == user_id, User.is_deleted == False)
    )

    if user is None:
        logger.warning(f"User not found id={user_id}")
        raise NotFoundError("User not found")

    return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdatePayload = Depends(),
    profile_image: UploadFile = File(None),
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Updating user id={user_id}")

    user = await db.scalar(
        select(User).where(User.id == user_id, User.is_deleted == False)
    )
    if user is None:
        raise NotFoundError("User not found")

    try:
        data = payload.model_dump(exclude_unset=True)

        data = {k: v for k, v in data.items() if v is not None and v != ""}

        old_image_path = user.profile_image

        # ------------------------
        # IMAGE
        # ------------------------
        if profile_image:
            data["profile_image"] = await validate_and_save_image(profile_image)

        # ------------------------
        # MOBILE
        # ------------------------
        if "mobile_number" in data:
            mobile_val = data.pop("mobile_number")

            mobile_val = mobile_val.strip()
            data["mobile"] = mobile_val

            existing = await db.scalar(
                select(User).where(and_(User.mobile == mobile_val, User.id != user_id))
            )
            if existing:
                raise ConflictError("Mobile already registered")

        # ------------------------
        # EMAIL
        # ------------------------
        if "email" in data:

            data["email"] = data["email"].strip().lower()

            existing = await db.scalar(
                select(User).where(
                    and_(User.email == data["email"], User.id != user_id)
                )
            )
            if existing:
                raise ConflictError("Email already registered")

        # ------------------------
        # ROLE
        # ------------------------
        if "role" in data:
            try:
                # data["role"] = UserRole(data["role"])
                data["role"] = UserRole(data["role"]).value
            except ValueError:
                raise AppError(422, f"Invalid role. Use one of: {ROLES}")

        # ------------------------
        # FINAL SAFETY CHECK
        # ------------------------
        final_email = data.get("email", user.email)
        final_mobile = data.get("mobile", user.mobile)

        if not final_email or not final_mobile:
            raise AppError(422, "Both email and mobile_number are required")

        # ------------------------
        # AUDIT
        # ------------------------
        change_group_id = str(uuid4())
        await log_user_changes(db, user, data, current_user.id, change_group_id)

        # ------------------------
        # APPLY CHANGES
        # ------------------------
        updated_fields = []

        for key, value in data.items():
            if hasattr(user, key) and getattr(user, key) != value:
                setattr(user, key, value)
                updated_fields.append(key)

        user.updated_by = current_user.id
        await db.flush()

        # ------------------------
        # ACTIVITY LOG
        # ------------------------
        await log_activity(
            db,
            action="UPDATE_USER",
            entity="USER",
            entity_id=user.id,
            performed_by=current_user.id,
            details={"fields_updated": updated_fields},
        )

        # ------------------------
        # CLEAN OLD IMAGE
        # ------------------------
        if profile_image and old_image_path and old_image_path.startswith("/uploads"):
            old_path = os.path.join(".", old_image_path.lstrip("/"))
            if os.path.exists(old_path) and os.path.isfile(old_path):
                os.remove(old_path)

        return UserOut.model_validate(user)

    except Exception:
        logger.exception(f"User update failed id={user_id}")
        raise


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Deleting user id={user_id}")

    user = await db.scalar(
        select(User).where(User.id == user_id, User.is_deleted == False)
    )

    if user is None:
        raise NotFoundError("User not found")

    user.is_deleted = True
    user.deleted_at = date.today()
    user.updated_by = current_user.id

    await db.flush()

    await log_activity(
        db,
        action="DELETE_USER",
        entity="USER",
        entity_id=user.id,
        performed_by=current_user.id,
    )

    logger.info(f"User soft deleted id={user_id}")

    return None


@router.put("/{user_id}/restore", response_model=UserOut)
async def restore_user(
    user_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Restoring user id={user_id}")

    user = await db.scalar(
        select(User).where(User.id == user_id, User.is_deleted == True)
    )

    if user is None:
        raise NotFoundError("Deleted user not found")

    user.is_deleted = False
    user.deleted_at = None
    user.updated_by = current_user.id

    await db.flush()

    await log_activity(
        db,
        action="RESTORE_USER",
        entity="USER",
        entity_id=user.id,
        performed_by=current_user.id,
    )

    logger.info(f"User restored id={user_id}")

    return UserOut.model_validate(user)


@router.get("/{user_id}/audit-logs", response_model=list[UserAuditOut])
async def get_user_audit_logs(
    user_id: int,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    changed_by: Optional[int] = Query(None),
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
    db: AsyncSession = Depends(get_db_session),
):
    logger.info(f"Fetching audit logs for user id={user_id}")

    query = select(UserAuditLog).where(UserAuditLog.user_id == user_id)

    # filter by date range
    if start_date:
        query = query.where(
            UserAuditLog.changed_at >= datetime.combine(start_date, time.min)
        )

    if end_date:
        query = query.where(
            UserAuditLog.changed_at <= datetime.combine(end_date, time.max)
        )

    # filter by who made changes
    if changed_by:
        query = query.where(UserAuditLog.changed_by == changed_by)

    result = await db.execute(query.order_by(UserAuditLog.changed_at.desc()))

    logs = result.scalars().all()

    return logs


@router.get("/{user_id}/audit-logs-grouped")
async def get_grouped_audit_logs(
    user_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
):
    result = await db.execute(
        select(UserAuditLog)
        .where(UserAuditLog.user_id == user_id)
        .order_by(UserAuditLog.changed_at.desc())
    )

    logs = result.scalars().all()

    grouped = {}

    for log in logs:
        gid = log.change_group_id

        if gid not in grouped:
            grouped[gid] = {
                "group_id": gid,
                "changed_by": log.changed_by,
                "changed_at": log.changed_at,
                "changes": [],
            }

        grouped[gid]["changes"].append(
            {"field": log.field_name, "old": log.old_value, "new": log.new_value}
        )

    return list(grouped.values())
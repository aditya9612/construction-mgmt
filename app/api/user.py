from datetime import date, datetime, time
import secrets
from typing import Optional
from pydantic import ValidationError
from sqlalchemy import String, and_, or_, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.dependencies import get_current_active_user, require_roles
from app.core.security import get_password_hash
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import ROLES, ActivityLog, User, UserAuditLog, UserRole
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.user import UserAuditOut, UserOut, UserCreatePayload, UserUpdatePayload
from app.utils.helpers import AppError, ConflictError, NotFoundError
from app.core.logger import logger
from fastapi import Depends, File, Request, UploadFile , Query , APIRouter
import os , shutil
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

async def log_user_changes(db, user, data: dict, current_user_id: int, change_group_id: str):
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
    if not payload.email and not payload.mobile_number:
        raise AppError(422, "Provide either email or mobile_number")

    if payload.email and not payload.password:
        raise AppError(422, "Password required when creating with email")

    try:
        role = UserRole(payload.role)
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
        mobile_val = (
            payload.mobile_number.strip() if payload.mobile_number else None
        )

        email = payload.email.strip().lower() if payload.email else None

        # ------------------------
        # EMAIL FLOW (UNCHANGED + FIXED)
        # ------------------------
        if email:
            # EMAIL UNIQUE CHECK
            existing_email = await db.scalar(
                select(User).where(User.email == email)
            )
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
                hashed_password=get_password_hash(payload.password),
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
            existing_mobile = await db.scalar(
                select(User).where(User.mobile == mobile)
            )
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


@router.get("", response_model=PaginatedResponse[UserOut])
async def list_users(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: Optional[str] = None,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
):
    query = select(User).where(User.is_deleted == False)
    count_query = select(func.count()).select_from(User).where(User.is_deleted == False)

    if search:
        logger.info(f"User search query={search}")
        like = f"%{search}%"
        cond = or_(
            User.email.ilike(like), User.full_name.ilike(like), User.mobile.cast(String).ilike(like)
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

@router.get("/activity-logs")
async def get_activity_logs(
    entity_id: Optional[int] = Query(None),
    action: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
):
    # base query
    query = select(ActivityLog)

    # filter only USER logs
    query = query.where(ActivityLog.entity == "USER")

    # JOIN with User to apply soft-delete filter
    query = query.join(User, User.id == ActivityLog.entity_id, isouter=True)
    query = query.where((User.is_deleted == False) | (User.id == None))

    # optional filters
    if entity_id:
        query = query.where(ActivityLog.entity_id == entity_id)

    if action:
        query = query.where(ActivityLog.action == action)

    result = await db.execute(query.order_by(ActivityLog.created_at.desc()))
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    user = await db.scalar(select(User).where(User.id == user_id, User.is_deleted == False))

    if user is None:
        logger.warning(f"User not found id={user_id}")
        raise NotFoundError("User not found")

    return UserOut.model_validate(user)


@router.put("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdatePayload = Depends(),
    profile_image: UploadFile = File(None),
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
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

        try:
            UserUpdatePayload(**data)
        except ValidationError as e:
            raise AppError(422, str(e))

        old_image_path = user.profile_image
        # IMAGE
        if profile_image:
            data["profile_image"] = await validate_and_save_image(profile_image)

        # MOBILE
        if "mobile_number" in data:
            mobile_val = data.pop("mobile_number")
            data["mobile"] = mobile_val

            if mobile_val:
                existing = await db.scalar(
                    select(User).where(
                        and_(User.mobile == mobile_val, User.id != user_id)
                    )
                )
                if existing:
                    raise ConflictError("Mobile already registered")

        # EMAIL (FIX ADDED)
        if "email" in data and data["email"]:
            data["email"] = data["email"].strip().lower()

            existing = await db.scalar(
                select(User).where(
                    and_(User.email == data["email"], User.id != user_id)
                )
            )
            if existing:
                raise ConflictError("Email already registered")

        # ROLE
        if "role" in data:
            try:
                data["role"] = UserRole(data["role"])
            except ValueError:
                raise AppError(422, f"Invalid role. Use one of: {ROLES}")

        change_group_id = str(uuid4())
        # AUDIT
        await log_user_changes(db, user, data, current_user.id, change_group_id)

        # APPLY
        for key, value in data.items():
            if hasattr(user, key):
                setattr(user, key, value)

        user.updated_by = current_user.id
        await db.flush()

        await log_activity(
            db,
            action="UPDATE_USER",
            entity="USER",
            entity_id=user.id,
            performed_by=current_user.id,
            details={"fields_updated": list(data.keys())},
        )

        # CLEAN OLD IMAGE
        if profile_image and old_image_path and old_image_path.startswith("/uploads"):
            old_path = os.path.join(".", old_image_path.lstrip("/"))
            if os.path.exists(old_path):
                os.remove(old_path)

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
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
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
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
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

    result = await db.execute(
        query.order_by(UserAuditLog.changed_at.desc())
    )

    logs = result.scalars().all()

    return logs

@router.get("/{user_id}/audit-logs-grouped")
async def get_grouped_audit_logs(
    user_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
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
                "changes": []
            }

        grouped[gid]["changes"].append({
            "field": log.field_name,
            "old": log.old_value,
            "new": log.new_value
        })

    return list(grouped.values())

import os
import shutil

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.settings import CompanySettings, UserSettings
from app.schemas.settings import CompanySettingsOut, CompanySettingsUpdate, UserSettingsUpdate, UserSettingsOut

from app.models.user import User
from app.core.dependencies import get_current_active_user
from app.schemas.user import UserOut, UserUpdatePayload


router = APIRouter(prefix="/settings", tags=["Settings"])


#  GET SETTINGS
@router.get("", response_model=UserSettingsOut)
async def get_settings(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )

    if not obj:
        obj = UserSettings(user_id=current_user.id)
        db.add(obj)
        await db.commit()
        await db.refresh(obj)

    return obj


#  UPDATE SETTINGS
@router.put("", response_model=UserSettingsOut)
async def update_settings(
    payload: UserSettingsUpdate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.scalar(
        select(UserSettings).where(UserSettings.user_id == current_user.id)
    )

    if not obj:
        obj = UserSettings(user_id=current_user.id)
        db.add(obj)

    for k, v in payload.dict(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)

    return obj


@router.put("/profile", response_model=UserOut)
async def update_profile(
    payload: UserUpdatePayload,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    data = payload.dict(exclude_unset=True)

    #  SECURITY: block sensitive fields
    data.pop("role", None)
    data.pop("mobile_number", None)
    data.pop("email", None)

    #  Update allowed fields only
    for k, v in data.items():
        setattr(current_user, k, v)

    await db.commit()
    await db.refresh(current_user)

    return current_user


@router.get("/profile", response_model=UserOut)
async def get_profile(
    current_user: User = Depends(get_current_active_user),
):
    return current_user


UPLOAD_DIR = "uploads/company"

os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)


# =========================================================
# GET SETTINGS
# =========================================================

@router.get(
    "/company",
    response_model=CompanySettingsOut
)
async def get_company_settings(
    db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(
        select(CompanySettings)
    )

    settings = result.scalars().first()

    if not settings:

        settings = CompanySettings()

        db.add(settings)

        await db.commit()

        await db.refresh(settings)

    return settings


# =========================================================
# UPDATE SETTINGS
# =========================================================

@router.put(
    "/company",
    response_model=CompanySettingsOut
)
async def update_company_settings(
    payload: CompanySettingsUpdate,
    db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(
        select(CompanySettings)
    )

    settings = result.scalars().first()

    if not settings:

        settings = CompanySettings()

        db.add(settings)

    update_data = payload.model_dump(
        exclude_unset=True
    )

    for key, value in update_data.items():
        setattr(settings, key, value)

    await db.commit()

    await db.refresh(settings)

    return settings


# =========================================================
# UPLOAD LOGO
# =========================================================

@router.post("/upload-logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
):

    result = await db.execute(
        select(CompanySettings)
    )

    settings = result.scalars().first()

    if not settings:

        settings = CompanySettings()

        db.add(settings)

    file_path = (
        f"{UPLOAD_DIR}/logo_{file.filename}"
    )

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(
            file.file,
            buffer
        )

    settings.company_logo = file_path

    await db.commit()

    return {
        "message": "Logo uploaded successfully",
        "file_path": file_path
    }


# =========================================================
# UPLOAD SIGNATURE
# =========================================================

@router.post("/upload-signature")
async def upload_signature(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session)
):
    import os
    import shutil

    # =====================================================
    # ALLOWED IMAGE TYPES ONLY
    # =====================================================
    allowed_extensions = {".png", ".jpg", ".jpeg"}

    ext = os.path.splitext(file.filename)[1].lower()

    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail="Only PNG, JPG, and JPEG files are allowed for signature images."
        )

    # =====================================================
    # GET OR CREATE COMPANY SETTINGS
    # =====================================================
    result = await db.execute(
        select(CompanySettings)
    )

    settings = result.scalars().first()

    if not settings:
        settings = CompanySettings()
        db.add(settings)

    # =====================================================
    # ENSURE UPLOAD DIRECTORY EXISTS
    # =====================================================
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # =====================================================
    # SANITIZE FILE NAME
    # =====================================================
    safe_filename = os.path.basename(file.filename)

    # =====================================================
    # SAVE FILE
    # =====================================================
    file_path = f"{UPLOAD_DIR}/signature_{safe_filename}"

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # =====================================================
    # SAVE PATH IN DATABASE
    # =====================================================
    settings.signature_image = file_path

    await db.commit()

    return {
        "message": "Signature uploaded successfully",
        "file_path": file_path
    }
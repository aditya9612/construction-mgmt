import os
from fastapi import APIRouter, Depends, File, Form , HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db_session
from app.models.settings import CompanySettings, UserSettings
from app.schemas.settings import CompanySettingsOut, CompanySettingsUpdate, UserSettingsUpdate, UserSettingsOut
from app.models.user import User
from app.core.dependencies import get_current_active_user
from app.schemas.user import UserOut
from datetime import date
from typing import Optional
from app.core.validators import (
    validate_and_save_image,
    validate_full_name,
    validate_pan,
    validate_aadhaar,
    validate_joining_date,
)

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

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(obj, k, v)

    await db.commit()
    await db.refresh(obj)

    return obj


PROFILE_UPLOAD_DIR = "uploads/profile"

os.makedirs(
    PROFILE_UPLOAD_DIR,
    exist_ok=True
)

@router.put(
    "/profile",
    response_model=UserOut
)
async def update_profile(

    # =========================================
    # FORM FIELDS
    # =========================================

    full_name: Optional[str] = Form(None),

    address: Optional[str] = Form(None),

    pan_number: Optional[str] = Form(None),

    aadhaar_number: Optional[str] = Form(None),

    designation: Optional[str] = Form(None),

    joining_date: Optional[date] = Form(None),


    # =========================================
    # FILE
    # =========================================

    profile_image: UploadFile = File(None),

    current_user: User = Depends(get_current_active_user),

    db: AsyncSession = Depends(get_db_session),
):

    # =========================================
    # NORMALIZE EMPTY STRINGS
    # =========================================

    full_name = full_name or None
    address = address or None
    pan_number = pan_number or None
    aadhaar_number = aadhaar_number or None
    designation = designation or None
    # =========================================
    # VALIDATE + UPDATE TEXT FIELDS
    # =========================================

    if full_name is not None:
        current_user.full_name = validate_full_name(
            full_name
        )

    if address is not None:
        current_user.address = address.strip()

    if pan_number is not None:
        current_user.pan_number = validate_pan(
            pan_number
        )

    if aadhaar_number is not None:
        current_user.aadhaar_number = validate_aadhaar(
            aadhaar_number
        )

    if designation is not None:
        current_user.designation = designation.strip()

    if joining_date is not None:
        current_user.joining_date = validate_joining_date(
            joining_date
        )

    # =========================================
    # PROFILE IMAGE VALIDATION + UPLOAD
    # =========================================

    if profile_image:

        file_path = await validate_and_save_image(
            file=profile_image,
            upload_dir=PROFILE_UPLOAD_DIR,
            prefix="profile"
        )

        current_user.profile_image = file_path

    # =========================================
    # SAVE
    # =========================================

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

    file_path = await validate_and_save_image(
        file=file,
        upload_dir=UPLOAD_DIR,
        prefix="logo"
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

    result = await db.execute(
        select(CompanySettings)
    )

    settings = result.scalars().first()

    if not settings:
        settings = CompanySettings()
        db.add(settings)

    file_path = await validate_and_save_image(
        file=file,
        upload_dir=UPLOAD_DIR,
        prefix="signature"
    )

    settings.signature_image = file_path

    await db.commit()

    return {
        "message": "Signature uploaded successfully",
        "file_path": file_path
    }
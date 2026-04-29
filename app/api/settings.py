from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models.settings import UserSettings
from app.schemas.settings import UserSettingsUpdate, UserSettingsOut

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
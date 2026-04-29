from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.models import alert as m
from app.schemas import alert as s

from app.models.user import User
from app.core.dependencies import get_current_active_user

from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/alerts", tags=["Alerts"])


# ===================== CREATE ALERT =====================
@router.post("", response_model=s.AlertOut)
async def create_alert(
    payload: s.AlertCreate,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = m.Alert(**payload.model_dump())

    db.add(obj)
    await db.commit()
    await db.refresh(obj)

    return obj


# ===================== GET USER ALERTS =====================
@router.get("", response_model=list[s.AlertOut])
async def get_alerts(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.execute(
        select(m.Alert)
        .where(m.Alert.user_id == current_user.id)
        .order_by(m.Alert.created_at.desc())
    )

    return result.scalars().all()


# ===================== MARK AS READ =====================
@router.put("/{id}/read")
async def mark_alert_read(
    id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.Alert, id)

    if not obj:
        raise NotFoundError("Alert not found")

    if obj.user_id != current_user.id:
        raise NotFoundError("Not allowed")

    obj.status = "read"
    await db.commit()

    return {"message": "marked as read"}


# ===================== DELETE ALERT =====================
@router.delete("/{id}")
async def delete_alert(
    id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(m.Alert, id)

    if not obj:
        raise NotFoundError("Alert not found")

    if obj.user_id != current_user.id:
        raise NotFoundError("Not allowed")

    await db.delete(obj)
    await db.commit()

    return {"message": "deleted"}
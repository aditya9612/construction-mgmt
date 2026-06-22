from fastapi import APIRouter, Depends
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime

from app.db.session import get_db_session
from app.models.notification import Notification
from app.schemas.notification import NotificationOut, PMNotificationOut
from app.models.user import User
from app.core.dependencies import get_current_active_user
from app.utils.helpers import NotFoundError

router = APIRouter(prefix="/notifications", tags=["Notifications"])


# ===================== GET USER NOTIFICATIONS =====================
@router.get("", response_model=list[NotificationOut])
async def get_notifications(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    limit: int = 50,
    offset: int = 0,
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    return result.scalars().all()


# ===================== GET PM NOTIFICATIONS =====================
@router.get("/project-manager", response_model=list[PMNotificationOut])
async def get_pm_notifications(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    limit: int = 50,
):
    result = await db.execute(
        select(Notification)
        .where(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )

    notifications = result.scalars().all()
    pm_notifications = []

    for n in notifications:
        # standard fallback if type not properly categorized
        n_type = (
            n.type
            if n.type in ["Delay", "Budget", "Material", "Safety", "QC"]
            else "General"
        )

        pm_notifications.append(
            PMNotificationOut(
                id=n.id,
                title=n.title,
                message=n.message,
                type=n_type,
                project_name=None,  # Extract from link or title if possible, or null
                created_at=n.created_at,
                is_read=n.is_read,
            )
        )

    return pm_notifications


# ===================== GET UNREAD COUNT =====================
@router.get("/unread-count")
async def get_unread_count(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    result = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)
    )

    return {"unread_count": result or 0}


# ===================== MARK ALL AS READ =====================
@router.put("/read-all")
async def mark_all_read(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read == False)
        .values(is_read=True, read_at=datetime.utcnow())
    )
    await db.commit()

    return {"message": "All notifications marked as read"}


# ===================== MARK AS READ =====================
@router.put("/{id}/read")
async def mark_notification_read(
    id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Notification, id)

    if not obj:
        raise NotFoundError("Notification not found")

    if obj.user_id != current_user.id:
        raise NotFoundError("Not allowed")

    if not obj.is_read:
        obj.is_read = True
        obj.read_at = datetime.utcnow()
        await db.commit()

    return {"message": "marked as read"}


# ===================== DELETE NOTIFICATION =====================
@router.delete("/{id}")
async def delete_notification(
    id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
):
    obj = await db.get(Notification, id)

    if not obj:
        raise NotFoundError("Notification not found")

    if obj.user_id != current_user.id:
        raise NotFoundError("Not allowed")

    await db.delete(obj)
    await db.commit()

    return {"message": "deleted"}

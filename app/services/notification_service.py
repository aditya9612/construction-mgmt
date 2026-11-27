from typing import List, Optional, Set
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification
from app.models.user import User, UserRole

async def create_notification(
    db: AsyncSession,
    user_id: int,
    title: str,
    message: str,
    type: str = "info",
    link: str = None
) -> Notification:
    """
    Utility function to create and save a notification to the database.
    Does NOT call db.commit() to allow caller to handle transactions.
    """
    notification = Notification(
        user_id=user_id,
        title=title[:255],
        message=message[:500],
        type=type,
        link=link
    )
    db.add(notification)
    return notification


async def notify_company_stakeholders(
    db: AsyncSession,
    company_id: int,
    title: str,
    message: str,
    type: str = "info",
    link: Optional[str] = None,
    project_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    exclude_actor: bool = False,
) -> List[Notification]:
    """
    Area B: Dispatches notifications to all relevant active Admin and Project Manager
    stakeholders within the tenant company.
    - Excludes inactive and soft-deleted users.
    - Strictly isolates by company_id (users of other companies never receive it).
    - Prevents duplicate notification records within the same batch.
    - If exclude_actor is True, ensures the requesting maker (actor_id) NEVER receives
      the notification, preventing authorization/OTP leakage to the maker.
    """
    admin_roles = [
        UserRole.ADMIN.value,
        "admin",
        "Admin",
        UserRole.PROJECT_MANAGER.value,
        "project_manager",
        "ProjectManager",
    ]

    stmt = select(User).where(
        and_(
            User.company_id == company_id,
            User.is_active == True,
            User.is_deleted == False,
            User.role.in_(admin_roles),
        )
    )
    users = (await db.execute(stmt)).scalars().all()

    recipient_ids: Set[int] = set()
    for u in users:
        recipient_ids.add(u.id)

    # If exclude_actor is True, strictly prevent the maker from receiving the authorization notification
    if exclude_actor and actor_id:
        recipient_ids.discard(actor_id)
    elif actor_id:
        # Otherwise, ensure actor receives a record if they belong to this company and are active
        actor = await db.get(User, actor_id)
        if actor and actor.company_id == company_id and actor.is_active and not actor.is_deleted:
            recipient_ids.add(actor_id)

    created_notifications = []
    for uid in recipient_ids:
        notif = Notification(
            user_id=uid,
            title=title[:255],
            message=message[:500],
            type=type,
            link=link,
        )
        db.add(notif)
        created_notifications.append(notif)

    return created_notifications

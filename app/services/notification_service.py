from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification

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
        title=title,
        message=message,
        type=type,
        link=link
    )
    db.add(notification)
    return notification

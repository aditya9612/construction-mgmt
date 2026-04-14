from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import project as m
from app.models.user import User, UserRole
from app.utils.helpers import PermissionDeniedError


async def assert_project_access(
    db: AsyncSession,
    *,
    project_id: int,
    current_user: User,
):
    if current_user.role in (UserRole.ADMIN, UserRole.PROJECT_MANAGER):
        return

    exists = await db.scalar(
        select(func.count())
        .select_from(m.ProjectMember)
        .where(
            m.ProjectMember.project_id == project_id,
            m.ProjectMember.user_id == current_user.id,
        )
    )

    if not exists:
        raise PermissionDeniedError("User is not part of this project")
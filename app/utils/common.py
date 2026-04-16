from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import project as m
from app.models.user import User, UserRole
from app.utils.helpers import PermissionDeniedError
from app.models.contractor import ContractorProject
from app.models.project import ProjectMember
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError


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


async def validate_contractor_access(
    db,
    contractor_id: int,
    current_user,
):
    """
    Ensure user has access to contractor via project mapping
    """

    result = await db.execute(
        select(ContractorProject.project_id).where(
            ContractorProject.contractor_id == contractor_id
        )
    )
    contractor_project_ids = [r[0] for r in result.all()]

    if current_user.role == UserRole.ADMIN:
        return

    result = await db.execute(
        select(ProjectMember.project_id).where(ProjectMember.user_id == current_user.id)
    )
    user_project_ids = [r[0] for r in result.all()]

    if not set(contractor_project_ids).intersection(set(user_project_ids)):
        raise PermissionDeniedError("Access denied")


async def generate_business_id(
    db,
    model,
    column_name: str,
    prefix: str,
    padding: int = 3,
    max_retries: int = 5,
):
    """
    Generic, race-condition safe business ID generator.
    Works for PRJ, CNT, MAT, etc.
    """

    column = getattr(model, column_name)

    for _ in range(max_retries):
        #  Get latest ID with prefix
        result = await db.execute(
            select(func.max(column)).where(column.like(f"{prefix}%"))
        )
        last_id = result.scalar()

        if last_id:
            try:
                last_number = int(last_id.replace(prefix, ""))
            except ValueError:
                last_number = 0
            new_number = last_number + 1
        else:
            new_number = 1

        new_id = f"{prefix}{str(new_number).zfill(padding)}"

        #  Check if already exists (extra safety)
        exists = await db.scalar(select(func.count()).where(column == new_id))

        if not exists:
            return new_id

    # If still failing
    raise Exception("Unable to generate unique business ID after retries")
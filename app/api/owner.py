from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_roles
from app.db.session import get_db_session
from app.models.owner import Owner
from app.models.user import UserRole

from app.schemas.owner import OwnerCreate, OwnerUpdate, OwnerOut
from app.core.errors import NotFoundError


router = APIRouter(
    prefix="/owners",
    tags=["owners"],
)


# -------------------------
# CREATE OWNER
# -------------------------
@router.post("", response_model=OwnerOut)
async def create_owner(
    payload: OwnerCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_roles([UserRole.ADMIN])),
):
    obj = Owner(**payload.model_dump())

    try:
        db.add(obj)
        await db.flush()
        await db.commit()
        await db.refresh(obj)
    except Exception:
        await db.rollback()
        raise

    return OwnerOut.model_validate(obj)


# -------------------------
# LIST OWNERS
# -------------------------
@router.get("", response_model=list[OwnerOut])
async def list_owners(
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
):
    query = select(Owner)

    if search:
        query = query.where(Owner.owner_name.ilike(f"%{search}%"))

    result = await db.execute(query)
    return result.scalars().all()


# -------------------------
# GET OWNER
# -------------------------
@router.get("/{owner_id}", response_model=OwnerOut)
async def get_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_roles([UserRole.ADMIN, UserRole.PROJECT_MANAGER])),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    return OwnerOut.model_validate(obj)


# -------------------------
# UPDATE OWNER
# -------------------------
@router.put("/{owner_id}", response_model=OwnerOut)
async def update_owner(
    owner_id: int,
    payload: OwnerUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_roles([UserRole.ADMIN])),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    data = payload.model_dump(exclude_unset=True)

    for k, v in data.items():
        setattr(obj, k, v)

    try:
        await db.flush()
        await db.commit()
        await db.refresh(obj)
    except Exception:
        await db.rollback()
        raise

    return OwnerOut.model_validate(obj)


# -------------------------
# DELETE OWNER
# -------------------------
@router.delete("/{owner_id}", status_code=204)
async def delete_owner(
    owner_id: int,
    db: AsyncSession = Depends(get_db_session),
    current_user=Depends(require_roles([UserRole.ADMIN])),
):
    obj = await db.scalar(select(Owner).where(Owner.id == owner_id))

    if not obj:
        raise NotFoundError("Owner not found")

    try:
        await db.delete(obj)
        await db.flush()
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return None
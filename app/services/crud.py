from typing import Any, Dict, Optional, Type

from sqlalchemy.ext.asyncio import AsyncSession


async def create_obj(session: AsyncSession, model: Type, data: Dict[str, Any]) -> Any:
    obj = model(**data)
    session.add(obj)
    await session.flush()  # populate PKs
    return obj


async def get_obj_by_id(session: AsyncSession, model: Type, obj_id: int) -> Optional[Any]:
    # `session.get` is the fastest path for primary keys.
    return await session.get(model, obj_id)


async def update_obj(session: AsyncSession, db_obj: Any, data: Dict[str, Any]) -> Any:
    for field, value in data.items():
        setattr(db_obj, field, value)
    await session.flush()
    return db_obj


async def delete_obj(session: AsyncSession, db_obj: Any) -> None:
    await session.delete(db_obj)


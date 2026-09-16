import pytest
from unittest.mock import AsyncMock
from app.utils.common import generate_business_id


from sqlalchemy import Column, String
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class DummyModel(Base):
    __tablename__ = "dummy_model"
    code = Column(String(50), primary_key=True)


class MockResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


@pytest.mark.asyncio
async def test_empty_table():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult([]))
    db.scalar = AsyncMock(return_value=0)

    val = await generate_business_id(db, DummyModel, "code", "WO", padding=3)
    assert val == "WO001"


@pytest.mark.asyncio
async def test_sequential_ids():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult(["WO001", "WO002", "WO003"]))
    db.scalar = AsyncMock(return_value=0)

    val = await generate_business_id(db, DummyModel, "code", "WO", padding=3)
    assert val == "WO004"


@pytest.mark.asyncio
async def test_mixed_ids_non_numeric_suffix():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult(["WO001", "WO005", "WO2-b40e297d"]))
    db.scalar = AsyncMock(return_value=0)

    val = await generate_business_id(db, DummyModel, "code", "WO", padding=3)
    assert val == "WO006"


@pytest.mark.asyncio
async def test_one_collision():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult(["WO001", "WO002"]))
    # First candidate WO003 collides (count = 1), next candidate WO004 does not (count = 0)
    db.scalar = AsyncMock(side_effect=[1, 0])

    val = await generate_business_id(db, DummyModel, "code", "WO", padding=3)
    assert val == "WO004"


@pytest.mark.asyncio
async def test_consecutive_collisions():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MockResult(["WO001"]))
    # Collides twice (WO002 -> 1, WO003 -> 1), then WO004 succeeds (0)
    db.scalar = AsyncMock(side_effect=[1, 1, 0])

    val = await generate_business_id(db, DummyModel, "code", "WO", padding=3)
    assert val == "WO004"

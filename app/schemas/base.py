from typing import Generic, List, TypeVar

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PaginationMeta(BaseSchema):
    total: int
    limit: int
    offset: int


T = TypeVar("T")


class PaginatedResponse(BaseSchema, Generic[T]):
    items: List[T]
    meta: PaginationMeta



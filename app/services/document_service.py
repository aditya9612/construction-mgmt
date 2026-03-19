from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.models.document import Document
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.schemas.document import DocumentCreate, DocumentOut, DocumentUpdate
from app.utils.helpers import NotFoundError


class DocumentService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:documents"

    async def create_document(self, payload: DocumentCreate) -> DocumentOut:
        obj = Document(**payload.model_dump(exclude_unset=True))
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return DocumentOut.model_validate(obj)

    async def get_document(self, document_id: int) -> DocumentOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:documents:get:{version}:{document_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return DocumentOut.model_validate(cached)

        obj = await self.db.scalar(select(Document).where(Document.id == document_id))
        if obj is None:
            raise NotFoundError("Document not found")

        out = DocumentOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_documents(
        self,
        limit: int = 20,
        offset: int = 0,
        search: Optional[str] = None,
        document_type: Optional[str] = None,
        project_id: Optional[int] = None,
    ) -> PaginatedResponse[DocumentOut]:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:documents:list:{version}:{limit}:{offset}:{search}:{document_type}:{project_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[DocumentOut].model_validate(cached)

        query = select(Document)
        count_query = select(func.count()).select_from(Document)

        if search:
            like = f"%{search}%"
            query = query.where(Document.title.ilike(like))
            count_query = count_query.where(Document.title.ilike(like))

        if document_type:
            query = query.where(Document.document_type == document_type)
            count_query = count_query.where(Document.document_type == document_type)

        if project_id is not None:
            query = query.where(Document.project_id == project_id)
            count_query = count_query.where(Document.project_id == project_id)

        query = query.order_by(Document.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [DocumentOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}
        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[DocumentOut].model_validate(result)

    async def update_document(self, document_id: int, payload: DocumentUpdate) -> DocumentOut:
        obj = await self.db.scalar(select(Document).where(Document.id == document_id))
        if obj is None:
            raise NotFoundError("Document not found")

        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(obj, k, v)

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return DocumentOut.model_validate(obj)

    async def delete_document(self, document_id: int) -> None:
        obj = await self.db.scalar(select(Document).where(Document.id == document_id))
        if obj is None:
            raise NotFoundError("Document not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)


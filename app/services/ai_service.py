from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.models.ai_prediction import AIPrediction
from app.schemas.ai_prediction import AIPredictRequest, AIPredictionOut, AIPredictResponse
from app.schemas.base import PaginationMeta, PaginatedResponse
from app.utils.helpers import NotFoundError


class AIService:
    def __init__(self, db: AsyncSession, redis):
        self.db = db
        self.redis = redis
        self.version_key = "cache_version:ai_predictions"

    def _placeholder_predict(self, module_name: str, prompt: Optional[str]) -> Dict[str, Any]:
        # Placeholder: return deterministic-ish output so callers can wire UI immediately.
        prompt_len = len(prompt or "")
        return {
            "module_name": module_name,
            "estimated_delay_days": max(0, (prompt_len % 7)),
            "estimated_cost_impact": round((prompt_len % 100) * 1.25, 2),
            "confidence": round(0.6 + ((prompt_len % 10) / 100), 2),
            "notes": "Placeholder prediction. Integrate ML model for production.",
        }

    async def predict_and_store(
        self, payload: AIPredictRequest, created_by_user_id: Optional[int]
    ) -> AIPredictResponse:
        prediction = self._placeholder_predict(payload.module_name, payload.prompt)
        obj = AIPrediction(
            module_name=payload.module_name,
            prompt=payload.prompt,
            prediction=prediction,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return AIPredictResponse(module_name=obj.module_name, prediction=obj.prediction)

    async def create_prediction_record(self, module_name: str, prompt: Optional[str], prediction: Dict[str, Any], created_by_user_id: Optional[int]) -> AIPredictionOut:
        obj = AIPrediction(
            module_name=module_name,
            prompt=prompt,
            prediction=prediction,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return AIPredictionOut.model_validate(obj)

    async def get_prediction(self, prediction_id: int) -> AIPredictionOut:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:ai:get:{version}:{prediction_id}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return AIPredictionOut.model_validate(cached)

        obj = await self.db.scalar(select(AIPrediction).where(AIPrediction.id == prediction_id))
        if obj is None:
            raise NotFoundError("Prediction not found")

        out = AIPredictionOut.model_validate(obj)
        await cache_set_json(self.redis, cache_key, out.model_dump())
        return out

    async def list_predictions(
        self,
        limit: int = 20,
        offset: int = 0,
        module_name: Optional[str] = None,
        search: Optional[str] = None,
    ) -> PaginatedResponse[AIPredictionOut]:
        version = await get_cache_version(self.redis, self.version_key)
        cache_key = f"cache:ai:list:{version}:{limit}:{offset}:{module_name}:{search}"
        cached = await cache_get_json(self.redis, cache_key)
        if cached is not None:
            return PaginatedResponse[AIPredictionOut].model_validate(cached)

        query = select(AIPrediction)
        count_query = select(func.count()).select_from(AIPrediction)

        if module_name:
            query = query.where(AIPrediction.module_name == module_name)
            count_query = count_query.where(AIPrediction.module_name == module_name)

        if search:
            like = f"%{search}%"
            query = query.where(AIPrediction.module_name.ilike(like))
            count_query = count_query.where(AIPrediction.module_name.ilike(like))

        query = query.order_by(AIPrediction.id.desc()).limit(limit).offset(offset)

        total = await self.db.scalar(count_query)
        rows = (await self.db.execute(query)).scalars().all()

        items = [AIPredictionOut.model_validate(r).model_dump() for r in rows]
        meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
        result = {"items": items, "meta": meta.model_dump()}
        await cache_set_json(self.redis, cache_key, result)
        return PaginatedResponse[AIPredictionOut].model_validate(result)

    async def update_prediction(
        self, prediction_id: int, payload: Dict[str, Any], updated_by_user_id: Optional[int]
    ) -> AIPredictionOut:
        obj = await self.db.scalar(select(AIPrediction).where(AIPrediction.id == prediction_id))
        if obj is None:
            raise NotFoundError("Prediction not found")

        module_name = payload.get("module_name")
        if module_name is not None:
            obj.module_name = module_name
        if "prompt" in payload:
            obj.prompt = payload.get("prompt")
        if "prediction" in payload and payload.get("prediction") is not None:
            obj.prediction = payload["prediction"]

        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)
        return AIPredictionOut.model_validate(obj)

    async def delete_prediction(self, prediction_id: int) -> None:
        obj = await self.db.scalar(select(AIPrediction).where(AIPrediction.id == prediction_id))
        if obj is None:
            raise NotFoundError("Prediction not found")

        await self.db.delete(obj)
        await self.db.flush()
        await bump_cache_version(self.redis, self.version_key)


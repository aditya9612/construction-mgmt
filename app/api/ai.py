from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.ai_prediction import AIPrediction
from app.models.user import User, UserRole
from app.schemas.ai_prediction import AIPredictRequest, AIPredictResponse, AIPredictionOut
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.utils.helpers import NotFoundError


router = APIRouter(prefix="/ai", tags=["ai"], dependencies=[default_rate_limiter_dependency()])

VERSION_KEY = "cache_version:ai_predictions"


def _placeholder_predict(module_name: str, prompt: Optional[str]) -> Dict[str, Any]:
    prompt_len = len(prompt or "")
    return {
        "module_name": module_name,
        "estimated_delay_days": max(0, (prompt_len % 7)),
        "estimated_cost_impact": round((prompt_len % 100) * 1.25, 2),
        "confidence": round(0.6 + ((prompt_len % 10) / 100), 2),
        "notes": "Placeholder prediction. Integrate ML model for production.",
    }


@router.post("/predict", response_model=AIPredictResponse)
async def predict(
    payload: AIPredictRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    prediction = _placeholder_predict(payload.module_name, payload.prompt)
    obj = AIPrediction(
        module_name=payload.module_name,
        prompt=payload.prompt,
        prediction=prediction,
        created_by_user_id=current_user.id,
    )
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return AIPredictResponse(module_name=obj.module_name, prediction=obj.prediction)


@router.get("", response_model=PaginatedResponse[AIPredictionOut])
async def list_predictions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    module_name: Optional[str] = None,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:ai:list:{version}:{limit}:{offset}:{module_name}:{search}"
    cached = await cache_get_json(redis, cache_key)
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

    total = await db.scalar(count_query)
    rows = (await db.execute(query)).scalars().all()

    items = [AIPredictionOut.model_validate(r).model_dump() for r in rows]
    meta = PaginationMeta(total=int(total or 0), limit=limit, offset=offset)
    result = {"items": items, "meta": meta.model_dump()}
    await cache_set_json(redis, cache_key, result)
    return PaginatedResponse[AIPredictionOut].model_validate(result)


@router.get("/{prediction_id}", response_model=AIPredictionOut)
async def get_prediction(
    prediction_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    version = await get_cache_version(redis, VERSION_KEY)
    cache_key = f"cache:ai:get:{version}:{prediction_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return AIPredictionOut.model_validate(cached)

    obj = await db.scalar(select(AIPrediction).where(AIPrediction.id == prediction_id))
    if obj is None:
        raise NotFoundError("Prediction not found")

    out = AIPredictionOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{prediction_id}", response_model=AIPredictionOut)
async def update_prediction(
    prediction_id: int,
    payload: Dict[str, Any],
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(AIPrediction).where(AIPrediction.id == prediction_id))
    if obj is None:
        raise NotFoundError("Prediction not found")

    module_name = payload.get("module_name")
    if module_name is not None:
        obj.module_name = module_name
    if "prompt" in payload:
        obj.prompt = payload.get("prompt")
    if "prediction" in payload and payload.get("prediction") is not None:
        obj.prediction = payload["prediction"]

    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return AIPredictionOut.model_validate(obj)


@router.delete("/{prediction_id}", status_code=204)
async def delete_prediction(
    prediction_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    obj = await db.scalar(select(AIPrediction).where(AIPrediction.id == prediction_id))
    if obj is None:
        raise NotFoundError("Prediction not found")

    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, VERSION_KEY)
    return None

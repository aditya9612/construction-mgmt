from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import bump_cache_version, cache_get_json, cache_set_json, get_cache_version
from app.core.dependencies import get_current_active_user, get_request_redis, require_permission, require_feature
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.ai_prediction import AIPrediction
from app.models.company import Company
from app.models.user import User
from app.schemas.ai_prediction import (
    AIPredictRequest,
    AIPredictResponse,
    AIPredictionOut,
    AIPredictionUpdate,
)
from app.schemas.base import PaginatedResponse, PaginationMeta
from app.utils.helpers import NotFoundError


router = APIRouter(
    prefix="/ai",
    tags=["ai"],
    dependencies=[
        default_rate_limiter_dependency(),
        Depends(require_feature("ai_features", "AI Predictions")),
    ],
)


def _get_version_key(company_scope: Any) -> str:
    return f"cache_version:ai_predictions:{company_scope}"


def _check_tenant_access(current_user: User) -> bool:
    is_sa = getattr(current_user, "is_super_admin", False) is True
    if not is_sa and current_user.company_id is None:
        raise HTTPException(status_code=403, detail="Company context required")
    return is_sa


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
    current_user: User = Depends(require_permission("ai.create")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        target_company_id = current_user.company_id
    else:
        target_id = payload.company_id if payload.company_id is not None else current_user.company_id
        if target_id is None:
            raise HTTPException(
                status_code=400,
                detail="Super Admin must provide active company context or target company_id to create AI predictions",
            )
        target_company = await db.get(Company, target_id)
        if not target_company:
            raise NotFoundError("Company not found")
        target_company_id = target_id

    prediction = _placeholder_predict(payload.module_name, payload.prompt)
    obj = AIPrediction(
        module_name=payload.module_name,
        prompt=payload.prompt,
        prediction=prediction,
        created_by_user_id=current_user.id,
        company_id=target_company_id,
    )
    db.add(obj)
    await db.flush()
    await bump_cache_version(redis, _get_version_key(target_company_id))
    if is_sa:
        await bump_cache_version(redis, _get_version_key("sa"))
    return AIPredictResponse(module_name=obj.module_name, prediction=obj.prediction)


@router.get("", response_model=PaginatedResponse[AIPredictionOut])
async def list_predictions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    module_name: Optional[str] = None,
    search: Optional[str] = None,
    company_id: Optional[int] = Query(None, description="Filter by company ID (Super Admin only)"),
    current_user: User = Depends(require_permission("ai.view")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    is_sa = _check_tenant_access(current_user)
    if not is_sa:
        effective_company_id = current_user.company_id
        company_scope = current_user.company_id
    else:
        if company_id is not None:
            comp = await db.get(Company, company_id)
            if not comp:
                raise NotFoundError("Company not found")
            effective_company_id = company_id
            company_scope = f"sa:{company_id}"
        else:
            effective_company_id = None
            company_scope = "sa"

    version = await get_cache_version(redis, _get_version_key(company_scope))
    cache_key = f"cache:ai:list:{company_scope}:{version}:{limit}:{offset}:{module_name}:{search}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return PaginatedResponse[AIPredictionOut].model_validate(cached)

    query = select(AIPrediction)
    count_query = select(func.count()).select_from(AIPrediction)

    if effective_company_id is not None:
        query = query.where(AIPrediction.company_id == effective_company_id)
        count_query = count_query.where(AIPrediction.company_id == effective_company_id)

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
    current_user: User = Depends(require_permission("ai.view")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    is_sa = _check_tenant_access(current_user)
    company_scope = current_user.company_id if not is_sa else "sa"
    version = await get_cache_version(redis, _get_version_key(company_scope))
    cache_key = f"cache:ai:get:{company_scope}:{version}:{prediction_id}"
    cached = await cache_get_json(redis, cache_key)
    if cached is not None:
        return AIPredictionOut.model_validate(cached)

    query = select(AIPrediction).where(AIPrediction.id == prediction_id)
    if not is_sa:
        query = query.where(AIPrediction.company_id == current_user.company_id)

    obj = await db.scalar(query)
    if obj is None:
        raise NotFoundError("Prediction not found")

    out = AIPredictionOut.model_validate(obj)
    await cache_set_json(redis, cache_key, out.model_dump())
    return out


@router.put("/{prediction_id}", response_model=AIPredictionOut)
async def update_prediction(
    prediction_id: int,
    payload: AIPredictionUpdate,
    current_user: User = Depends(require_permission("ai.edit")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    is_sa = _check_tenant_access(current_user)
    query = select(AIPrediction).where(AIPrediction.id == prediction_id)
    if not is_sa:
        query = query.where(AIPrediction.company_id == current_user.company_id)

    obj = await db.scalar(query)
    if obj is None:
        raise NotFoundError("Prediction not found")

    if payload.module_name is not None:
        obj.module_name = payload.module_name
    if payload.prompt is not None:
        obj.prompt = payload.prompt
    if payload.prediction is not None:
        obj.prediction = payload.prediction

    await db.flush()
    await bump_cache_version(redis, _get_version_key(obj.company_id))
    await bump_cache_version(redis, _get_version_key("sa"))
    return AIPredictionOut.model_validate(obj)


@router.delete("/{prediction_id}", status_code=204)
async def delete_prediction(
    prediction_id: int,
    current_user: User = Depends(require_permission("ai.delete")),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    is_sa = _check_tenant_access(current_user)
    query = select(AIPrediction).where(AIPrediction.id == prediction_id)
    if not is_sa:
        query = query.where(AIPrediction.company_id == current_user.company_id)

    obj = await db.scalar(query)
    if obj is None:
        raise NotFoundError("Prediction not found")

    comp_id = obj.company_id
    await db.delete(obj)
    await db.flush()
    await bump_cache_version(redis, _get_version_key(comp_id))
    await bump_cache_version(redis, _get_version_key("sa"))
    return None

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_active_user, get_request_redis, require_roles
from app.db.session import get_db_session
from app.middlewares.rate_limiter import default_rate_limiter_dependency
from app.models.user import User, UserRole
from app.schemas.ai_prediction import AIPredictRequest, AIPredictResponse, AIPredictionOut
from app.schemas.base import PaginatedResponse
from app.services.ai_service import AIService


router = APIRouter(dependencies=[default_rate_limiter_dependency()])


@router.post("/predict", response_model=AIPredictResponse)
async def predict(
    payload: AIPredictRequest,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = AIService(db, redis)
    return await service.predict_and_store(payload, created_by_user_id=current_user.id)


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
    service = AIService(db, redis)
    return await service.list_predictions(limit=limit, offset=offset, module_name=module_name, search=search)


@router.get("/{prediction_id}", response_model=AIPredictionOut)
async def get_prediction(
    prediction_id: int,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = AIService(db, redis)
    return await service.get_prediction(prediction_id)


@router.put("/{prediction_id}", response_model=AIPredictionOut)
async def update_prediction(
    prediction_id: int,
    payload: Dict[str, Any],
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = AIService(db, redis)
    return await service.update_prediction(
        prediction_id=prediction_id,
        payload=payload,
        updated_by_user_id=current_user.id,
    )


@router.delete("/{prediction_id}", status_code=204)
async def delete_prediction(
    prediction_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db_session),
    redis=Depends(get_request_redis),
):
    service = AIService(db, redis)
    await service.delete_prediction(prediction_id)
    return None


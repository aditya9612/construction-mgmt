from fastapi import APIRouter, Request, Response
from sqlalchemy import text
from app.core.db import AsyncSessionLocal
import asyncio

router = APIRouter(tags=["health"])

@router.get("/health/live")
async def health_live():
    return {"status": "ok"}

@router.get("/health/ready")
async def health_ready(request: Request, response: Response):
    db_status = "ok"
    redis_status = "ok"
    status_code = 200
    overall_status = "ok"

    # Check Database
    try:
        async with AsyncSessionLocal() as session:
            await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=1.5)
    except Exception as e:
        db_status = "unavailable"
        overall_status = "error"
        status_code = 503

    # Check Redis
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        redis_status = "unavailable"
        if overall_status == "ok":
            overall_status = "degraded"
    else:
        try:
            await asyncio.wait_for(redis.ping(), timeout=1.0)
        except Exception:
            redis_status = "unavailable"
            if overall_status == "ok":
                overall_status = "degraded"

    response.status_code = status_code
    return {
        "status": overall_status,
        "database": db_status,
        "redis": redis_status
    }

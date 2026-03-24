import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.ai import router as ai_router
from app.api.auth import router as auth_router
from app.api.document import router as document_router
from app.api.equipment import router as equipment_router
from app.api.labour import router as labour_router
from app.api.material import router as material_router
from app.api.project import router as project_router
from app.api.boq import router as boq_router
from app.api.user import router as user_router
from app.api.owner import router as owner_router
from app.cache.redis import create_redis_client
from app.core.config import settings
from app.middlewares.rate_limiter import init_rate_limiter
from app.core.errors import AppError   # ✅ UPDATED IMPORT
from app.utils.logger import configure_logging

from fastapi.security import HTTPBearer

app = FastAPI()

bearer_scheme = HTTPBearer()
logger = logging.getLogger("construction-mgmt")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(settings.APP_ENV)

    # Redis is shared across caching + rate limiting.
    app.state.redis = await create_redis_client(settings.REDIS_URL)
    try:
        await init_rate_limiter(app, app.state.redis)
    except (RedisConnectionError, OSError) as exc:
        # Allow local dev startup even when Redis is not running yet.
        logger.warning("Redis unavailable; continuing without Redis rate limiting. error=%s", exc)
        app.state.rate_limiter = None
        app.state.redis = None

    logger.info(
        "Application ready. Open in browser: http://localhost:%s  (docs: http://localhost:%s/docs)",
        settings.APP_PORT,
        settings.APP_PORT,
    )
    if settings.APP_HOST == "0.0.0.0":
        logger.info(
            "NOTE: Use http://localhost:%s (NOT http://0.0.0.0:%s) - 0.0.0.0 is not routable in browsers.",
            settings.APP_PORT,
            settings.APP_PORT,
        )
    try:
        yield
    finally:
        redis = getattr(app.state, "redis", None)
        if redis is not None:
            await redis.close()


def create_app() -> FastAPI:
    application = FastAPI(title=settings.APP_NAME, version="0.1.0", lifespan=lifespan)

    @application.middleware("http")
    async def log_requests(request: Request, call_next):
        logger.info("request.start method=%s path=%s", request.method, request.url.path)
        try:
            response = await call_next(request)
            logger.info(
                "request.end method=%s path=%s status=%s",
                request.method,
                request.url.path,
                response.status_code,
            )
            return response
        except Exception:
            logger.exception("request.error method=%s path=%s", request.method, request.url.path)
            raise

    # ✅ UPDATED EXCEPTION HANDLER
    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "success": False,
                "message": exc.message,
                "error_code": exc.error_code,
                "details": exc.details,
            },
        )

    @application.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("database.error")
        return JSONResponse(status_code=500, content={"detail": "Database error"})

    @application.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    api_router = APIRouter()
    api_router.include_router(auth_router)
    api_router.include_router(user_router)
    api_router.include_router(project_router)
    api_router.include_router(boq_router)
    api_router.include_router(material_router)
    api_router.include_router(labour_router)
    api_router.include_router(equipment_router)
    api_router.include_router(document_router)
    api_router.include_router(ai_router)
    api_router.include_router(owner_router)

    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
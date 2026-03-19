import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.v1.api import api_router
from app.cache.redis import create_redis_client
from app.core.config import settings
from app.middlewares.rate_limiter import init_rate_limiter
from app.utils.helpers import AppError
from app.utils.logger import configure_logging


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

    @application.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @application.exception_handler(SQLAlchemyError)
    async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
        logger.exception("database.error")
        return JSONResponse(status_code=500, content={"detail": "Database error"})

    @application.get("/health", tags=["health"])
    async def health():
        return {"status": "ok"}

    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()


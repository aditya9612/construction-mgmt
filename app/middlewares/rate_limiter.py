import inspect

from fastapi import Depends, FastAPI, Request
from pyrate_limiter import Duration, Limiter, Rate
from pyrate_limiter.buckets.redis_bucket import RedisBucket
from app.core.config import settings
from app.core.errors import AppError


async def init_rate_limiter(app: FastAPI, redis) -> None:
    """
    Initialize a Redis-backed `pyrate_limiter.Limiter` and store it on `app.state`.
    """
    rates = [Rate(settings.RATE_LIMIT_TIMES, Duration.SECOND * settings.RATE_LIMIT_SECONDS)]
    bucket_key = "ratelimit:fastapi"

    bucket = RedisBucket.init(rates=rates, redis=redis, bucket_key=bucket_key)
    if inspect.isawaitable(bucket):
        bucket = await bucket

    app.state.rate_limiter = Limiter(bucket)


def default_rate_limiter_dependency():
    """
    FastAPI dependency that applies a default request limit per `client_ip + path`.
    """
    async def _rate_limit(request: Request):
        limiter = getattr(request.app.state, "rate_limiter", None)
        if limiter is None:
            fallback_rates = [Rate(settings.RATE_LIMIT_TIMES, Duration.SECOND * settings.RATE_LIMIT_SECONDS)]
            limiter = Limiter(fallback_rates)

        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        elif request.client:
            ip = request.client.host
        else:
            ip = "127.0.0.1"

        key = f"{ip}:{request.url.path}"
        allowed = await limiter.try_acquire_async(name=key, blocking=False)

        if not allowed:
            raise AppError(
                status_code=429,
                message="Too Many Requests",
                error_code="RATE_LIMIT",
            )

    return Depends(_rate_limit)
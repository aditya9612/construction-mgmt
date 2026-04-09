import json
from typing import Any, Optional

import orjson
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from app.core.logger import logger
from app.core.config import settings


async def create_redis_client(redis_url: str) -> Redis:
    # decode_responses=False to keep bytes storage for speed.
    return Redis.from_url(redis_url, decode_responses=False)


def _dumps(value: Any) -> bytes:
    # Prefer orjson for speed; fall back to std json if needed.
    return orjson.dumps(value, default=str)


def _loads(raw: bytes) -> Any:
    try:
        return orjson.loads(raw)
    except Exception:
        logger.warning("Cache decode failed, returning None")
        return None


async def cache_get_json(redis: Redis, key: str) -> Optional[Any]:
    if redis is None:
        return None

    raw = await redis.get(key)

    if raw is None:
        return None

    data = _loads(raw)

    if data is None:
        logger.warning(f"Invalid cache data key={key}")

    return data


async def cache_set_json(redis: Redis, key: str, value: Any, ttl_seconds: int = None) -> None:
    if redis is None:
        return
    ttl = ttl_seconds if ttl_seconds is not None else settings.REDIS_CACHE_TTL_SECONDS
    await redis.set(key, _dumps(value), ex=ttl)


async def get_cache_version(redis: Redis, version_key: str, default: int = 1) -> int:
    if redis is None:
        return default
    raw = await redis.get(version_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


async def bump_cache_version(redis: Redis, version_key: str) -> int:
    if redis is None:
        return 1
    # Atomic increment prevents cache stampedes across concurrent workers.
    return int(await redis.incr(version_key))


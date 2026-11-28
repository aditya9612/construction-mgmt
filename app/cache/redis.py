import json
from typing import Any, Optional

import orjson
from redis.asyncio import Redis
from redis.asyncio.client import Pipeline
from redis.exceptions import RedisError
from app.core.logger import logger
from app.core.config import settings


LUA_ATOMIC_GETDEL = """
local val = redis.call('GET', KEYS[1])
if val then
    redis.call('DEL', KEYS[1])
end
return val
"""


async def create_redis_client(redis_url: str) -> Redis:
    # decode_responses=False to keep bytes storage for speed.
    return Redis.from_url(redis_url, decode_responses=False)


def _dumps(value: Any) -> bytes:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "dict"):
        value = value.dict()
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

    try:
        raw = await redis.get(key)
    except (RedisError, OSError) as exc:
        logger.warning(f"Redis get failed key={key}: {exc}")
        return None

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
    try:
        data = _dumps(value)
        await redis.set(key, data, ex=ttl)
    except (RedisError, OSError) as exc:
        logger.warning(f"Redis set failed key={key}: {exc}")
    except Exception as exc:
        logger.warning(f"Cache serialization failed key={key}: {exc}")


async def get_cache_version(redis: Redis, version_key: str, default: int = 1) -> int:
    if redis is None:
        return default
    try:
        raw = await redis.get(version_key)
    except (RedisError, OSError) as exc:
        logger.warning(f"Redis get version failed key={version_key}: {exc}")
        return default
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
    try:
        return int(await redis.incr(version_key))
    except (RedisError, OSError) as exc:
        logger.warning(f"Redis bump version failed key={version_key}: {exc}")
        return 1



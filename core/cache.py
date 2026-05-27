"""
Cache — Redis-first, in-process dict fallback.

Usage:
    from core.cache import cache

    await cache.get("key")
    await cache.set("key", value, ttl=60)
    await cache.delete("key")
    await cache.clear_prefix("dashboard:")

Set REDIS_URL in .env to enable Redis. Without it, an in-process TTL dict is
used automatically — suitable for single-process dev/desktop mode.
"""

import asyncio
import logging
import os
import time
from typing import Any, Optional

logger = logging.getLogger("origin.cache")

_REDIS_URL = os.getenv("REDIS_URL", "")


class _InProcessCache:
    """Simple TTL dict — no external dependency, no persistence across restarts."""

    def __init__(self):
        self._store: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if expires_at and time.monotonic() > expires_at:
                del self._store[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        async with self._lock:
            expires_at = (time.monotonic() + ttl) if ttl else 0.0
            self._store[key] = (value, expires_at)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear_prefix(self, prefix: str) -> int:
        async with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]
            return len(keys)

    def stats(self) -> dict:
        now = time.monotonic()
        live = sum(1 for _, (_, exp) in self._store.items() if not exp or now <= exp)
        return {"backend": "in-process", "keys": len(self._store), "live_keys": live}


class _RedisCache:
    """Async Redis wrapper (requires `redis[asyncio]` package)."""

    def __init__(self, url: str):
        import redis.asyncio as aioredis  # type: ignore[import]
        self._client = aioredis.from_url(url, decode_responses=False)
        self._url = url

    async def get(self, key: str) -> Optional[Any]:
        import pickle
        raw = await self._client.get(key)
        return pickle.loads(raw) if raw is not None else None

    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        import pickle
        raw = pickle.dumps(value)
        if ttl:
            await self._client.setex(key, ttl, raw)
        else:
            await self._client.set(key, raw)

    async def delete(self, key: str) -> None:
        await self._client.delete(key)

    async def clear_prefix(self, prefix: str) -> int:
        keys = await self._client.keys(f"{prefix}*")
        if keys:
            await self._client.delete(*keys)
        return len(keys)

    def stats(self) -> dict:
        return {"backend": "redis", "url": self._url}


def _build_cache():
    if _REDIS_URL:
        try:
            c = _RedisCache(_REDIS_URL)
            logger.info("Cache: Redis backend at %s", _REDIS_URL)
            return c
        except Exception as e:
            logger.warning("Redis cache init failed (%s) — falling back to in-process cache", e)
    logger.info("Cache: in-process TTL dict (set REDIS_URL to enable Redis)")
    return _InProcessCache()


cache = _build_cache()


def cached(key_template: str, ttl: int = 60):
    """Decorator: cache the result of an async function.

    Example:
        @cached("dashboard:stats", ttl=10)
        async def get_dashboard_stats() -> dict: ...
    """
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            key = key_template.format(*args, **kwargs)
            hit = await cache.get(key)
            if hit is not None:
                return hit
            result = await fn(*args, **kwargs)
            await cache.set(key, result, ttl=ttl)
            return result
        wrapper.__wrapped__ = fn
        return wrapper
    return decorator

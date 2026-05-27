"""Tests for the caching layer (in-process backend)."""

import asyncio
import pytest


@pytest.mark.asyncio
async def test_cache_set_and_get():
    from core.cache import _InProcessCache

    c = _InProcessCache()
    await c.set("key1", {"value": 42})
    result = await c.get("key1")
    assert result == {"value": 42}


@pytest.mark.asyncio
async def test_cache_miss_returns_none():
    from core.cache import _InProcessCache

    c = _InProcessCache()
    result = await c.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cache_ttl_expiry():
    from core.cache import _InProcessCache
    import time

    c = _InProcessCache()
    await c.set("expiring", "value", ttl=1)
    assert await c.get("expiring") == "value"

    # Manually expire by manipulating internal state
    key = "expiring"
    val, _ = c._store[key]
    c._store[key] = (val, time.monotonic() - 1)

    assert await c.get("expiring") is None
    assert "expiring" not in c._store


@pytest.mark.asyncio
async def test_cache_delete():
    from core.cache import _InProcessCache

    c = _InProcessCache()
    await c.set("del_me", 123)
    await c.delete("del_me")
    assert await c.get("del_me") is None


@pytest.mark.asyncio
async def test_cache_clear_prefix():
    from core.cache import _InProcessCache

    c = _InProcessCache()
    await c.set("health:snap", 1)
    await c.set("health:meta", 2)
    await c.set("other:key", 3)

    removed = await c.clear_prefix("health:")
    assert removed == 2
    assert await c.get("health:snap") is None
    assert await c.get("health:meta") is None
    assert await c.get("other:key") == 3


@pytest.mark.asyncio
async def test_cache_stats():
    from core.cache import _InProcessCache

    c = _InProcessCache()
    await c.set("a", 1)
    await c.set("b", 2)
    stats = c.stats()
    assert stats["backend"] == "in-process"
    assert stats["keys"] == 2


@pytest.mark.asyncio
async def test_cached_decorator():
    from core.cache import _InProcessCache, cached
    import core.cache as cache_mod

    # Swap to a fresh in-process cache for isolation
    original = cache_mod.cache
    cache_mod.cache = _InProcessCache()

    call_count = 0

    @cached("test:result", ttl=60)
    async def expensive():
        nonlocal call_count
        call_count += 1
        return {"computed": True}

    r1 = await expensive()
    r2 = await expensive()
    assert r1 == r2 == {"computed": True}
    assert call_count == 1  # second call hit cache

    cache_mod.cache = original

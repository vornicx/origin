"""Tests para el sistema de hooks/plugins."""

import pytest
from skills.plugin_hooks import PluginManager, HookEvent, HookContext


@pytest.mark.asyncio
async def test_register_and_trigger():
    pm = PluginManager()
    calls = []

    async def my_hook(ctx: HookContext):
        calls.append(ctx.event.value)

    pm.register(HookEvent.BEFORE_SKILL, my_hook)
    await pm.trigger(HookEvent.BEFORE_SKILL, HookContext(event=HookEvent.BEFORE_SKILL))

    assert len(calls) == 1
    assert calls[0] == "before_skill"


@pytest.mark.asyncio
async def test_hook_priority():
    pm = PluginManager()
    order = []

    async def first(ctx):
        order.append(1)

    async def second(ctx):
        order.append(2)

    pm.register(HookEvent.BEFORE_SKILL, second, priority=200)
    pm.register(HookEvent.BEFORE_SKILL, first, priority=10)

    await pm.trigger(HookEvent.BEFORE_SKILL, HookContext(event=HookEvent.BEFORE_SKILL))

    assert order == [1, 2]


@pytest.mark.asyncio
async def test_wrap_skill():
    pm = PluginManager()
    calls = []

    async def my_hook(ctx: HookContext):
        calls.append(ctx.event.value)

    pm.register(HookEvent.BEFORE_SKILL, my_hook)

    async def dummy(inputs):
        return {"success": True}

    wrapped = pm.wrap_skill("test", dummy)
    result = await wrapped({"query": "test"})

    assert result["success"] is True
    assert "before_skill" in calls


@pytest.mark.asyncio
async def test_hook_error_doesnt_break():
    pm = PluginManager()

    async def broken(ctx):
        raise ValueError("hook error")

    pm.register(HookEvent.BEFORE_SKILL, broken)

    async def dummy(inputs):
        return {"success": True}

    wrapped = pm.wrap_skill("test", dummy)
    result = await wrapped({"query": "test"})

    assert result["success"] is True

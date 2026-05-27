"""Tests para el SkillScheduler."""

import pytest
from skills.scheduler import SkillScheduler


class FakeExecutor:
    async def execute(self, name, inputs):
        return {"success": True, "result": {"done": True}, "skill": name}


@pytest.mark.asyncio
async def test_scheduler_empty_plan():
    scheduler = SkillScheduler(FakeExecutor())
    result = await scheduler.execute([])
    assert result["success"] is True
    assert result["plan_executed"] == 0


@pytest.mark.asyncio
async def test_scheduler_single_step():
    scheduler = SkillScheduler(FakeExecutor())
    plan = [{"step": 1, "action": "test", "skill": "web_search", "inputs": {"query": "test"}}]
    result = await scheduler.execute(plan)
    assert result["success"] is True
    assert result["plan_executed"] == 1


@pytest.mark.asyncio
async def test_scheduler_parallel_steps():
    scheduler = SkillScheduler(FakeExecutor())
    plan = [
        {"step": 1, "action": "search", "skill": "web_search", "inputs": {"query": "a"}},
        {"step": 2, "action": "calc", "skill": "calculator", "inputs": {"expression": "2+2"}},
    ]
    result = await scheduler.execute(plan)
    assert result["success"] is True
    assert result["plan_executed"] == 2
    assert len(result["skills_executed"]) == 2

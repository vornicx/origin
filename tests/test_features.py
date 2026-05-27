"""Tests para cron, context, personality, subagent, compression."""

import pytest


class TestCronSkill:
    def test_parse_cron(self):
        from skills.cron_skill import _parse_cron

        parsed = _parse_cron("0 * * * *")
        assert parsed is not None
        assert parsed[0] == 0
        assert parsed[1] == -1

    def test_parse_invalid_cron(self):
        from skills.cron_skill import _parse_cron

        assert _parse_cron("invalid") is None
        assert _parse_cron("a b c d e") is None

    def test_matches_cron(self):
        from skills.cron_skill import _parse_cron
        from datetime import datetime

        dt = datetime(2026, 5, 13, 15, 30, 0)
        parsed = _parse_cron("30 15 * * *")
        assert parsed is not None
        assert _check_match(parsed, dt)

    def test_add_and_remove_job(self):
        from skills.cron_skill import CronSkill

        class FakeExec:
            async def execute(self, name, inputs):
                return {"success": True, "result": {}}

        cron = CronSkill(FakeExec())
        job = cron.add_job("test_job", "0 * * * *", "system_info")
        assert job is not None
        assert job.name == "test_job"
        assert cron.remove_job(job.job_id)
        assert not cron.remove_job("nonexistent")

    def test_pause_resume(self):
        from skills.cron_skill import CronSkill

        class FakeExec:
            async def execute(self, name, inputs):
                return {"success": True, "result": {}}

        cron = CronSkill(FakeExec())
        job = cron.add_job("pause_test", "*/5 * * * *", "datetime")
        assert cron.pause_job(job.job_id)
        assert cron._jobs[job.job_id].status.value == "paused"
        assert cron.resume_job(job.job_id)
        assert cron._jobs[job.job_id].status.value == "active"

    def test_list_jobs(self):
        from skills.cron_skill import CronSkill

        class FakeExec:
            async def execute(self, name, inputs):
                return {"success": True, "result": {}}

        cron = CronSkill(FakeExec())
        cron.add_job("j1", "0 9 * * *", "weather")
        cron.add_job("j2", "30 18 * * *", "system_info")
        jobs = cron.list_jobs()
        assert len(jobs) == 2


def _check_match(parsed, dt):
    from skills.cron_skill import _matches_cron

    return _matches_cron(parsed, dt)


class TestContextManager:
    def test_list_empty(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        assert cm.list_contexts() is not None

    def test_set_and_get(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        cm.set_context("test.md", "test content")
        content = cm.get("test.md")
        assert content == "test content"
        cm.delete_context("test.md")
        assert cm.get("test.md") is None

    def test_get_context_returns_list(self):
        from core.context_manager import ContextManager

        cm = ContextManager()
        ctx = cm.get_context()
        assert isinstance(ctx, list)
        for item in ctx:
            assert "role" in item
            assert "content" in item
            assert item["role"] == "system"


class TestPersonalityLearner:
    def test_technical_detection(self):
        from skills.personality_learner import PersonalityLearner
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        pl = PersonalityLearner(mm)
        profile = pl.learn_from_interaction("Tengo un bug en el código Python, la función async no funciona")
        assert profile.communication_style == "technical"
        assert profile.technical_level > 0.5

    def test_casual_detection(self):
        from skills.personality_learner import PersonalityLearner
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        pl = PersonalityLearner(mm)
        profile = pl.learn_from_interaction("Oye, qué tal? una pregunta rápida")
        assert profile.communication_style == "casual"

    def test_increasing_confidence(self):
        from skills.personality_learner import PersonalityLearner
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        pl = PersonalityLearner(mm)
        for i in range(10):
            pl.learn_from_interaction(f"Mensaje de prueba número {i}")
        assert pl.profile.confidence > 0.1

    def test_topic_extraction(self):
        from skills.personality_learner import PersonalityLearner
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        pl = PersonalityLearner(mm)
        pl.learn_from_interaction("El error en el servidor de PostgreSQL")
        assert "debug" in pl.profile.common_topics or "base_datos" in pl.profile.common_topics


class TestSubAgent:
    @pytest.mark.asyncio
    async def test_delegate(self):
        from core.sub_agent import SubAgent
        from core.mind import Mind

        m = Mind()
        sub = SubAgent(m)
        task = sub.delegate("Dime la hora actual")
        assert task.task_id is not None
        assert task.status == "pending"

    @pytest.mark.asyncio
    async def test_gather_executes_tasks(self):
        from core.sub_agent import SubAgent
        from core.mind import Mind

        m = Mind()
        sub = SubAgent(m)
        t1 = sub.delegate("Dime qué día es hoy")
        results = await sub.gather([t1])
        assert len(results) == 1
        assert results[0].status == "completed" or results[0].status == "failed"

    def test_get_all_tasks(self):
        from core.sub_agent import SubAgent
        from core.mind import Mind

        m = Mind()
        sub = SubAgent(m)
        sub.delegate("test1")
        sub.delegate("test2")
        assert len(sub.get_all_tasks()) == 2


class TestContextCompression:
    def test_estimate_tokens(self):
        from skills.context_compression import estimate_tokens

        assert estimate_tokens("hello world") > 0

    @pytest.mark.asyncio
    async def test_no_compression_needed(self):
        from skills.context_compression import ContextCompressor
        from core.llm_router import LLMRouter

        comp = ContextCompressor(LLMRouter())
        conv = [
            {"role": "user", "content": "Hola", "timestamp": "2026-01-01T00:00:00"},
            {"role": "assistant", "content": "Hola!", "timestamp": "2026-01-01T00:00:01"},
        ]
        result = await comp.compress(conv, max_tokens=99999)
        assert len(result) == 2

    def test_detect_segments(self):
        from skills.context_compression import ContextCompressor

        comp = ContextCompressor(None)
        conv = [
            {"role": "user", "content": "Hola", "timestamp": "2026-01-01T00:00:00"},
            {"role": "user", "content": "Cambiando de tema, ahora sobre Python", "timestamp": "2026-01-01T01:00:00"},
        ]
        segments = comp._detect_segments(conv)
        assert len(segments) >= 1

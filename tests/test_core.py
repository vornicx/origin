"""Tests para auth, persistence, agent_workspace, scheduler."""

import pytest


_core_counter = 0


class TestCoreAuth:
    def _unique_user(self):
        import time

        global _core_counter
        _core_counter += 1
        return f"core_{int(time.time())}_{_core_counter}"

    def test_hash_verify_password(self):
        from core.auth import _hash_password, _verify_password

        h = _hash_password("secure_pass_123")
        assert h != "secure_pass_123"
        assert _verify_password("secure_pass_123", h)
        assert not _verify_password("wrong_pass", h)

    def test_token_roundtrip(self):
        from core.auth import _create_token, _verify_token

        token = _create_token("alice")
        username = _verify_token(token)
        assert username == "alice"

    def test_invalid_token_returns_none(self):
        from core.auth import _verify_token

        assert _verify_token("invalid_token_data") is None

    def test_register_authenticate(self):
        from core.auth import register_user, authenticate_user

        username = self._unique_user()
        register_user(username, "SecurePass_test123!")
        result = authenticate_user(username, "SecurePass_test123!")
        assert result["username"] == username
        assert "access_token" in result

    def test_duplicate_user_raises(self):
        from core.auth import register_user

        dup_name = self._unique_user()
        register_user(dup_name, "SecurePass_dup123!")
        with pytest.raises(Exception):
            register_user(dup_name, "AnotherSecurePass456!")


class TestPersistence:
    def test_init(self):
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_user")
        assert pm.user_id == "test_user"

    def test_persist_memory_no_crash_without_db(self):
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_user")
        pm.persist_memory("test content", "test", {"key": "val"})


@pytest.mark.asyncio
class TestAgentWorkspace:
    async def test_create_and_list(self):
        from skills.agent_workspace import AgentWorkspace

        ws = AgentWorkspace()
        assert ws.get_stats()["total"] == 0

        async def dummy():
            return {"done": True}

        task = await ws.run_task("test", dummy(), "test task")
        assert task.task_id in ws._tasks
        assert ws.get_stats()["total"] == 1

    async def test_list_with_filters(self):
        from skills.agent_workspace import AgentWorkspace

        ws = AgentWorkspace()
        tasks = ws.list_tasks(status_filter="running")
        assert isinstance(tasks, list)

    async def test_cancel_nonexistent(self):
        from skills.agent_workspace import AgentWorkspace

        ws = AgentWorkspace()
        assert not ws.cancel_task("nonexistent_id")


@pytest.mark.asyncio
class TestScheduler:
    async def test_scheduler_execute(self):
        from skills.scheduler import SkillScheduler

        class FakeExecutor:
            async def execute(self, name, inputs):
                return {"success": True, "result": {"done": True}, "skill": name}

        sched = SkillScheduler(FakeExecutor())
        r = await sched.execute(
            [
                {"step": 1, "action": "calc", "skill": "calculator", "inputs": {"expression": "2+2"}},
            ]
        )
        assert r["success"]
        assert r["plan_executed"] == 1

    async def test_scheduler_empty(self):
        from skills.scheduler import SkillScheduler

        class FakeExecutor:
            async def execute(self, name, inputs):
                return {"success": True, "result": {}, "skill": name}

        r = await SkillScheduler(FakeExecutor()).execute([])
        assert r["success"]
        assert r["plan_executed"] == 0

    async def test_scheduler_parallel(self):
        from skills.scheduler import SkillScheduler

        class FakeExecutor:
            async def execute(self, name, inputs):
                return {"success": True, "result": {}, "skill": name}

        plan = [
            {"step": 1, "action": "a", "skill": "calc", "inputs": {}},
            {"step": 2, "action": "b", "skill": "datetime", "inputs": {}},
            {"step": 3, "action": "c", "skill": "web_search", "inputs": {}, "depends_on": ["step_1"]},
        ]
        r = await SkillScheduler(FakeExecutor()).execute(plan)
        assert r["success"]
        assert r["plan_executed"] == 3

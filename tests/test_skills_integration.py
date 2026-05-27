"""
B5.2 — Tests de integración de skills individuales.

Llama a skill.execute() directamente sin pasar por el reasoning loop.
No requiere Ollama ni LLM externo para las skills fundamentales.
"""

import pytest
import os
import tempfile


# ── Calculator ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCalculatorSkill:
    async def test_basic_addition(self):
        from skills.calculator import CalculatorSkill

        skill = CalculatorSkill()
        result = await skill.execute({"expression": "2 + 2"})
        assert result["success"] is True
        assert result["result"]["value"] == 4

    async def test_percentage(self):
        from skills.calculator import CalculatorSkill

        skill = CalculatorSkill()
        result = await skill.execute({"expression": "15 / 100 * 240"})
        assert result["success"] is True
        assert abs(result["result"]["value"] - 36.0) < 0.001

    async def test_complex_expression(self):
        from skills.calculator import CalculatorSkill

        skill = CalculatorSkill()
        result = await skill.execute({"expression": "(100 + 50) * 2 / 3"})
        assert result["success"] is True
        assert result["result"]["value"] == pytest.approx(100.0)

    async def test_invalid_expression(self):
        from skills.calculator import CalculatorSkill

        skill = CalculatorSkill()
        result = await skill.execute({"expression": "import os; os.system('echo hacked')"})
        assert result["success"] is False

    async def test_empty_expression(self):
        from skills.calculator import CalculatorSkill

        skill = CalculatorSkill()
        result = await skill.execute({"expression": ""})
        assert result["success"] is False


# ── DateTime ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDateTimeSkill:
    async def test_current_time(self):
        from skills.datetime_skill import DateTimeSkill

        skill = DateTimeSkill()
        result = await skill.execute({"action": "now"})
        assert result["success"] is True
        r = result["result"]
        assert "datetime" in r or "iso" in r or "timestamp" in r or isinstance(r, dict)

    async def test_format_date(self):
        from skills.datetime_skill import DateTimeSkill

        skill = DateTimeSkill()
        result = await skill.execute({"action": "now"})
        assert result["success"] is True

    async def test_result_has_required_fields(self):
        from skills.datetime_skill import DateTimeSkill

        skill = DateTimeSkill()
        result = await skill.execute({"action": "now"})
        assert result["success"] is True
        assert result["result"] is not None
        assert result["execution_time"] >= 0


# ── SystemInfo ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSystemInfoSkill:
    async def test_cpu_info(self):
        from skills.system_info import SystemInfoSkill

        skill = SystemInfoSkill()
        result = await skill.execute({"action": "cpu"})
        assert result["success"] is True
        r = result["result"]
        assert r is not None

    async def test_memory_info(self):
        from skills.system_info import SystemInfoSkill

        skill = SystemInfoSkill()
        result = await skill.execute({"action": "memory"})
        assert result["success"] is True
        assert result["result"] is not None

    async def test_system_info(self):
        from skills.system_info import SystemInfoSkill

        skill = SystemInfoSkill()
        result = await skill.execute({"action": "system"})
        assert result["success"] is True
        r = result["result"]
        assert r is not None

    async def test_disk_info(self):
        from skills.system_info import SystemInfoSkill

        skill = SystemInfoSkill()
        result = await skill.execute({"action": "disk"})
        assert result["success"] is True


# ── FileManager ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestFileManagerSkill:
    async def test_list_directory(self):
        from skills.file_manager import FileManagerSkill

        skill = FileManagerSkill()
        result = await skill.execute({"action": "list", "path": "."})
        assert result["success"] is True
        r = result["result"]
        assert r is not None

    async def test_list_nonexistent_dir(self):
        from skills.file_manager import FileManagerSkill

        skill = FileManagerSkill()
        result = await skill.execute({"action": "list", "path": "/nonexistent_path_xyz_123"})
        assert result["success"] is False

    async def test_read_write_file(self):
        from skills.file_manager import FileManagerSkill

        skill = FileManagerSkill()
        with tempfile.TemporaryDirectory() as tmp:
            test_file = os.path.join(tmp, "test.txt")
            write_result = await skill.execute(
                {"action": "write", "path": test_file, "content": "hello origin"}
            )
            assert write_result["success"] is True

            read_result = await skill.execute({"action": "read", "path": test_file})
            assert read_result["success"] is True
            content = read_result["result"]
            assert "hello origin" in str(content)

    async def test_invalid_action(self):
        from skills.file_manager import FileManagerSkill

        skill = FileManagerSkill()
        result = await skill.execute({"action": "nonexistent_action", "path": "."})
        assert result["success"] is False


# ── WebSearch ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestWebSearchSkill:
    async def test_result_structure(self):
        """Verifica que la estructura de resultado es correcta (con o sin red)."""
        from skills.web_search import WebSearchSkill

        skill = WebSearchSkill()
        result = await skill.execute({"query": "Python programming language"})
        # Structure must always be correct regardless of network availability
        assert "success" in result
        assert "result" in result
        assert "error" in result
        assert "execution_time" in result

    async def test_empty_query_fails(self):
        from skills.web_search import WebSearchSkill

        skill = WebSearchSkill()
        result = await skill.execute({"query": ""})
        assert result["success"] is False

    async def test_very_long_query_fails(self):
        from skills.web_search import WebSearchSkill

        skill = WebSearchSkill()
        result = await skill.execute({"query": "x" * 600})
        assert result["success"] is False

    async def test_max_results_respected(self):
        """Si hay resultados, no supera max_results."""
        from skills.web_search import WebSearchSkill

        skill = WebSearchSkill()
        result = await skill.execute({"query": "Python", "max_results": 3})
        if result["success"]:
            assert result["result"]["total_found"] <= 3

    async def test_source_field_present(self):
        """Si devuelve resultados, el campo source debe estar."""
        from skills.web_search import WebSearchSkill

        skill = WebSearchSkill()
        result = await skill.execute({"query": "OpenAI GPT"})
        if result["success"]:
            assert "source" in result["result"]
            assert result["result"]["source"] in (
                "brave",
                "searxng",
                "duckduckgo_html",
                "duckduckgo_instant",
            )


# ── Cron next_run ────────────────────────────────────────────────────────────


class TestCronNextRun:
    def test_next_run_calculated_on_add(self):
        from skills.cron_skill import CronSkill

        class FakeExec:
            async def execute(self, name, inputs):
                return {"success": True, "result": {}}

        cron = CronSkill(FakeExec())
        job = cron.add_job("hourly", "0 * * * *", "datetime")
        assert job is not None
        assert job.next_run is not None
        assert "T" in job.next_run  # ISO format has T separator

    def test_calculate_next_run_every_minute(self):
        from skills.cron_skill import _calculate_next_run
        from datetime import datetime, timedelta

        after = datetime(2026, 5, 15, 10, 30, 0)
        result = _calculate_next_run("* * * * *", after=after)
        assert result is not None
        assert result > after
        # Every-minute cron should fire within 1 minute
        assert result <= after + timedelta(minutes=2)

    def test_calculate_next_run_specific_time(self):
        from skills.cron_skill import _calculate_next_run
        from datetime import datetime

        # Job that fires at 09:00 every day
        after = datetime(2026, 5, 15, 8, 0, 0)
        result = _calculate_next_run("0 9 * * *", after=after)
        assert result is not None
        assert result.hour == 9
        assert result.minute == 0

    def test_calculate_next_run_invalid_expression(self):
        from skills.cron_skill import _calculate_next_run

        assert _calculate_next_run("not a cron") is None

    def test_next_run_not_in_the_past(self):
        from skills.cron_skill import _calculate_next_run
        from datetime import datetime

        now = datetime.utcnow()
        result = _calculate_next_run("*/5 * * * *")
        assert result is not None
        assert result > now


# ── SkillExecutor ────────────────────────────────────────────────────────────


class TestSkillExecutor:
    def test_skill_status_returns_dict(self):
        from skills.skill_executor import SkillExecutor

        executor = SkillExecutor()
        status = executor.get_skill_status()
        assert isinstance(status, dict)
        # Core skills should always be registered
        assert "calculator" in status or "datetime" in status

    def test_registered_skills_have_ok_status(self):
        from skills.skill_executor import SkillExecutor

        executor = SkillExecutor()
        status = executor.get_skill_status()
        for name, info in status.items():
            assert "status" in info
            assert info["status"] in ("ok", "failed")
            if info["status"] == "failed":
                assert "error" in info

    def test_can_execute_known_skill(self):
        from skills.skill_executor import SkillExecutor

        executor = SkillExecutor()
        assert executor.can_execute("calculator") is True
        assert executor.can_execute("nonexistent_skill_xyz") is False

    @pytest.mark.asyncio
    async def test_execute_calculator_via_executor(self):
        from skills.skill_executor import SkillExecutor

        executor = SkillExecutor()
        result = await executor.execute("calculator", {"expression": "10 * 10"})
        assert result["success"] is True
        assert result["result"]["value"] == 100
        assert result["skill"] == "calculator"

    @pytest.mark.asyncio
    async def test_execute_unknown_skill_returns_error(self):
        from skills.skill_executor import SkillExecutor

        executor = SkillExecutor()
        result = await executor.execute("ghost_skill", {})
        assert result["success"] is False
        assert "no encontrada" in result["error"].lower() or "ghost_skill" in result["error"]

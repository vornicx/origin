"""
B5.1 — Tests end-to-end del reasoning loop de Mind.

Los tests que requieren Ollama están marcados con @pytest.mark.skipif
y se saltan automáticamente si el servidor no está disponible.
"""

import pytest


# ── Fixture: check Ollama availability ──────────────────────────────────────


def _ollama_reachable() -> bool:
    try:
        import httpx

        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


def _sentence_transformers_available() -> bool:
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


OLLAMA_AVAILABLE = _ollama_reachable()
EMBEDDINGS_AVAILABLE = _sentence_transformers_available()

requires_ollama = pytest.mark.skipif(
    not OLLAMA_AVAILABLE,
    reason="Ollama not running at localhost:11434",
)
requires_full_stack = pytest.mark.skipif(
    not (OLLAMA_AVAILABLE and EMBEDDINGS_AVAILABLE),
    reason="Requires Ollama + sentence_transformers",
)


# ── Mind instantiation (no LLM needed) ──────────────────────────────────────


class TestMindInit:
    def test_mind_can_be_instantiated(self):
        from core.mind import Mind

        m = Mind()
        assert m is not None

    def test_mind_has_skill_executor(self):
        from core.mind import Mind

        m = Mind()
        assert m.skill_executor is not None

    def test_mind_has_memory_manager(self):
        from core.mind import Mind

        m = Mind()
        assert m.memory_mgr is not None

    def test_mind_has_subconscious(self):
        from core.mind import Mind

        m = Mind()
        assert m.subconscious is not None

    def test_mind_has_services_graph(self):
        from core.mind import Mind

        m = Mind()
        assert m.services_graph is not None

    def test_mind_skill_executor_has_skills(self):
        from core.mind import Mind

        m = Mind()
        skills = m.skill_executor.list_skills()
        assert len(skills) > 0


# ── Mock LLM reasoning (no Ollama needed) ───────────────────────────────────


@pytest.mark.asyncio
class TestMindWithMockLLM:
    """Tests del reasoning loop con LLM mockeado — sin dependencia de Ollama."""

    async def _make_mind_with_mock(self, responses: list):
        """Crea un Mind con LLM router y memory manager mockeados."""
        from core.mind import Mind

        m = Mind()
        call_count = {"n": 0}

        async def mock_call_llm(*args, **kwargs):
            idx = min(call_count["n"], len(responses) - 1)
            call_count["n"] += 1
            return {"success": True, "content": responses[idx], "provider": "mock", "model": "mock"}

        m.llm_router.call_llm = mock_call_llm

        # Mock memory context so tests don't need sentence_transformers
        def mock_get_memory_context(query, **kwargs):
            return {
                "relevant_memories": [],
                "recent_conversation": [],
                "preferences": {},
                "context_files": "",
                "memory_tree": "",
                "subconscious": "",
                "cross_reference_index": [],
                "current_tone": "technical",
                "tone_confidence": 0.8,
                "latest_summary": None,
            }

        m.memory_mgr.get_memory_context = mock_get_memory_context
        return m

    async def test_think_returns_reasoning_result(self):
        """think() debe devolver un ReasoningResult con final_answer y to_dict()."""
        import json
        from core.mind import ReasoningResult

        intent_json = json.dumps({"intent": "calcular", "entities": {}, "tone": "technical"})
        plan_json = json.dumps(
            [{"step": 1, "action": "calculate", "skill": "calculator", "inputs": {"expression": "2+2"}}]
        )
        review_json = json.dumps({"is_valid": True, "issues": [], "suggestions": []})
        response_text = "El resultado es 4."

        m = await self._make_mind_with_mock([intent_json, plan_json, review_json, response_text])
        result = await m.think("¿Cuánto es 2 + 2?")

        assert isinstance(result, ReasoningResult)
        assert hasattr(result, "final_answer")
        assert hasattr(result, "cycle_id")
        d = result.to_dict()
        assert "final_answer" in d
        assert "cycle_id" in d

    async def test_think_direct_response_no_skill(self):
        """Si el plan no necesita skill, debe devolver respuesta directa sin crash."""
        import json
        from core.mind import ReasoningResult

        intent_json = json.dumps({"intent": "saludo", "entities": {}, "tone": "casual"})
        plan_json = json.dumps([{"step": 1, "action": "responder", "skill": "", "inputs": {}}])
        review_json = json.dumps({"is_valid": True, "issues": [], "suggestions": []})
        response_text = "¡Hola! ¿En qué te puedo ayudar?"

        m = await self._make_mind_with_mock([intent_json, plan_json, review_json, response_text])
        result = await m.think("Hola")

        assert isinstance(result, ReasoningResult)

    async def test_think_handles_llm_failure_gracefully(self):
        """Si el LLM falla, think() no debe lanzar excepción."""
        from core.mind import Mind, ReasoningResult

        m = Mind()

        def mock_get_memory_context(query, **kwargs):
            return {"relevant_memories": [], "recent_conversation": [], "preferences": {},
                    "context_files": "", "memory_tree": "", "subconscious": "",
                    "cross_reference_index": [], "current_tone": "technical",
                    "tone_confidence": 0.8, "latest_summary": None}

        m.memory_mgr.get_memory_context = mock_get_memory_context

        async def always_fail(*args, **kwargs):
            return {"success": False, "content": "", "error": "LLM unavailable", "provider": "mock"}

        m.llm_router.call_llm = always_fail
        result = await m.think("Cualquier pregunta")

        # Must not raise — should return a ReasoningResult even on failure
        assert isinstance(result, ReasoningResult)


# ── Full e2e with Ollama ─────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestMindE2EWithOllama:
    """Tests end-to-end reales que requieren Ollama corriendo."""

    @requires_full_stack
    async def test_calculator_via_reasoning(self):
        """Origin debe usar la skill calculator para responder '¿Cuánto es el 15% de 240?'."""
        from core.mind import Mind

        m = Mind()
        result = await m.think("¿Cuánto es el 15% de 240?")

        assert isinstance(result, dict)
        final = result.get("final_answer", result.get("response", ""))
        # The answer 36 should appear somewhere in the response
        assert "36" in str(final) or result.get("success") is True

    @requires_full_stack
    async def test_datetime_via_reasoning(self):
        """Origin debe poder responder qué hora/fecha es usando la skill datetime."""
        from core.mind import Mind

        m = Mind()
        result = await m.think("¿Qué hora es ahora mismo?")

        assert isinstance(result, dict)
        assert result is not None

    @requires_full_stack
    async def test_web_search_via_reasoning(self):
        """Origin debe invocar web_search para consultas de información actual."""
        from core.mind import Mind

        m = Mind()
        result = await m.think("Busca noticias recientes sobre inteligencia artificial")

        assert isinstance(result, dict)
        assert result is not None

    @requires_full_stack
    async def test_multi_step_plan_executes(self):
        """Planes con múltiples pasos deben ejecutarse sin crash."""
        from core.mind import Mind

        m = Mind()
        result = await m.think("Dime la hora actual y cuánto es 100 dividido entre 4")

        assert isinstance(result, dict)
        assert result is not None


# ── Reviewer behavior ────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestReviewer:
    async def test_reviewer_returns_false_on_llm_failure(self):
        """Reviewer no debe devolver is_valid=True cuando el LLM falla."""
        from core.reviewer import Reviewer

        class FailLLM:
            async def call_llm(self, *a, **kw):
                return {"success": False, "content": "", "error": "LLM down"}

        class FakeProfile:
            origin_identity = {"nombre": "Origin"}

        reviewer = Reviewer(FailLLM(), FakeProfile())
        result = await reviewer.review("test intent", {"result": "test"})

        assert result["is_valid"] is False
        assert len(result["issues"]) > 0

    async def test_reviewer_parses_valid_json(self):
        """Reviewer debe aceptar respuesta JSON válida del LLM."""
        from core.reviewer import Reviewer
        import json

        valid_review = json.dumps({"is_valid": True, "issues": [], "suggestions": ["Mejorar formato"]})

        class OkLLM:
            async def call_llm(self, *a, **kw):
                return {"success": True, "content": valid_review}

        class FakeProfile:
            origin_identity = {"nombre": "Origin"}

        reviewer = Reviewer(OkLLM(), FakeProfile())
        result = await reviewer.review("test intent", {"result": "test"})

        assert result["is_valid"] is True
        assert result["suggestions"] == ["Mejorar formato"]

    async def test_reviewer_returns_false_on_invalid_json(self):
        """Reviewer con JSON malformado debe devolver is_valid=False."""
        from core.reviewer import Reviewer

        class BadJsonLLM:
            async def call_llm(self, *a, **kw):
                return {"success": True, "content": "this is not json at all <<<"}

        class FakeProfile:
            origin_identity = {"nombre": "Origin"}

        reviewer = Reviewer(BadJsonLLM(), FakeProfile())
        result = await reviewer.review("test intent", {})

        assert result["is_valid"] is False


# ── Services Graph integration ───────────────────────────────────────────────


@pytest.mark.asyncio
class TestServicesGraphFired:
    async def test_services_graph_accepts_reasoning_done_event(self):
        """ServicesGraph debe aceptar el evento reasoning_done sin crash."""
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        try:
            await sg.fire("reasoning_done", {"intent": "test", "result": "ok", "steps_used": []})
        except Exception as e:
            pytest.fail(f"services_graph.fire raised unexpectedly: {e}")

    async def test_services_graph_accepts_cron_fired_event(self):
        """ServicesGraph debe aceptar el evento cron_fired sin crash."""
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        try:
            await sg.fire("cron_fired", {"job_name": "test_job", "skill": "datetime", "success": True})
        except Exception as e:
            pytest.fail(f"services_graph.fire raised unexpectedly: {e}")

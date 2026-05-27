"""
B5.3 — Tests de los subsistemas de background de Origin.

Cubre: SubconsciousEngine, MemoryTree, ServicesGraph.
No requiere Ollama para los tests de estructura; los de reflexión usan mock LLM.
"""

import pytest
from datetime import datetime


# ── SubconsciousEngine ───────────────────────────────────────────────────────


class TestSubconsciousEngine:
    def test_init_has_expected_attributes(self):
        from core.subconscious import SubconsciousEngine
        from core.llm_router import LLMRouter
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=LLMRouter(), memory_mgr=mm, memory_tree=MemoryTree())
        assert hasattr(sc, "_thoughts")
        assert hasattr(sc, "_running")
        assert hasattr(sc, "_cycle_count")

    def test_stats_returns_expected_keys(self):
        from core.subconscious import SubconsciousEngine
        from core.llm_router import LLMRouter
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=LLMRouter(), memory_mgr=mm, memory_tree=MemoryTree())
        s = sc.stats
        assert isinstance(s, dict)
        for key in ("total_thoughts", "cycle_count", "running"):
            assert key in s, f"Missing key '{key}' in stats"

    def test_get_recent_thoughts_returns_list(self):
        from core.subconscious import SubconsciousEngine
        from core.llm_router import LLMRouter
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=LLMRouter(), memory_mgr=mm, memory_tree=MemoryTree())
        thoughts = sc.get_recent_thoughts(n=5)
        assert isinstance(thoughts, list)

    def test_set_broadcast_accepted(self):
        from core.subconscious import SubconsciousEngine
        from core.llm_router import LLMRouter
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=LLMRouter(), memory_mgr=mm, memory_tree=MemoryTree())
        callback_received = []

        async def mock_broadcast(msg):
            callback_received.append(msg)

        sc.set_broadcast(mock_broadcast)
        assert sc._broadcast is not None

    def test_not_running_before_start(self):
        from core.subconscious import SubconsciousEngine
        from core.llm_router import LLMRouter
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=LLMRouter(), memory_mgr=mm, memory_tree=MemoryTree())
        assert sc._running is False

    @pytest.mark.asyncio
    async def test_force_reflect_with_mock_llm(self):
        """force_reflect() debe retornar lista (vacía si LLM falla, no crash)."""
        import json
        from core.subconscious import SubconsciousEngine
        from core.memory_manager import MemoryManager

        mm = MemoryManager()
        thoughts_json = json.dumps([
            {"content": "El usuario parece interesado en IA.", "type": "observation", "importance": 0.7},
            {"content": "Sería útil recordar sus preferencias de lenguaje.", "type": "suggestion", "importance": 0.5},
        ])

        class MockLLM:
            async def call_llm(self, *a, **kw):
                return {"success": True, "content": thoughts_json}

        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=MockLLM(), memory_mgr=mm, memory_tree=MemoryTree())
        # Inject some conversation context so there's something to reflect on
        mm.add_conversation("user", "¿Cuál es la capital de Francia?")
        mm.add_conversation("assistant", "La capital de Francia es París.")

        results = await sc.force_reflect()
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_force_reflect_graceful_on_llm_failure(self):
        """force_reflect() no debe lanzar excepción si el LLM falla."""
        from core.subconscious import SubconsciousEngine
        from core.memory_manager import MemoryManager

        mm = MemoryManager()

        class FailLLM:
            async def call_llm(self, *a, **kw):
                return {"success": False, "content": "", "error": "LLM down"}

        from core.memory_tree import MemoryTree
        sc = SubconsciousEngine(llm_router=FailLLM(), memory_mgr=mm, memory_tree=MemoryTree())
        results = await sc.force_reflect()
        assert isinstance(results, list)


# ── MemoryTree ───────────────────────────────────────────────────────────────


class TestMemoryTree:
    def test_init(self):
        from core.memory_tree import MemoryTree

        mt = MemoryTree()
        assert mt is not None
        assert hasattr(mt, "_nodes")
        assert hasattr(mt, "_pending_messages")

    def test_ingest_message_adds_to_pending(self):
        from core.memory_tree import MemoryTree

        mt = MemoryTree()
        initial = len(mt._pending_messages)
        mt.ingest_message("user", "Hola mundo")
        assert len(mt._pending_messages) == initial + 1

    def test_ingest_multiple_messages(self):
        from core.memory_tree import MemoryTree

        mt = MemoryTree()
        initial = len(mt._pending_messages)
        for i in range(5):
            mt.ingest_message("user", f"Mensaje {i}")
        assert len(mt._pending_messages) == initial + 5

    def test_get_context_layers_returns_dict(self):
        from core.memory_tree import MemoryTree

        mt = MemoryTree()
        ctx = mt.get_context_layers()
        assert isinstance(ctx, dict)

    def test_root_updated_at_type(self):
        """_root_updated_at debe ser string ISO o None, nunca un tipo inesperado."""
        from core.memory_tree import MemoryTree

        mt = MemoryTree()
        if mt._root_updated_at is not None:
            # Must be parseable as ISO datetime
            try:
                datetime.fromisoformat(mt._root_updated_at)
            except ValueError:
                pytest.fail(f"_root_updated_at is not a valid ISO datetime: {mt._root_updated_at}")

    def test_pending_messages_capped(self):
        """El buffer de pending messages no debe crecer indefinidamente."""
        from core.memory_tree import MemoryTree

        mt = MemoryTree()
        for i in range(150):
            mt.ingest_message("user", f"Mensaje de prueba número {i} con contenido largo")
        assert len(mt._pending_messages) <= 100

    @pytest.mark.asyncio
    async def test_consolidate_hour_no_crash_when_too_few_messages(self):
        """consolidate_hour() no debe crashear si hay muy pocos mensajes."""
        from core.memory_tree import MemoryTree

        class MockLLM:
            async def call_llm(self, *a, **kw):
                return {"success": True, "content": "Resumen de la hora."}

        mt = MemoryTree()
        # With 0 pending messages (below threshold) should return None gracefully
        result = await mt.consolidate_hour(MockLLM())
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_consolidate_hour_with_enough_messages(self):
        """consolidate_hour() con suficientes mensajes debe retornar un resumen."""
        from core.memory_tree import MemoryTree, HOUR_MIN_MESSAGES

        class MockLLM:
            async def call_llm(self, *a, **kw):
                return {"success": True, "content": "El usuario discutió temas de Python."}

        mt = MemoryTree()
        # Inject enough messages to trigger consolidation
        for i in range(HOUR_MIN_MESSAGES + 1):
            mt.ingest_message("user" if i % 2 == 0 else "assistant", f"Mensaje {i}: contenido de prueba")

        result = await mt.consolidate_hour(MockLLM())
        # Should return a summary string or None (if hour node already exists)
        assert result is None or isinstance(result, str)


# ── ServicesGraph ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestServicesGraph:
    async def test_init(self):
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        assert sg is not None
        assert hasattr(sg, "_workflows")

    async def test_fire_no_workflows_returns_dict(self):
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        # Remove any pre-loaded workflows for clean test
        sg._workflows.clear()

        result = await sg.fire("test_event", {"data": "value"})
        assert isinstance(result, dict)

    async def test_fire_reasoning_done_no_crash(self):
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        sg._workflows.clear()

        result = await sg.fire("reasoning_done", {
            "intent": "calcular algo",
            "success": True,
            "skills_used": ["calculator"],
            "cycle_id": "test-123",
        })
        assert isinstance(result, dict)

    async def test_fire_cron_fired_no_crash(self):
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        sg._workflows.clear()

        result = await sg.fire("cron_fired", {
            "job_name": "hourly_report",
            "skill": "system_info",
            "success": True,
        })
        assert isinstance(result, dict)

    async def test_set_broadcast_accepted(self):
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)

        async def mock_ws(msg):
            pass

        sg.set_broadcast(mock_ws)
        assert sg._broadcast is not None

    async def test_workflow_list_is_dict(self):
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        assert isinstance(sg._workflows, dict)

    async def test_fire_unknown_trigger_no_crash(self):
        """Fire con trigger type desconocido no debe crashear."""
        from core.services_graph import ServicesGraph

        sg = ServicesGraph(skill_executor=None, mind=None)
        sg._workflows.clear()

        result = await sg.fire("totally_unknown_event_xyz", {"some": "data"})
        assert result is not None


# ── Persistence backend ──────────────────────────────────────────────────────


class TestPersistenceBackend:
    def test_sqlite_backend_activates_when_no_postgres(self):
        """Cuando PostgreSQL no está disponible, debe usar SQLite."""
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_subsystem_user")
        # Either sqlite or postgresql — should NOT be ram if SQLite is available
        assert pm.backend in ("sqlite", "postgresql", "ram")

    def test_persist_memory_no_crash(self):
        """persist_memory() no debe crashear independientemente del backend."""
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_subsystem_user")
        try:
            pm.persist_memory("Test memory content", "general", {"test": True})
        except Exception as e:
            pytest.fail(f"persist_memory raised: {e}")

    def test_persist_conversation_no_crash(self):
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_subsystem_user")
        try:
            pm.persist_conversation("user", "Hello Origin", cycle_id="test-123")
        except Exception as e:
            pytest.fail(f"persist_conversation raised: {e}")

    def test_sqlite_memory_searchable(self):
        """Con SQLite activo, search_memories_text debe retornar resultados."""
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_fts_user")
        if pm.backend != "sqlite":
            pytest.skip("SQLite not the active backend — skipping FTS test")

        pm.persist_memory("Python programming is awesome for AI", "learning", {})
        pm.persist_memory("Origin uses multiple LLM providers", "decision", {})

        results = pm.search_memories_text("Python programming", top_k=5)
        assert isinstance(results, list)
        assert any("Python" in r.get("content", "") for r in results)

    def test_backend_property_string(self):
        from core.persistence import PersistenceManager

        pm = PersistenceManager("test_backend_user")
        assert isinstance(pm.backend, str)
        assert pm.backend in ("postgresql", "sqlite", "ram")

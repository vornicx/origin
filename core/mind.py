from typing import Dict, Any, Optional, List, Callable, Awaitable, Set
from datetime import datetime
import asyncio
import uuid
import logging
from enum import Enum

from .config import config_manager
from .profile_manager import ProfileManager
from .memory_manager import MemoryManager
from .llm_router import LLMRouter
from .persistence import PersistenceManager
from .intent import IntentParser
from .planner import Planner
from .reviewer import Reviewer
from .responder import Responder
from skills.skill_executor import SkillExecutor
from skills.auto_skill import AutoSkillEngine
from skills.context_compression import ContextCompressor
from skills.personality_learner import PersonalityLearner
from .context_manager import ContextManager
from .sub_agent import SubAgent
from .memory_tree import MemoryTree
from .subconscious import SubconsciousEngine
from .injection_guard import InjectionGuard
from .token_juice import TokenJuice
from .service_hub import ServiceHub
from .proactive_engine import ProactiveEngine
from .services_graph import ServicesGraph
from .auto_context import AutoContextLoader

logger = logging.getLogger("origin.mind")


class Step(str, Enum):
    INTENT = "intent"
    PLAN = "plan"
    ACT = "act"
    CHECK = "check"
    SAVE = "save"
    ANSWER = "answer"


class ReasoningResult:
    def __init__(self):
        self.cycle_id = str(uuid.uuid4())
        self.timestamp = datetime.now()
        self.steps: Dict[Step, Any] = {}
        self.final_answer = ""
        self.memories_to_save: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle_id": self.cycle_id,
            "timestamp": self.timestamp.isoformat(),
            "steps": {k.value: v for k, v in self.steps.items()},
            "final_answer": self.final_answer,
            "memories_saved": len(self.memories_to_save),
        }


class Mind:
    """
    La Mente de Origin - Reasoning Loop Principal.
    Orquestador delgado que delega en módulos especializados.
    """

    def __init__(self):
        self.profile_mgr = ProfileManager()
        self.memory_mgr = MemoryManager()
        self.llm_router = LLMRouter()
        self.skill_executor = SkillExecutor()
        self.execution_policy = config_manager.policy.get("execution_policy", {})
        self.user_id = "vadim_vornic"

        self.available_skills = {skill["name"]: skill["description"] for skill in self.skill_executor.list_skills()}

        self._inject_llm_router()
        self._wire_monitor_notification()

        # Sub-módulos del reasoning loop
        self.persistence = PersistenceManager(self.user_id)
        self.intent_parser = IntentParser(self.llm_router, self.profile_mgr, self.available_skills)
        self.planner = Planner(self.llm_router, self.profile_mgr, self.available_skills)
        self.reviewer = Reviewer(self.llm_router, self.profile_mgr)
        self.responder = Responder(self.llm_router, self.profile_mgr, self.memory_mgr)

        # AutoSkill: genera skills dinámicamente si faltan
        self.auto_skill = AutoSkillEngine(self.llm_router, self.skill_executor)

        # ContextCompression: comprime conversaciones largas
        self.compressor = ContextCompressor(self.llm_router)

        # PersonalityLearner: aprende del usuario
        self.learner = PersonalityLearner(self.memory_mgr)

        # ContextManager: archivos de contexto persistentes (AGENTS.md style)
        self.context_manager = ContextManager()

        # MemoryTree: summarización jerárquica hora→día→mes→año→root
        self.memory_tree = MemoryTree()

        # SubconsciousEngine: reflexión autónoma en background
        self.subconscious = SubconsciousEngine(self.llm_router, self.memory_mgr, self.memory_tree)

        # InjectionGuard: detección de prompt injection antes del reasoning
        self.injection_guard = InjectionGuard()

        # TokenJuice: compresión de contexto basada en reglas (sin LLM)
        self.token_juice = TokenJuice()

        # ServiceHub: registry centralizado de servicios externos
        self.service_hub = ServiceHub(skill_executor=self.skill_executor)

        # ProactiveEngine: motor de sugerencias proactivas (eventos del SO)
        self.proactive = ProactiveEngine(skill_executor=self.skill_executor, mind=self)

        # ServicesGraph: workflows trigger-action (IFTTT-like)
        self.services_graph = ServicesGraph(skill_executor=self.skill_executor, mind=self)

        # AutoContext: bootstrap de contexto en startup
        self.auto_context = AutoContextLoader(skill_executor=self.skill_executor, mind=self)

        # SubAgent: delegación a sub-agentes
        self.sub_agent = SubAgent(self)

        # Strong refs to background tasks so they don't get GC'd (Python docs warn
        # asyncio.create_task() returns a weak reference internally).
        self._bg_tasks: Set[asyncio.Task] = set()

        # Memory compaction: triggered every N reasoning cycles when memory > threshold
        self._reasoning_cycle_count: int = 0
        self._COMPACTION_CYCLE_INTERVAL: int = 50   # cycles between compaction checks
        self._COMPACTION_MEMORY_THRESHOLD: int = 500  # min memories before pruning old ones

        # SelfImprovement: automejora constante (se inicia desde api/main.py)

    def _inject_llm_router(self):
        for name in ("vision", "camera"):
            skill = self.skill_executor.get(name)
            if skill and hasattr(skill, "set_llm_router"):
                skill.set_llm_router(self.llm_router)

    def _wire_monitor_notification(self):
        """Connect monitor → notification via async task."""
        monitor = self.skill_executor.get("monitor")
        notification = self.skill_executor.get("notification")
        if monitor and notification and hasattr(monitor, "set_notification_callback"):

            async def _notify_wrapper(inputs):
                await notification.execute(inputs)

            monitor.set_notification_callback(_notify_wrapper)

    # ── Persistence helpers (delegados) ──────────────────────────

    def _persist_memory(self, content: str, memory_type_str: str, metadata: dict = None, embedding: list = None):
        self.persistence.persist_memory(content, memory_type_str, metadata, embedding)

    def _persist_conversation(
        self,
        role: str,
        content: str,
        cycle_id: str = None,
        intent: dict = None,
        plan: list = None,
        provider: str = None,
    ):
        self.persistence.persist_conversation(role, content, cycle_id, intent, plan, provider)

    def _persist_audit(self, action: str, action_type: str, details: dict = None, cycle_id: str = None):
        self.persistence.persist_audit(action, action_type, details, cycle_id)

    async def _generate_summary(self) -> None:
        await self.persistence.generate_summary(self.memory_mgr, self.llm_router)

    # ── Steps del reasoning loop ────────────────────────────────

    async def parse_intent(self, user_input: str) -> Dict[str, Any]:
        return await self.intent_parser.parse(user_input)

    async def generate_plan(self, intent: Dict[str, Any]) -> List[Dict[str, Any]]:
        return await self.planner.generate(intent)

    # Skills that need to run sequentially (UI state / pipeline dependencies)
    _SEQUENTIAL_SKILLS = {"ui_automation", "vision", "browser", "os_control"}

    def _plan_is_parallelizable(self, plan: List[Dict[str, Any]]) -> bool:
        """Returns True if ALL steps are independent and can safely run in parallel.

        A step is NOT parallelizable if it:
        - Uses a UI/sequential skill (state depends on previous UI actions)
        - Has inputs with $prev / $step_N pipeline references
        - Has x=0, y=0 vision-to-click auto-injection inputs
        """
        if len(plan) < 2:
            return False
        for step in plan:
            skill = step.get("skill", "")
            if skill in self._SEQUENTIAL_SKILLS:
                return False
            inputs = step.get("inputs", {})
            for val in inputs.values():
                if isinstance(val, str) and (val == "$prev" or val.startswith("$step_")):
                    return False
            if inputs.get("action") == "click" and inputs.get("x") == 0 and inputs.get("y") == 0:
                return False
        return True

    async def execute_plan(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ejecuta plan. Si una skill no existe, la genera con AutoSkill.

        Si el plan tiene 2+ pasos independientes (sin referencias $prev ni skills
        de UI), los ejecuta en paralelo vía SkillScheduler para reducir latencia.
        """
        for step in plan:
            skill_name = step.get("skill", "")
            if skill_name and skill_name not in self.available_skills:
                task_desc = step.get("action", skill_name)
                logger.info(f"AutoSkill generando '{skill_name}' para: {task_desc[:60]}")
                new_skill = await self.auto_skill.generate_skill(task_desc, step.get("inputs"))
                if new_skill:
                    self.available_skills[new_skill] = f"Auto-generated: {task_desc}"
                else:
                    logger.warning(f"No se pudo generar skill para '{skill_name}'")

        if self._plan_is_parallelizable(plan):
            logger.info(f"Plan has {len(plan)} independent steps — running in parallel")
            return await self.skill_executor.execute_plan_parallel(plan)
        return await self.skill_executor.execute_plan(plan)

    async def auto_review(self, original_intent: str, plan_result: Dict[str, Any]) -> Dict[str, Any]:
        return await self.reviewer.review(original_intent, plan_result)

    async def save_learnings(
        self, user_input: str, intent: Dict[str, Any], result: Dict[str, Any], review: Dict[str, Any]
    ) -> List[str]:
        memories_saved = []
        self.memory_mgr.add_conversation("user", user_input)

        if intent.get("confidence", 0) > 0.7:
            meta = {
                "intent_confidence": intent.get("confidence"),
                "task_type": intent.get("task_type"),
                "success": result.get("success"),
            }
            decision = self.memory_mgr.add_memory(
                content=f"Usuario quiso: {intent.get('intent')}. Se ejecutó: {result.get('plan_executed')} pasos.",
                memory_type="decision",
                metadata=meta,
            )
            memories_saved.append(decision.id)
            self._persist_memory(
                content=decision.content,
                memory_type_str="decision",
                metadata=meta,
                embedding=decision.embedding,
            )

        if review.get("is_valid") and result.get("success"):
            pattern = self.memory_mgr.add_memory(
                content=(
                    f"Patrón: Para '{intent.get('intent')}' "
                    f"usar skill(s): {', '.join(intent.get('required_skills', []))}"
                ),
                memory_type="learning",
                metadata={"confidence": "high"},
            )
            memories_saved.append(pattern.id)
            self._persist_memory(
                content=pattern.content,
                memory_type_str="learning",
                metadata={"confidence": "high"},
                embedding=pattern.embedding,
            )

        return memories_saved

    async def generate_answer(
        self, user_input: str, intent: Dict[str, Any], execution_result: Dict[str, Any], memory_context: Dict[str, Any]
    ) -> str:
        return await self.responder.generate(user_input, intent, execution_result, memory_context)

    # ── Reasoning Loop ──────────────────────────────────────────

    def _is_simple_task(self, intent: Dict[str, Any]) -> bool:
        return self.intent_parser.is_simple_task(intent)

    def _is_chitchat(self, intent: Dict[str, Any]) -> bool:
        """Pure conversational query: simple, no skills, high confidence.

        For these we skip PLAN+ACT+CHECK entirely and go straight to ANSWER
        with a stripped-down context. Used to cut latency from 3-5s → <1s.
        """
        return (
            self.intent_parser.is_simple_task(intent)
            and not intent.get("required_skills")
            and intent.get("confidence", 0) >= 0.8
        )

    StepCallback = Callable[[str, int, int, Dict[str, Any]], Awaitable[None]]

    async def think(self, user_input: str, on_step: Optional[StepCallback] = None) -> ReasoningResult:
        result = ReasoningResult()

        async def _notify(step: str, num: int, total: int, data: Dict[str, Any]):
            if on_step:
                try:
                    await on_step(step, num, total, data)
                except Exception as e:
                    logger.warning(f"on_step callback error: {e}")

        self._persist_conversation(role="user", content=user_input, cycle_id=result.cycle_id)
        self.memory_tree.ingest_message("user", user_input)

        # SECURITY: Prompt injection check (fast, regex-only, <1ms)
        guard_result = self.injection_guard.analyze(user_input)
        if guard_result.is_blocked:
            logger.warning(
                f"Input BLOCKED by InjectionGuard: score={guard_result.risk_score:.2f} "
                f"matches={len(guard_result.matches)}"
            )
            result.final_answer = (
                "Tu mensaje fue bloqueado por el sistema de seguridad. "
                "Contiene patrones que se parecen a un intento de manipulacion. "
                "Si es un error, reformula tu mensaje."
            )
            result.steps[Step.INTENT] = {
                "blocked": True,
                "risk_score": guard_result.risk_score,
                "risk_level": guard_result.risk_level,
                "matches": [m.description for m in guard_result.matches],
            }
            self._persist_audit(
                action="injection_blocked",
                action_type="security",
                details=guard_result.to_dict(),
                cycle_id=result.cycle_id,
            )
            return result

        # If medium risk, use sanitized input for the reasoning loop
        if guard_result.sanitized_input and guard_result.risk_level in ("medium", "high"):
            logger.info(f"Input SANITIZED by InjectionGuard: score={guard_result.risk_score:.2f}")
            user_input = guard_result.sanitized_input

        # STEP 1: INTENT (en paralelo con preparación de contexto y compresión)
        await _notify("intent", 1, 6, {"status": "running"})

        async def _prepare_context():
            self.learner.learn_from_interaction(user_input)
            mc = self.memory_mgr.get_memory_context(user_input)
            mc["style_instruction"] = self.learner.get_style_instruction()
            mc["context_files"] = self.context_manager.get_formatted_prompt()
            # Memory Tree: multi-scale context (root identity + month + day + hours)
            tree_context = self.memory_tree.get_formatted_context()
            if tree_context:
                mc["memory_tree"] = tree_context
            # Subconscious: surface relevant thoughts
            sub_context = self.subconscious.get_formatted_context(user_input)
            if sub_context:
                mc["subconscious"] = sub_context
            conv = self.memory_mgr.get_recent_conversation(n=50)
            if conv:
                # TokenJuice: fast rule-based squeeze on conversation turns
                conv = self.token_juice.squeeze_conversation(conv, max_tokens=3000)
                compressed = await self.compressor.compress(conv)
                if len(compressed) < len(conv):
                    mc["compressed"] = True
                    mc["compression_ratio"] = f"{len(compressed)}/{len(conv)}"

            # TokenJuice: squeeze all context sections within budget
            mc = self.token_juice.squeeze_context(mc, total_budget=8000)
            return mc

        intent, memory_context = await asyncio.gather(
            self.parse_intent(user_input),
            _prepare_context(),
        )
        result.steps[Step.INTENT] = intent

        is_chitchat = self._is_chitchat(intent)
        skip_check = self._is_simple_task(intent)
        if is_chitchat:
            total_steps = 2  # INTENT + ANSWER only
            logger.info(f"Chitchat fast-path: INTENT → ANSWER (confidence={intent.get('confidence')})")
        elif skip_check:
            total_steps = 5
            logger.info(f"Fast-path: skipping CHECK (confidence={intent.get('confidence')})")
        else:
            total_steps = 6

        await _notify(
            "intent",
            1,
            total_steps,
            {
                "status": "done",
                "intent": intent.get("intent", ""),
                "task_type": intent.get("task_type", ""),
                "confidence": intent.get("confidence", 0),
                "skills": intent.get("required_skills", []),
                "fast_path": skip_check,
                "chitchat": is_chitchat,
            },
        )

        # Chitchat fast-path: skip PLAN/ACT/CHECK entirely
        if is_chitchat:
            plan = []
            execution_result = {"success": True, "results": [], "skills_executed": [], "plan_executed": 0}
            result.steps[Step.PLAN] = plan
            result.steps[Step.ACT] = execution_result
            # Mark memory_context to use lite mode in responder
            memory_context["_lite_context"] = True
            # Jump to ANSWER (step 2 of 2)
            await _notify("answer", 2, total_steps, {"status": "running"})
            final_answer = await self.generate_answer(user_input, intent, execution_result, memory_context)
            result.final_answer = final_answer
            result.steps[Step.ANSWER] = {"generated": True, "length": len(final_answer), "chitchat": True}
            await _notify("answer", 2, total_steps, {"status": "done", "length": len(final_answer)})

            # Minimal save: only the conversation pair
            self.memory_mgr.add_conversation("user", user_input)
            self._persist_conversation(
                role="assistant",
                content=final_answer,
                cycle_id=result.cycle_id,
                intent=intent,
                plan=plan,
                provider=None,
            )
            self.memory_tree.ingest_message("assistant", final_answer)
            return result

        # STEP 2: PLAN
        await _notify("plan", 2, total_steps, {"status": "running"})
        plan = await self.generate_plan(intent)
        result.steps[Step.PLAN] = plan
        await _notify("plan", 2, total_steps, {"status": "done", "steps_count": len(plan)})

        # STEP 3: ACT
        await _notify("act", 3, total_steps, {"status": "running"})
        execution_result = await self.execute_plan(plan)
        result.steps[Step.ACT] = execution_result

        for step_result in execution_result.get("results", []):
            if isinstance(step_result, dict):
                self._persist_audit(
                    action=step_result.get("skill", "unknown"),
                    action_type="auto_allowed",
                    details=step_result,
                    cycle_id=result.cycle_id,
                )

        await _notify(
            "act",
            3,
            total_steps,
            {
                "status": "done",
                "success": execution_result.get("success", False),
                "skills_executed": execution_result.get("skills_executed", []),
            },
        )

        # STEP 4: CHECK
        if skip_check:
            review = {"is_valid": True, "issues": [], "suggestions": [], "review_status": "skipped_fast_path"}
        else:
            await _notify("check", 4, total_steps, {"status": "running"})
            review = await self.auto_review(user_input, execution_result)
            await _notify("check", 4, total_steps, {"status": "done", "is_valid": review.get("is_valid", False)})
        result.steps[Step.CHECK] = review

        # STEP 5: SAVE
        save_step = 4 if skip_check else 5
        await _notify("save", save_step, total_steps, {"status": "running"})
        memories_saved = await self.save_learnings(user_input, intent, execution_result, review)
        result.steps[Step.SAVE] = {"memories_saved": memories_saved, "count": len(memories_saved)}
        result.memories_to_save = [{"id": mid} for mid in memories_saved]
        await _notify("save", save_step, total_steps, {"status": "done", "count": len(memories_saved)})

        # STEP 6: ANSWER
        answer_step = 5 if skip_check else 6
        await _notify("answer", answer_step, total_steps, {"status": "running"})
        final_answer = await self.generate_answer(user_input, intent, execution_result, memory_context)
        result.final_answer = final_answer
        result.steps[Step.ANSWER] = {"generated": True, "length": len(final_answer), "fast_path": skip_check}
        await _notify("answer", answer_step, total_steps, {"status": "done", "length": len(final_answer)})

        self._persist_conversation(
            role="assistant",
            content=final_answer,
            cycle_id=result.cycle_id,
            intent=intent,
            plan=plan,
            provider=execution_result.get("provider"),
        )
        self.memory_tree.ingest_message("assistant", final_answer)

        # Background tasks: context checkpoint + memory tree consolidation
        self._reasoning_cycle_count += 1

        if self.memory_mgr.should_checkpoint():
            task = asyncio.create_task(self._generate_summary())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
            logger.info("Context checkpoint scheduled (background)")

        # Memory compaction: prune old/duplicate memories periodically
        if (
            self._reasoning_cycle_count % self._COMPACTION_CYCLE_INTERVAL == 0
            and len(self.memory_mgr.memories) > self._COMPACTION_MEMORY_THRESHOLD
        ):
            compact_task = asyncio.create_task(self._compact_memories())
            self._bg_tasks.add(compact_task)
            compact_task.add_done_callback(self._bg_tasks.discard)

        # Memory Tree: consolidate in background (hour→day→month→year→root)
        tree_task = asyncio.create_task(self._consolidate_memory_tree())
        self._bg_tasks.add(tree_task)
        tree_task.add_done_callback(self._bg_tasks.discard)

        # ServicesGraph: fire reasoning_done event for any workflows listening
        try:
            graph_task = asyncio.create_task(
                self.services_graph.fire(
                    "reasoning_done",
                    {
                        "task_type": intent.get("task_type"),
                        "intent": intent.get("intent", "")[:200],
                        "success": execution_result.get("success", False),
                        "skills_used": execution_result.get("skills_executed", []),
                        "cycle_id": result.cycle_id,
                    },
                )
            )
            self._bg_tasks.add(graph_task)
            graph_task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            logger.debug(f"services_graph fire error: {e}")

        return result

    async def _consolidate_memory_tree(self):
        """Background task: run memory tree auto-consolidation."""
        try:
            results = await self.memory_tree.auto_consolidate(self.llm_router)
            consolidated = [k for k, v in results.items() if v]
            if consolidated:
                logger.info(f"Memory tree consolidated: {consolidated}")
        except Exception as e:
            logger.warning(f"Memory tree consolidation error: {e}")

    async def _compact_memories(self):
        """Background task: prune memories older than 60 days to prevent unbounded growth."""
        try:
            before = len(self.memory_mgr.memories)
            removed = self.memory_mgr.clear_old_memories(days=60)
            after = len(self.memory_mgr.memories)
            if removed:
                logger.info(
                    f"Memory compaction: removed {removed} old memories "
                    f"({before} → {after} total)"
                )
            else:
                logger.debug(f"Memory compaction: nothing to prune ({after} memories)")
        except Exception as e:
            logger.warning(f"Memory compaction error: {e}")

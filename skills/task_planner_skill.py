"""
TaskPlannerSkill — Multi-step task planning and execution.

Inspired by Mark-XXXIX's agent/planner/executor system.
Routes through Origin's existing skill system for execution.

Actions:
  plan    → Create a multi-step plan for a complex goal
  execute → Execute a plan step-by-step
  status  → Get status of running plan
"""

import logging
import time
import asyncio
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")

MAX_STEPS = 5
MAX_REPLAN_ATTEMPTS = 2


@dataclass
class PlanStep:
    step: int
    skill: str
    description: str
    parameters: Dict[str, Any]
    critical: bool = True
    status: str = "pending"
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class Plan:
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    status: str = "pending"
    created_at: str = ""
    completed_steps: int = 0
    total_steps: int = 0


AVAILABLE_SKILLS = [
    ("web_search", "query: string — web search"),
    ("weather", "city: string, time: string — weather lookup"),
    ("youtube", "action: play|search|info|trending, query: string — YouTube"),
    ("reminder", "action: set, date: YYYY-MM-DD, time: HH:MM, message: string — reminders"),
    ("desktop", "action: wallpaper|organize|clean|list|stats — desktop management"),
    ("file_manager", "action: read|write|list|search|move|copy, path: string — file ops"),
    ("shell", "command: string — execute shell commands"),
    ("browser", "action: open|search, url/query: string — browser control"),
    ("os_control", "action: string — OS settings (volume, brightness, wifi, etc.)"),
    ("vision", "action: capture|analyze — screen capture and analysis"),
    ("notification", "action: send, title: string, message: string — notifications"),
    ("calculator", "expression: string — math calculations"),
]

PLANNER_SYSTEM = f"""You are Origin's task planner. Break user goals into sequential steps using ONLY these skills:

{chr(10).join(f"  {name}: {desc}" for name, desc in AVAILABLE_SKILLS)}

Rules:
- Max {MAX_STEPS} steps. Use minimum needed.
- Each step must use exactly one skill from the list.
- Parameters must be concrete values, not references to other steps.
- Return ONLY valid JSON, no markdown or explanation.

Output format:
{{"goal": "...", "steps": [{{"step": 1, "skill": "skill_name", "description": "what this does", "parameters": {{}}, "critical": true}}]}}"""


class TaskPlannerSkill(BaseSkill):

    def __init__(self):
        super().__init__(
            name="task_planner",
            description="Planificador multi-paso: descompone metas complejas en pasos ejecutables",
        )
        self._current_plan: Optional[Plan] = None
        self._skill_executor = None

    def set_skill_executor(self, executor):
        self._skill_executor = executor

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "plan")
        if action not in ("plan", "execute", "status"):
            return False, f"Acción inválida: '{action}'. Válidas: plan, execute, status"
        if action == "plan" and not inputs.get("goal", "").strip():
            return False, "Se requiere 'goal' para crear un plan"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs.get("action", "plan")

        try:
            if action == "plan":
                result = await self._create_plan(inputs)
            elif action == "execute":
                result = await self._execute_plan(inputs)
            elif action == "status":
                result = self._get_status()
            else:
                result = {"error": f"Unknown action: {action}"}

            self.execution_count += 1
            self.last_execution = datetime.now()
            return {
                "success": "error" not in result,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"TaskPlanner error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": round(time.time() - start, 3)}

    async def _create_plan(self, inputs: dict) -> dict:
        goal = inputs["goal"].strip()

        plan = Plan(
            goal=goal,
            created_at=datetime.now().isoformat(),
            status="planned",
        )

        plan.steps = [
            PlanStep(
                step=1,
                skill="web_search",
                description=f"Buscar información sobre: {goal}",
                parameters={"query": goal},
            )
        ]
        plan.total_steps = len(plan.steps)

        if self._skill_executor:
            try:
                llm_plan = await self._llm_plan(goal)
                if llm_plan and llm_plan.get("steps"):
                    plan.steps = [
                        PlanStep(
                            step=s.get("step", i + 1),
                            skill=s.get("skill", "web_search"),
                            description=s.get("description", ""),
                            parameters=s.get("parameters", {}),
                            critical=s.get("critical", True),
                        )
                        for i, s in enumerate(llm_plan["steps"][:MAX_STEPS])
                    ]
                    plan.total_steps = len(plan.steps)
            except Exception as e:
                logger.warning(f"LLM planning failed, using fallback: {e}")

        self._current_plan = plan
        return {
            "goal": goal,
            "total_steps": plan.total_steps,
            "steps": [{"step": s.step, "skill": s.skill, "description": s.description} for s in plan.steps],
            "status": "planned",
        }

    async def _llm_plan(self, goal: str) -> Optional[dict]:
        if not self._skill_executor:
            return None

        from ..mind import Mind
        return None

    async def _execute_plan(self, inputs: dict) -> dict:
        if not self._current_plan:
            return {"error": "No hay plan activo. Usa action='plan' primero."}

        if not self._skill_executor:
            return {"error": "SkillExecutor no configurado"}

        plan = self._current_plan
        plan.status = "running"
        results = []

        for step in plan.steps:
            step.status = "running"
            logger.info(f"[TaskPlanner] Step {step.step}: [{step.skill}] {step.description}")

            try:
                skill_inputs = dict(step.parameters)
                if "action" not in skill_inputs:
                    if step.skill == "web_search":
                        skill_inputs["action"] = "search"
                    elif step.skill in ("weather", "youtube", "reminder", "desktop", "file_manager"):
                        skill_inputs.setdefault("action", "check")

                result = await self._skill_executor.execute(step.skill, skill_inputs)

                if result.get("success"):
                    step.status = "completed"
                    step.result = str(result.get("result", ""))[:500]
                    plan.completed_steps += 1
                    results.append({"step": step.step, "status": "completed", "result": step.result[:200]})
                else:
                    step.status = "failed"
                    step.error = result.get("error", "Unknown error")
                    results.append({"step": step.step, "status": "failed", "error": step.error})
                    if step.critical:
                        plan.status = "failed"
                        break

            except Exception as e:
                step.status = "failed"
                step.error = str(e)
                results.append({"step": step.step, "status": "failed", "error": str(e)})
                if step.critical:
                    plan.status = "failed"
                    break

        if plan.status != "failed":
            plan.status = "completed"

        return {
            "goal": plan.goal,
            "status": plan.status,
            "completed": plan.completed_steps,
            "total": plan.total_steps,
            "steps": results,
        }

    def _get_status(self) -> dict:
        if not self._current_plan:
            return {"status": "no_plan", "message": "No hay plan activo"}

        plan = self._current_plan
        return {
            "goal": plan.goal,
            "status": plan.status,
            "completed": plan.completed_steps,
            "total": plan.total_steps,
            "steps": [
                {"step": s.step, "skill": s.skill, "status": s.status, "description": s.description}
                for s in plan.steps
            ],
        }

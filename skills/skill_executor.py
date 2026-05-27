from typing import Dict, Any, List, Optional
import asyncio
import logging
import time

from .base_skill import BaseSkill
from .plugin_hooks import PluginManager
from .scheduler import SkillScheduler
from .web_search import WebSearchSkill
from .datetime_skill import DateTimeSkill
from .system_info import SystemInfoSkill
from .calculator import CalculatorSkill
from .web_scraper import WebScraperSkill
from .shell_skill import ShellSkill
from .external_apis import ExternalAPIsSkill
from .memory_compaction import MemoryCompactionSkill
from .file_manager import FileManagerSkill
from .code_doctor import CodeDoctorSkill
from .vision_skill import VisionSkill
from .ui_automation_skill import UIAutomationSkill
from .notification_skill import NotificationSkill
from .monitor_skill import MonitorSkill
from .voice_skill import VoiceSkill
from .app_integrations import AppIntegrationsSkill
from .wake_word_skill import WakeWordSkill
from .camera_skill import CameraSkill
from .system_tray_skill import SystemTraySkill
from .os_control_skill import OSControlSkill
from .clipboard_skill import ClipboardSkill
from .active_window_skill import ActiveWindowSkill
from .browser_skill import BrowserSkill
from .eventlog_skill import EventLogSkill
from .telegram_skill import TelegramSkill
from .weather_skill import WeatherSkill
from .youtube_skill import YouTubeSkill
from .reminder_skill import ReminderSkill
from .desktop_skill import DesktopSkill
from .task_planner_skill import TaskPlannerSkill

logger = logging.getLogger("origin.skills")


class SkillExecutor:
    """
    Registra y ejecuta skills. Punto central de acceso del Cuerpo de Origin.
    La Mente llama aquí durante el paso ACT del reasoning loop.
    """

    def __init__(self):
        self._registry: Dict[str, BaseSkill] = {}
        self._wrapped: set[str] = set()
        self._failed: Dict[str, str] = {}  # skill_name → error message
        self.plugin_manager = PluginManager()
        self.scheduler = SkillScheduler(self)
        t0 = time.perf_counter()
        self._register_defaults()
        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        failed_names = list(self._failed.keys())
        logger.info(
            f"SkillExecutor ready: {len(self._registry)} skills registered, "
            f"{len(failed_names)} failed {failed_names}, scheduler ready in {elapsed}ms"
        )

    def _register_defaults(self) -> None:
        """Registra las skills disponibles."""
        default_skills = [
            WebSearchSkill,
            DateTimeSkill,
            SystemInfoSkill,
            CalculatorSkill,
            WebScraperSkill,
            ShellSkill,
            ExternalAPIsSkill,
            MemoryCompactionSkill,
            FileManagerSkill,
            CodeDoctorSkill,
            VisionSkill,
            UIAutomationSkill,
            NotificationSkill,
            MonitorSkill,
            VoiceSkill,
            AppIntegrationsSkill,
            WakeWordSkill,
            CameraSkill,
            SystemTraySkill,
            OSControlSkill,
            ClipboardSkill,
            ActiveWindowSkill,
            BrowserSkill,
            EventLogSkill,
            TelegramSkill,
            WeatherSkill,
            YouTubeSkill,
            ReminderSkill,
            DesktopSkill,
            TaskPlannerSkill,
        ]
        for skill_class in default_skills:
            try:
                self.register(skill_class())
            except Exception as e:
                skill_name = getattr(skill_class, "name", skill_class.__name__)
                error_msg = f"{type(e).__name__}: {e}"
                self._failed[skill_name] = error_msg
                logger.error(f"Failed to register {skill_class.__name__}: {error_msg}")

        planner = self.get("task_planner")
        if planner and hasattr(planner, "set_skill_executor"):
            planner.set_skill_executor(self)

    def register(self, skill: BaseSkill) -> None:
        """Registra una nueva skill. Idempotente respecto al wrapping de hooks."""
        self._registry[skill.name] = skill
        if skill.name not in self._wrapped:
            skill.execute = self.plugin_manager.wrap_skill(skill.name, skill.execute)
            self._wrapped.add(skill.name)
        logger.info(f"Skill registered: {skill.name}")

    def get(self, name: str) -> Optional[BaseSkill]:
        """Obtiene una skill por nombre."""
        return self._registry.get(name)

    def list_skills(self) -> List[Dict[str, Any]]:
        """Lista todas las skills disponibles."""
        return [skill.get_metadata() for skill in self._registry.values()]

    def can_execute(self, skill_name: str) -> bool:
        """Verifica si una skill existe."""
        return skill_name in self._registry

    def get_skill_status(self) -> Dict[str, Any]:
        """Returns registration status for all skills (ok + failed)."""
        ok = {name: {"status": "ok"} for name in self._registry}
        failed = {name: {"status": "failed", "error": err} for name, err in self._failed.items()}
        return {**ok, **failed}

    async def execute(self, skill_name: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ejecuta una skill por nombre.

        Returns:
            {
                "success": bool,
                "result": Any,
                "error": Optional[str],
                "execution_time": float,
                "skill": str
            }
        """
        if not skill_name:
            # Empty skill name = no execution needed (direct LLM response)
            logger.debug("No skill requested, skipping execution")
            return {"success": True, "result": None, "error": None, "execution_time": 0, "skill": ""}

        skill = self.get(skill_name)
        if not skill:
            logger.error(f"Skill '{skill_name}' no encontrada. Available: {list(self._registry.keys())}")
            return {
                "success": False,
                "result": None,
                "error": f"Skill '{skill_name}' no encontrada",
                "execution_time": 0,
                "skill": skill_name,
            }

        logger.info(f"Executing skill: {skill_name} with inputs: {list(inputs.keys())}")
        try:
            result = await skill.execute(inputs)
            result["skill"] = skill_name
            logger.debug(f"Skill {skill_name} completed. Success: {result.get('success')}")
            return result
        except Exception as e:
            logger.error(f"Skill {skill_name} threw exception: {e}")
            return {
                "success": False,
                "result": None,
                "error": f"Exception in skill execution: {str(e)}",
                "execution_time": 0,
                "skill": skill_name,
            }

    async def execute_plan(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Ejecuta cada paso del plan del reasoning loop.
        Soporta pipeline: resultados de pasos anteriores se pasan a los siguientes.

        Features:
            - Data passing: $prev para resultado del paso anterior
            - Vision→Click: coordenadas de vision.find se inyectan en ui_automation.click
            - Auto-wait: pausa breve entre pasos para estabilidad de UI
            - Error recovery: continua con siguientes pasos si uno falla (configurable)

        Args:
            plan: Lista de pasos generados por generate_plan() de la Mente

        Returns:
            Dict con resultados de cada paso y status general
        """
        logger.debug(f"Starting plan execution with {len(plan)} steps")
        results = []
        all_success = True
        executed_skills = []
        pipeline_context = {}  # Datos compartidos entre pasos

        for idx, step in enumerate(plan):
            # Normaliza: si el paso es string, lo convierte a dict
            if isinstance(step, str):
                step = {"step": idx + 1, "action": step, "skill": "", "inputs": {}}
            if not isinstance(step, dict):
                continue

            step_num = step.get("step", idx + 1)
            skill_name = step.get("skill", "")
            action = step.get("action", "")
            inputs = step.get("inputs", {})

            # ── Pipeline: inject data from previous steps ──
            inputs = self._resolve_pipeline_refs(inputs, pipeline_context)

            # ── Auto-wait between UI steps for stability ──
            if idx > 0 and skill_name in ("ui_automation", "vision"):
                await asyncio.sleep(0.5)

            logger.debug(f"Step {step_num}: {action} | Skill: '{skill_name}' | Inputs: {list(inputs.keys())}")

            # Mapeo de skills especiales (empty or direct_* strings)
            if skill_name in ("direct_response", "direct_llm_call", "none", ""):
                logger.debug(f"Step {step_num} is direct LLM response (no skill)")
                results.append(
                    {
                        "step": step_num,
                        "skill": skill_name if skill_name else "direct_response",
                        "action": action,
                        "success": True,
                        "result": {"message": "Direct LLM response (no skill needed)"},
                        "error": None,
                    }
                )
                continue

            # Ejecutar skill real
            if not self.can_execute(skill_name):
                logger.error(
                    f"Step {step_num}: Skill '{skill_name}' no disponible. Available: {list(self._registry.keys())}"
                )
                results.append(
                    {
                        "step": step_num,
                        "skill": skill_name,
                        "action": action,
                        "success": False,
                        "result": None,
                        "error": f"Skill '{skill_name}' no disponible",
                    }
                )
                all_success = False
                continue

            logger.info(f"Step {step_num}: Executing skill '{skill_name}'")
            step_result = await self.execute(skill_name, inputs)
            results.append({"step": step_num, "action": action, **step_result})
            executed_skills.append(skill_name)

            # ── Pipeline: store result for next steps ──
            pipeline_context[f"step_{step_num}"] = step_result.get("result")
            pipeline_context["prev"] = step_result.get("result")
            pipeline_context["prev_success"] = step_result.get("success", False)

            # Auto-inject vision.find coordinates into next click step
            if skill_name == "vision" and step_result.get("success"):
                result_data = step_result.get("result", {})
                found = result_data.get("result", {}) if isinstance(result_data, dict) else {}
                if isinstance(found, dict) and found.get("found") and found.get("absolute_location"):
                    pipeline_context["found_x"] = found["absolute_location"]["x"]
                    pipeline_context["found_y"] = found["absolute_location"]["y"]
                    logger.info(
                        f"Pipeline: vision found element at ({pipeline_context['found_x']}, {pipeline_context['found_y']})"  # noqa: E501
                    )

            if not step_result.get("success"):
                logger.warning(f"Step {step_num}: Skill '{skill_name}' failed - {step_result.get('error')}")
                all_success = False
            else:
                logger.debug(f"Step {step_num}: Skill '{skill_name}' completed successfully")

        logger.info(
            f"Plan execution complete. Steps: {len(plan)}, Executed: {len(executed_skills)}, Success: {all_success}"
        )
        return {
            "plan_executed": len(plan),
            "skills_executed": executed_skills,
            "results": results,
            "success": all_success,
            "pipeline_context": {k: str(v)[:200] for k, v in pipeline_context.items() if k != "prev"},
        }

    def _resolve_pipeline_refs(self, inputs: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Resuelve referencias a datos de pasos anteriores en los inputs.

        Patrones soportados:
            - Valor 0 en x/y cuando hay found_x/found_y en contexto → inyecta coordenadas
            - "$prev" como valor → sustituye por resultado del paso anterior
            - "$step_N" → resultado del paso N
        """
        resolved = dict(inputs)

        # Auto-inject vision coordinates into click with x=0, y=0
        if resolved.get("action") == "click" and resolved.get("x") == 0 and resolved.get("y") == 0:
            if "found_x" in ctx and "found_y" in ctx:
                resolved["x"] = ctx["found_x"]
                resolved["y"] = ctx["found_y"]
                logger.info(f"Pipeline: injected coordinates ({ctx['found_x']}, {ctx['found_y']}) into click")

        # Resolve string references
        for key, value in resolved.items():
            if isinstance(value, str):
                if value == "$prev" and "prev" in ctx:
                    resolved[key] = ctx["prev"]
                elif value.startswith("$step_") and value[6:].isdigit():
                    step_key = f"step_{value[6:]}"
                    if step_key in ctx:
                        resolved[key] = ctx[step_key]

        return resolved

    async def execute_plan_parallel(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Ejecuta plan en paralelo usando el SkillScheduler."""
        return await self.scheduler.execute(plan)

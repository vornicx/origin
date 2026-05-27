"""
SubAgent — Delegación a sub-agentes para trabajo en paralelo.

Inspirado en Hermes Agent. Permite que Origin:
  - Lance sub-agentes para tareas específicas
  - Ejecuten en paralelo vía asyncio.gather
  - Cada sub-agente tiene su propio contexto y razonamiento
  - Los resultados se fusionan en la respuesta principal

Uso:
    sub = SubAgent(mind)
    task1 = sub.delegate("Busca el clima en Madrid")
    task2 = sub.delegate("Dime la hora actual")
    results = await sub.gather([task1, task2])
"""

import asyncio
import logging
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger("origin.core.subagent")


@dataclass
class SubAgentTask:
    """Una tarea delegada a un sub-agente."""

    task_id: str
    instruction: str
    status: str = "pending"  # pending | running | completed | failed
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = ""
    completed_at: Optional[str] = None
    execution_time: float = 0.0


class SubAgent:
    """Gestor de sub-agentes para ejecución paralela de tareas.

    Cada sub-agente recibe una instrucción y ejecuta el reasoning loop
    completo (intent → plan → act → check → save → answer) de forma
    aislada, con su propio contexto.

    Los sub-agentes NO comparten memoria entre sí, pero el agente
    principal sí ve los resultados de todos.
    """

    def __init__(self, mind):
        self._mind = mind
        self._tasks: Dict[str, SubAgentTask] = {}

    def delegate(self, instruction: str) -> SubAgentTask:
        """Registra una tarea para delegación.

        Retorna un SubAgentTask inmediatamente (no ejecuta aún).
        Llamar a gather() para ejecutar todas las tareas registradas.
        """
        task = SubAgentTask(
            task_id=str(uuid.uuid4())[:8],
            instruction=instruction,
            created_at=datetime.utcnow().isoformat(),
        )
        self._tasks[task.task_id] = task
        logger.info(f"SubAgent: tarea '{instruction[:60]}...' registrada ({task.task_id})")
        return task

    async def gather(self, tasks: List[SubAgentTask] = None, max_concurrent: int = 5) -> List[SubAgentTask]:
        """Ejecuta todas las tareas en paralelo y retorna los resultados.

        Args:
            tasks: Lista de tareas a ejecutar (si es None, ejecuta todas las pendientes)
            max_concurrent: Máximo de tareas simultáneas

        Returns:
            Lista de SubAgentTask con resultados poblados
        """
        if tasks is None:
            tasks = [t for t in self._tasks.values() if t.status == "pending"]
        if not tasks:
            return []

        logger.info(f"SubAgent: ejecutando {len(tasks)} tareas (max {max_concurrent} concurrentes)")

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _run(task: SubAgentTask):
            async with semaphore:
                return await self._execute_single(task)

        results = await asyncio.gather(*[_run(t) for t in tasks], return_exceptions=True)

        final = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tasks[i].status = "failed"
                tasks[i].error = str(result)
                logger.warning(f"SubAgent tarea {tasks[i].task_id} falló: {result}")
            final.append(tasks[i])

        return final

    async def _execute_single(self, task: SubAgentTask) -> SubAgentTask:
        """Ejecuta una única tarea con el reasoning loop completo."""
        import time

        start = time.time()
        task.status = "running"

        try:
            result = await self._mind.think(task.instruction)
            task.result = {
                "cycle_id": result.cycle_id,
                "final_answer": result.final_answer,
                "steps": {k.value: v for k, v in result.steps.items()},
            }
            task.status = "completed"
            task.execution_time = round(time.time() - start, 3)
            task.completed_at = datetime.utcnow().isoformat()
            logger.info(f"SubAgent tarea {task.task_id} completada en {task.execution_time}s")
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            task.execution_time = round(time.time() - start, 3)
            logger.error(f"SubAgent tarea {task.task_id} error: {e}")

        return task

    def get_task(self, task_id: str) -> Optional[SubAgentTask]:
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[SubAgentTask]:
        return list(self._tasks.values())

    def clear_completed(self, max_age_minutes: int = 60):
        """Limpia tareas completadas antiguas."""
        now = datetime.utcnow()
        to_remove = []
        for tid, task in self._tasks.items():
            if task.completed_at:
                age = now - datetime.fromisoformat(task.completed_at)
                if age.total_seconds() > max_age_minutes * 60:
                    to_remove.append(tid)
        for tid in to_remove:
            del self._tasks[tid]

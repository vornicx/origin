"""Agent Workspace para Origin — tareas de larga duración con monitorización.

Windows 11 Agent Workspace permite a los agentes ejecutar tareas en segundo plano
y que el usuario pueda ver su progreso desde el OS.

Este módulo gestiona:
  - Registro de tareas de larga duración (scraping, análisis, monitorización)
  - Estado y progreso de cada tarea
  - Historial de tareas completadas
  - Cancelación de tareas en ejecución
"""

from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum
from dataclasses import dataclass
import asyncio
import logging
import uuid

logger = logging.getLogger("origin.skills.agent_workspace")


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentTask:
    """Una tarea de larga duración en el Agent Workspace."""

    task_id: str
    name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    created_at: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    coro: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "description": self.description,
            "status": self.status.value,
            "progress": self.progress,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class AgentWorkspace:
    """Workspace para tareas de larga duración.

    Uso:
        workspace = AgentWorkspace()
        task = await workspace.run_task("web_scraper", url="https://...")
        status = workspace.get_task(task.task_id)
    """

    def __init__(self):
        self._tasks: Dict[str, AgentTask] = {}
        self._max_history = 50
        logger.info("AgentWorkspace initialized")

    async def run_task(self, name: str, coro, description: str = "") -> AgentTask:
        """Registra y ejecuta una tarea en segundo plano."""
        task_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        task = AgentTask(
            task_id=task_id,
            name=name,
            description=description or f"Tarea: {name}",
            created_at=now,
            coro=coro,
        )
        self._tasks[task_id] = task
        asyncio.create_task(self._execute_task(task_id))
        return task

    async def _execute_task(self, task_id: str):
        """Ejecuta el coroutine y actualiza estado."""
        task = self._tasks.get(task_id)
        if not task:
            return
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow().isoformat()
        try:
            result = await task.coro
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.utcnow().isoformat()
            task.progress = 1.0
            task.result = result if isinstance(result, dict) else {"result": str(result)}
        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow().isoformat()
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.completed_at = datetime.utcnow().isoformat()
            task.error = str(e)
            logger.warning(f"Agent task {task.name} failed: {e}")

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 20, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        tasks = list(self._tasks.values())
        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return [t.to_dict() for t in tasks[:limit]]

    def cancel_task(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task and task.status == TaskStatus.RUNNING and task.coro:
            try:
                task.coro.throw(asyncio.CancelledError)
            except (asyncio.CancelledError, StopIteration):
                pass
            except Exception:
                pass
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow().isoformat()
            return True
        return False

    def update_progress(self, task_id: str, progress: float):
        task = self._tasks.get(task_id)
        if task:
            task.progress = min(1.0, max(0.0, progress))

    def cleanup(self, max_age_hours: int = 24):
        now = datetime.utcnow()
        to_remove = []
        for tid, task in self._tasks.items():
            if task.completed_at:
                age = now - datetime.fromisoformat(task.completed_at)
                if age.total_seconds() > max_age_hours * 3600:
                    to_remove.append(tid)
        for tid in to_remove:
            del self._tasks[tid]
        logger.debug(f"AgentWorkspace cleanup: removed {len(to_remove)} old tasks")

    def get_stats(self) -> Dict[str, Any]:
        total = len(self._tasks)
        by_status = {}
        for t in self._tasks.values():
            s = t.status.value
            by_status[s] = by_status.get(s, 0) + 1
        return {"total": total, "by_status": by_status}

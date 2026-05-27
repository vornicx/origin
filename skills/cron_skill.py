"""
CronSkill — Tareas programadas para Origin (inspirado en Hermes Agent).

Permite:
  - Programar tareas recurrentes (cada N minutos/horas/días)
  - Ejecutar skills en horario específico
  - Ver historial de ejecuciones
  - Notificar resultados vía WebSocket/tray

Formato cron: MIN HOUR DAY MONTH DAY_OF_WEEK (estilo cron Unix)
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("origin.skills.cron")


class CronStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CronJob:
    """Una tarea programada."""

    job_id: str
    name: str
    expression: str  # cron expression: "*/5 * * * *"
    skill_name: str
    skill_inputs: Dict[str, Any] = field(default_factory=dict)
    status: CronStatus = CronStatus.ACTIVE
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    run_count: int = 0
    fail_count: int = 0
    created_at: str = ""
    tz_offset: int = 0  # minutes from UTC

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "expression": self.expression,
            "skill_name": self.skill_name,
            "status": self.status.value,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "fail_count": self.fail_count,
            "created_at": self.created_at,
        }


def _parse_cron(expression: str) -> Optional[List[int]]:
    """Parsea expresión cron simplificada: MIN HOUR DAY MONTH DOW
    Retorna [minute, hour, day, month, day_of_week] con -1 para '*'"""
    parts = expression.strip().split()
    if len(parts) != 5:
        return None
    result = []
    for part in parts:
        if part == "*":
            result.append(-1)
        elif part.isdigit():
            result.append(int(part))
        elif part.startswith("*/"):
            result.append(part)  # "*/5"
        else:
            return None
    return result


def _matches_cron(parsed: List, dt: datetime) -> bool:
    """Verifica si un datetime coincide con la expresión cron."""
    fields = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
    for rule, val in zip(parsed, fields):
        if rule == -1:
            continue
        if isinstance(rule, str) and rule.startswith("*/"):
            interval = int(rule[2:])
            if interval <= 0 or val % interval != 0:
                return False
        elif isinstance(rule, int) and rule != val:
            return False
    return True


def _calculate_next_run(expression: str, after: Optional[datetime] = None) -> Optional[datetime]:
    """Devuelve el próximo datetime en UTC en que dispara la expresión cron.

    Avanza minuto a minuto desde `after + 1min` buscando coincidencia.
    Límite de búsqueda: 366 días (suficiente para cualquier cron válido).
    """
    parsed = _parse_cron(expression)
    if not parsed:
        return None
    base = (after or datetime.utcnow()).replace(second=0, microsecond=0)
    candidate = base + timedelta(minutes=1)
    limit = candidate + timedelta(days=366)
    while candidate < limit:
        if _matches_cron(parsed, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    return None


class CronSkill:
    """Sistema de tareas programadas.

    Uso:
        cron = CronSkill(skill_executor)
        cron.add_job("hourly_report", "0 * * * *", "system_info", {"action": "snapshot"})
        cron.start()  # background loop
    """

    def __init__(self, skill_executor):
        self._executor = skill_executor
        self._jobs: Dict[str, CronJob] = {}
        self._history: List[Dict[str, Any]] = []
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._max_history = 100
        self._broadcast_cb = None
        self._services_graph = None

    def set_broadcast(self, cb):
        """Callback para notificar ejecuciones vía WebSocket."""
        self._broadcast_cb = cb

    def set_services_graph(self, sg):
        """Conecta el ServicesGraph para disparar eventos 'cron_fired'."""
        self._services_graph = sg

    def add_job(
        self, name: str, expression: str, skill_name: str, inputs: dict = None, tz_offset: int = 0
    ) -> Optional[CronJob]:
        """Añade una tarea programada."""
        parsed = _parse_cron(expression)
        if not parsed:
            logger.warning(f"Cron: expresión inválida '{expression}'")
            return None

        import uuid

        next_dt = _calculate_next_run(expression)
        job = CronJob(
            job_id=str(uuid.uuid4())[:8],
            name=name,
            expression=expression,
            skill_name=skill_name,
            skill_inputs=inputs or {},
            created_at=datetime.utcnow().isoformat(),
            next_run=next_dt.isoformat() if next_dt else None,
            tz_offset=tz_offset,
        )
        self._jobs[job.job_id] = job
        logger.info(f"Cron: job '{name}' creado ({expression}), next_run={job.next_run}")
        return job

    def remove_job(self, job_id: str) -> bool:
        if job_id in self._jobs:
            del self._jobs[job_id]
            return True
        return False

    def pause_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.status = CronStatus.PAUSED
            return True
        return False

    def resume_job(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if job:
            job.status = CronStatus.ACTIVE
            return True
        return False

    def list_jobs(self) -> List[Dict[str, Any]]:
        return [j.to_dict() for j in self._jobs.values()]

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        return self._history[-limit:]

    # ── Background loop ──────────────────────────────────────

    def start(self):
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Cron loop started")

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self):
        """Tick cada 30 segundos, verifica jobs pendientes."""
        while self._running:
            try:
                now = datetime.utcnow()
                for job in list(self._jobs.values()):
                    if job.status != CronStatus.ACTIVE:
                        continue
                    parsed = _parse_cron(job.expression)
                    if parsed and _matches_cron(parsed, now):
                        asyncio.create_task(self._execute_job(job))
            except Exception as e:
                logger.warning(f"Cron tick error: {e}")
            await asyncio.sleep(30)

    async def _execute_job(self, job: CronJob):
        """Ejecuta un job y registra el resultado."""
        logger.info(f"Cron ejecutando '{job.name}' (skill={job.skill_name})")
        now_str = datetime.utcnow().isoformat()
        job.last_run = now_str

        # Pre-calculate next_run before execution so it's always fresh
        next_dt = _calculate_next_run(job.expression)
        job.next_run = next_dt.isoformat() if next_dt else None

        try:
            result = await self._executor.execute(job.skill_name, job.skill_inputs)
            success = result.get("success", False)
            job.run_count += 1
            if not success:
                job.fail_count += 1

            entry = {
                "job_id": job.job_id,
                "name": job.name,
                "timestamp": now_str,
                "success": success,
                "result": str(result.get("result"))[:200] if result.get("result") else None,
                "error": result.get("error"),
            }
            self._history.append(entry)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history :]

            # Broadcast vía WebSocket
            if self._broadcast_cb:
                try:
                    await self._broadcast_cb(
                        {
                            "type": "cron_result",
                            "job_name": job.name,
                            "success": success,
                            "timestamp": now_str,
                            "next_run": job.next_run,
                        }
                    )
                except Exception:
                    pass

            # Fire services_graph event
            if self._services_graph:
                try:
                    self._services_graph.fire(
                        "cron_fired",
                        {"job_name": job.name, "skill": job.skill_name, "success": success},
                    )
                except Exception:
                    pass

            status = "OK" if success else "FAIL"
            logger.info(f"Cron '{job.name}': {status} (runs={job.run_count}, next={job.next_run})")

        except Exception as e:
            job.fail_count += 1
            logger.error(f"Cron '{job.name}' error: {e}")

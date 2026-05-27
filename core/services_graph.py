"""
ServicesGraph — Workflows declarativos trigger-action (IFTTT-like).

Inspirado en automation flows de OpenHuman. Conecta servicios entre sí con
reglas declarativas en JSON:

  trigger → conditions → actions

Triggers soportados:
  proactive_event   - Evento del ProactiveEngine (filtrable por kind/type_id)
  clipboard_change  - Nuevo contenido en clipboard (con regex pattern)
  window_change     - Cambio de ventana activa (con regex match)
  system_alert      - Alerta del monitor (CPU/RAM spike)
  schedule          - Cron expression (delegado a CronSkill)
  manual            - Disparado por POST /workflows/{id}/run
  reasoning_done    - Despues de cada ciclo think() (con filtro)

Actions soportadas:
  skill            - Ejecuta una skill con inputs
  notify           - Envía notificación (toast WS o Telegram)
  chain            - Secuencia de actions
  branch           - Condicional (if/else basado en context)
  http             - HTTP request a un endpoint externo
  log              - Append a un audit log

Persistencia: data/workflows/workflows.json
Audit:        data/workflows/runs.jsonl
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Awaitable

logger = logging.getLogger("origin.services_graph")

# ── Config ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "workflows"
WORKFLOWS_FILE = DATA_DIR / "workflows.json"
RUNS_FILE = DATA_DIR / "runs.jsonl"

MAX_RUNS_LOG = 500
MAX_CHAIN_DEPTH = 8  # Anti-recursion guard
ACTION_TIMEOUT_S = 30  # Per-action timeout

VALID_TRIGGER_TYPES = frozenset(
    {
        "proactive_event",
        "clipboard_change",
        "window_change",
        "system_alert",
        "schedule",
        "manual",
        "reasoning_done",
    }
)

VALID_ACTION_TYPES = frozenset(
    {
        "skill",
        "notify",
        "chain",
        "branch",
        "http",
        "log",
    }
)


# ── Data structures ─────────────────────────────────────────


@dataclass
class WorkflowRun:
    """Registro de una ejecución de workflow."""

    id: str
    workflow_id: str
    workflow_name: str
    trigger_type: str
    trigger_data: Dict[str, Any] = field(default_factory=dict)
    started_at: str = ""
    finished_at: Optional[str] = None
    status: str = "running"  # running | success | partial | failed | skipped
    actions_executed: int = 0
    error: Optional[str] = None
    duration_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Workflow:
    """Definición declarativa de un workflow.

    Schema:
      {
        "id": "...",
        "name": "...",
        "enabled": true,
        "trigger": {"type": "clipboard_change", "params": {"pattern": "^https?://"}},
        "conditions": [{"type": "field_eq", "path": "metadata.length", "value": 100}],
        "actions": [{"type": "skill", "target": "web_scraper", "inputs": {...}}]
      }
    """

    id: str
    name: str
    enabled: bool = True
    trigger: Dict[str, Any] = field(default_factory=dict)
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    actions: List[Dict[str, Any]] = field(default_factory=list)
    description: str = ""
    created_at: str = ""
    updated_at: str = ""
    run_count: int = 0
    last_run_at: Optional[str] = None
    last_status: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Built-in workflow examples ─────────────────────────────
# Estos son ejemplos opcionales que se cargan si no hay workflows guardados.

DEFAULT_WORKFLOWS = [
    {
        "id": "wf_clipboard_url_log",
        "name": "Log URLs copiadas",
        "description": "Cuando copias un URL, lo guarda en clipboard_history para revisar despues.",
        "enabled": False,  # Opt-in
        "trigger": {"type": "clipboard_change", "params": {"pattern": "^https?://"}},
        "conditions": [],
        "actions": [
            {"type": "log", "params": {"category": "urls_copied"}},
        ],
    },
    {
        "id": "wf_idle_focus_mode",
        "name": "Modo enfoque en codigo",
        "description": "Cuando abres VSCode/Cursor, suprime sugerencias proactivas (modo enfoque).",
        "enabled": False,
        "trigger": {"type": "window_change", "params": {"process_pattern": "(?i)code\\.exe|cursor\\.exe"}},
        "conditions": [],
        "actions": [
            {"type": "log", "params": {"category": "focus_mode_auto", "message": "Detected IDE"}},
        ],
    },
    {
        "id": "wf_high_cpu_telegram",
        "name": "Alerta CPU alto via Telegram",
        "description": "Si CPU > 90% por mas de 30s, envia notificacion a Telegram.",
        "enabled": False,
        "trigger": {"type": "system_alert", "params": {"metric": "cpu", "min_severity": "high"}},
        "conditions": [],
        "actions": [
            {"type": "skill", "target": "telegram", "inputs": {"action": "send", "text": "[ALERT] CPU alto detectado"}},
        ],
    },
]


# ── Helpers ─────────────────────────────────────────────────


def _get_nested(obj: Dict[str, Any], path: str) -> Any:
    """Obtiene un valor nested de un dict via dot-path (e.g. 'metadata.length')."""
    cur = obj
    for key in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
        if cur is None:
            return None
    return cur


def _check_condition(condition: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """Evalúa una condición declarativa contra el contexto del trigger."""
    ctype = condition.get("type", "")
    path = condition.get("path", "")
    value = condition.get("value")

    actual = _get_nested(context, path)

    if ctype == "field_eq":
        return actual == value
    elif ctype == "field_neq":
        return actual != value
    elif ctype == "field_contains":
        return value in (actual or "") if isinstance(actual, str) else False
    elif ctype == "field_gt":
        try:
            return float(actual) > float(value)
        except (TypeError, ValueError):
            return False
    elif ctype == "field_lt":
        try:
            return float(actual) < float(value)
        except (TypeError, ValueError):
            return False
    elif ctype == "field_in":
        return actual in (value if isinstance(value, list) else [value])
    elif ctype == "regex_match":
        try:
            return bool(re.search(str(value), str(actual or "")))
        except re.error:
            return False
    return False


def _trigger_matches(workflow: Workflow, trigger_type: str, data: Dict[str, Any]) -> bool:
    """Verifica si un workflow debe dispararse para el evento."""
    wf_trigger = workflow.trigger or {}
    if wf_trigger.get("type") != trigger_type:
        return False

    params = wf_trigger.get("params", {})

    # Trigger-specific matching
    if trigger_type == "clipboard_change":
        pattern = params.get("pattern")
        if pattern:
            try:
                if not re.search(pattern, data.get("content", "")):
                    return False
            except re.error:
                return False

    elif trigger_type == "window_change":
        proc_pat = params.get("process_pattern")
        title_pat = params.get("title_pattern")
        if proc_pat:
            try:
                if not re.search(proc_pat, data.get("process", "")):
                    return False
            except re.error:
                return False
        if title_pat:
            try:
                if not re.search(title_pat, data.get("title", "")):
                    return False
            except re.error:
                return False

    elif trigger_type == "proactive_event":
        kind = params.get("kind")
        type_id = params.get("type_id")
        if kind and data.get("kind") != kind:
            return False
        if type_id and data.get("metadata", {}).get("type_id") != type_id:
            return False

    elif trigger_type == "system_alert":
        metric = params.get("metric")
        min_sev = params.get("min_severity", "low")
        if metric and data.get("metric") != metric:
            return False
        sev_order = {"low": 1, "medium": 2, "high": 3, "critical": 4}
        if sev_order.get(data.get("severity", "low"), 0) < sev_order.get(min_sev, 0):
            return False

    elif trigger_type == "reasoning_done":
        # Filter by intent task_type, etc.
        task_type = params.get("task_type")
        if task_type and data.get("task_type") != task_type:
            return False

    return True


# ── ServicesGraph engine ────────────────────────────────────


class ServicesGraph:
    """Motor de workflows trigger-action."""

    def __init__(self, skill_executor=None, mind=None):
        self._skill_executor = skill_executor
        self._mind = mind
        self._workflows: Dict[str, Workflow] = {}
        self._runs: deque[WorkflowRun] = deque(maxlen=MAX_RUNS_LOG)
        self._broadcast: Optional[Callable[[Dict], Awaitable[None]]] = None
        self._bg_tasks: set[asyncio.Task] = set()

        # Counters
        self._total_triggered = 0
        self._total_succeeded = 0
        self._total_failed = 0
        self._total_skipped = 0

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._load()

        if not self._workflows:
            self._install_defaults()

        logger.info(
            f"ServicesGraph initialized: {len(self._workflows)} workflows "
            f"({sum(1 for w in self._workflows.values() if w.enabled)} enabled)"
        )

    # ── Lifecycle ─────────────────────────────────────────────

    def set_broadcast(self, broadcast_fn: Callable[[Dict], Awaitable[None]]):
        """Inject WS broadcast para emitir run updates."""
        self._broadcast = broadcast_fn

    def set_dependencies(self, skill_executor=None, mind=None):
        """Inyecta dependencias post-init si hace falta."""
        if skill_executor:
            self._skill_executor = skill_executor
        if mind:
            self._mind = mind

    # ── CRUD ──────────────────────────────────────────────────

    def list(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """Lista todos los workflows."""
        out = []
        for w in self._workflows.values():
            if enabled_only and not w.enabled:
                continue
            out.append(w.to_dict())
        return out

    def get(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene un workflow por id."""
        w = self._workflows.get(workflow_id)
        return w.to_dict() if w else None

    def create(self, definition: Dict[str, Any]) -> Dict[str, Any]:
        """Crea un nuevo workflow desde un dict declarativo."""
        # Validate
        valid, err = self._validate_definition(definition)
        if not valid:
            return {"ok": False, "error": err}

        wid = definition.get("id") or f"wf_{uuid.uuid4().hex[:8]}"
        now = datetime.now().isoformat()

        wf = Workflow(
            id=wid,
            name=definition.get("name", "Untitled"),
            enabled=bool(definition.get("enabled", True)),
            trigger=definition.get("trigger", {}),
            conditions=definition.get("conditions", []),
            actions=definition.get("actions", []),
            description=definition.get("description", ""),
            created_at=now,
            updated_at=now,
        )
        self._workflows[wid] = wf
        self._save()
        logger.info(f"Workflow created: {wid} ({wf.name})")
        return {"ok": True, "workflow": wf.to_dict()}

    def update(self, workflow_id: str, definition: Dict[str, Any]) -> Dict[str, Any]:
        """Actualiza un workflow existente."""
        if workflow_id not in self._workflows:
            return {"ok": False, "error": f"Workflow '{workflow_id}' not found"}

        # Validate the merged definition
        existing = self._workflows[workflow_id]
        merged = {**existing.to_dict(), **definition}
        valid, err = self._validate_definition(merged)
        if not valid:
            return {"ok": False, "error": err}

        wf = existing
        if "name" in definition:
            wf.name = definition["name"]
        if "enabled" in definition:
            wf.enabled = bool(definition["enabled"])
        if "trigger" in definition:
            wf.trigger = definition["trigger"]
        if "conditions" in definition:
            wf.conditions = definition["conditions"]
        if "actions" in definition:
            wf.actions = definition["actions"]
        if "description" in definition:
            wf.description = definition["description"]
        wf.updated_at = datetime.now().isoformat()

        self._save()
        return {"ok": True, "workflow": wf.to_dict()}

    def delete(self, workflow_id: str) -> Dict[str, Any]:
        """Elimina un workflow."""
        if workflow_id not in self._workflows:
            return {"ok": False, "error": f"Workflow '{workflow_id}' not found"}
        del self._workflows[workflow_id]
        self._save()
        return {"ok": True, "deleted": workflow_id}

    def enable(self, workflow_id: str) -> Dict[str, Any]:
        return self.update(workflow_id, {"enabled": True})

    def disable(self, workflow_id: str) -> Dict[str, Any]:
        return self.update(workflow_id, {"enabled": False})

    # ── Triggers ──────────────────────────────────────────────

    async def fire(self, trigger_type: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Dispara workflows que matchean un evento.

        Llamado por:
          - ProactiveEngine cuando emite un evento
          - Monitor cuando detecta una alerta
          - Mind despues de cada think() (reasoning_done)
          - API manual via POST /workflows/{id}/run
        """
        if trigger_type not in VALID_TRIGGER_TYPES:
            return {"ok": False, "error": f"Invalid trigger type: {trigger_type}"}

        matched: List[Workflow] = []
        for wf in self._workflows.values():
            if not wf.enabled:
                continue
            if not _trigger_matches(wf, trigger_type, data):
                continue
            # Check conditions
            if wf.conditions and not all(_check_condition(c, data) for c in wf.conditions):
                self._total_skipped += 1
                continue
            matched.append(wf)

        if not matched:
            return {"ok": True, "matched": 0, "executions": []}

        # Execute matched workflows in parallel (fire-and-forget for non-manual)
        if trigger_type == "manual":
            # Manual triggers wait for completion
            results = await asyncio.gather(
                *(self._execute_workflow(wf, trigger_type, data) for wf in matched),
                return_exceptions=True,
            )
        else:
            # Background execution to not block the trigger source
            for wf in matched:
                task = asyncio.create_task(self._execute_workflow(wf, trigger_type, data))
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            results = [{"started": True} for _ in matched]

        return {
            "ok": True,
            "matched": len(matched),
            "executions": [
                r.to_dict() if isinstance(r, WorkflowRun) else (r if isinstance(r, dict) else {"error": str(r)})
                for r in results
            ],
        }

    async def run_manual(self, workflow_id: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Dispara un workflow específico manualmente."""
        wf = self._workflows.get(workflow_id)
        if not wf:
            return {"ok": False, "error": f"Workflow '{workflow_id}' not found"}
        result = await self._execute_workflow(wf, "manual", data or {})
        return {"ok": result.status == "success", "run": result.to_dict()}

    # ── Execution ─────────────────────────────────────────────

    async def _execute_workflow(self, wf: Workflow, trigger_type: str, data: Dict[str, Any]) -> WorkflowRun:
        """Ejecuta todas las actions de un workflow."""
        run = WorkflowRun(
            id=f"run_{uuid.uuid4().hex[:8]}",
            workflow_id=wf.id,
            workflow_name=wf.name,
            trigger_type=trigger_type,
            trigger_data={k: v for k, v in data.items() if k != "content" or len(str(v)) < 200},
            started_at=datetime.now().isoformat(),
            status="running",
        )

        self._total_triggered += 1
        t0 = time.perf_counter()

        # Broadcast started
        await self._broadcast_run(run, "started")

        try:
            any_failed = False
            for idx, action in enumerate(wf.actions):
                retries = max(0, int(action.get("retries", 0)))
                continue_on_error = bool(action.get("continue_on_error", False))
                ok = False
                for attempt in range(retries + 1):
                    ok = await self._execute_action(action, data, depth=0)
                    if ok or attempt == retries:
                        break
                    logger.info(
                        f"Workflow {wf.id} action {idx} attempt {attempt + 1}/{retries + 1} failed, retrying"
                    )
                run.actions_executed += 1
                if not ok:
                    any_failed = True
                    logger.warning(
                        f"Workflow {wf.id} action {idx} ({action.get('type')}) failed"
                        + (" — continuing" if continue_on_error else " — aborting")
                    )
                    if not continue_on_error:
                        run.status = "failed"
                        run.error = f"Action {idx} ({action.get('type')}) failed"
                        break
            else:
                run.status = "partial" if any_failed else "success"

        except asyncio.TimeoutError:
            run.status = "failed"
            run.error = "Action timeout"
        except Exception as e:
            run.status = "failed"
            run.error = str(e)[:200]
            logger.warning(f"Workflow {wf.id} error: {e}")

        run.finished_at = datetime.now().isoformat()
        run.duration_ms = round((time.perf_counter() - t0) * 1000, 1)

        # Update workflow stats
        wf.run_count += 1
        wf.last_run_at = run.finished_at
        wf.last_status = run.status

        # Update global counters
        if run.status in ("success", "partial"):
            self._total_succeeded += 1
        elif run.status == "failed":
            self._total_failed += 1

        # Log run
        self._runs.appendleft(run)
        self._audit_log(run)

        # Periodic save (every 5 runs)
        if (self._total_succeeded + self._total_failed) % 5 == 0:
            self._save()

        # Broadcast finished
        await self._broadcast_run(run, "finished")

        logger.info(
            f"Workflow {wf.id} '{wf.name}': {run.status} "
            f"({run.actions_executed}/{len(wf.actions)} actions, "
            f"{run.duration_ms}ms)"
        )

        return run

    async def _execute_action(self, action: Dict[str, Any], context: Dict[str, Any], depth: int = 0) -> bool:
        """Ejecuta una sola action. Retorna True si OK."""
        if depth >= MAX_CHAIN_DEPTH:
            logger.warning(f"Action chain max depth ({MAX_CHAIN_DEPTH}) reached")
            return False

        atype = action.get("type", "")
        if atype not in VALID_ACTION_TYPES:
            logger.warning(f"Invalid action type: {atype}")
            return False

        try:
            if atype == "skill":
                return await self._action_skill(action, context)
            elif atype == "notify":
                return await self._action_notify(action, context)
            elif atype == "chain":
                return await self._action_chain(action, context, depth)
            elif atype == "branch":
                return await self._action_branch(action, context, depth)
            elif atype == "http":
                return await self._action_http(action, context)
            elif atype == "log":
                return await self._action_log(action, context)
        except Exception as e:
            logger.warning(f"Action '{atype}' error: {e}")
            return False
        return False

    # ── Action implementations ────────────────────────────────

    async def _action_skill(self, action: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Ejecuta una skill via SkillExecutor."""
        if not self._skill_executor:
            return False

        target = action.get("target", "")
        inputs = action.get("inputs", {})

        # Template substitution: {{trigger.field}} → value from context
        inputs = self._render_inputs(inputs, context)

        try:
            result = await asyncio.wait_for(
                self._skill_executor.execute(target, inputs),
                timeout=ACTION_TIMEOUT_S,
            )
            return bool(result.get("success"))
        except asyncio.TimeoutError:
            logger.warning(f"Skill '{target}' timed out")
            return False

    async def _action_notify(self, action: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Envia notificacion (WS toast o canal)."""
        message = self._render_string(action.get("message", "Notification"), context)
        title = self._render_string(action.get("title", "Origin"), context)
        severity = action.get("severity", "info")

        if self._broadcast:
            try:
                await self._broadcast(
                    {
                        "type": "monitor_alert",
                        "alert": {
                            "title": title,
                            "message": message,
                            "severity": severity,
                            "metric": "workflow",
                            "value": 0,
                            "threshold": 0,
                            "timestamp": datetime.now().isoformat(),
                        },
                    }
                )
            except Exception:
                pass
        return True

    async def _action_chain(self, action: Dict[str, Any], context: Dict[str, Any], depth: int) -> bool:
        """Ejecuta una secuencia de actions."""
        sub_actions = action.get("actions", [])
        for sub in sub_actions:
            ok = await self._execute_action(sub, context, depth=depth + 1)
            if not ok:
                return False
        return True

    async def _action_branch(self, action: Dict[str, Any], context: Dict[str, Any], depth: int) -> bool:
        """Condicional: if/else."""
        cond = action.get("if", {})
        if _check_condition(cond, context):
            for sub in action.get("then", []):
                await self._execute_action(sub, context, depth=depth + 1)
        else:
            for sub in action.get("else", []):
                await self._execute_action(sub, context, depth=depth + 1)
        return True

    async def _action_http(self, action: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """HTTP request a un endpoint externo (webhook)."""
        url = self._render_string(action.get("url", ""), context)
        method = action.get("method", "POST").upper()
        headers = action.get("headers", {})
        body = action.get("body", {})

        if not url:
            return False

        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                if method == "POST":
                    resp = await client.post(url, json=body, headers=headers)
                elif method == "GET":
                    resp = await client.get(url, headers=headers)
                else:
                    return False
                return 200 <= resp.status_code < 300
        except Exception as e:
            logger.debug(f"HTTP action error: {e}")
            return False

    async def _action_log(self, action: Dict[str, Any], context: Dict[str, Any]) -> bool:
        """Append entry a categoría de log."""
        category = action.get("params", {}).get("category", "general")
        message = action.get("params", {}).get("message", "")
        if not message:
            message = self._render_string(action.get("message", str(context)[:200]), context)

        log_file = DATA_DIR / f"log_{category}.jsonl"
        try:
            entry = {
                "ts": datetime.now().isoformat(),
                "message": message,
                "context": {k: str(v)[:200] for k, v in context.items()},
            }
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            return True
        except Exception:
            return False

    # ── Templating ────────────────────────────────────────────

    _TEMPLATE_PATTERN = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")

    def _render_string(self, template: str, context: Dict[str, Any]) -> str:
        """Sustituye {{trigger.field}} con valor del contexto."""
        if not isinstance(template, str):
            return template

        def replace(m):
            path = m.group(1)
            # Strip "trigger." prefix if present
            if path.startswith("trigger."):
                path = path[8:]
            val = _get_nested(context, path)
            return str(val) if val is not None else m.group(0)

        return self._TEMPLATE_PATTERN.sub(replace, template)

    def _render_inputs(self, inputs: Any, context: Dict[str, Any]) -> Any:
        """Renderiza inputs recursivamente con templating."""
        if isinstance(inputs, dict):
            return {k: self._render_inputs(v, context) for k, v in inputs.items()}
        elif isinstance(inputs, list):
            return [self._render_inputs(v, context) for v in inputs]
        elif isinstance(inputs, str):
            return self._render_string(inputs, context)
        return inputs

    # ── Validation ────────────────────────────────────────────

    def _validate_definition(self, d: Dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(d.get("name"), str) or not d["name"]:
            return False, "name required (string)"
        trigger = d.get("trigger", {})
        if not isinstance(trigger, dict) or not trigger.get("type"):
            return False, "trigger.type required"
        if trigger["type"] not in VALID_TRIGGER_TYPES:
            return False, f"invalid trigger type: {trigger['type']}"
        actions = d.get("actions", [])
        if not isinstance(actions, list):
            return False, "actions must be a list"
        for i, a in enumerate(actions):
            if not isinstance(a, dict) or not a.get("type"):
                return False, f"action {i} missing type"
            atype = a["type"]
            if atype not in VALID_ACTION_TYPES:
                return False, f"action {i} invalid type: {atype}"
            # Per-type required field validation
            if atype == "skill" and not isinstance(a.get("target"), str):
                return False, f"action {i} type=skill requires 'target' (string)"
            if atype == "http" and not isinstance(a.get("url"), str):
                return False, f"action {i} type=http requires 'url' (string)"
            if atype == "notify" and not isinstance(a.get("message"), str):
                return False, f"action {i} type=notify requires 'message' (string)"
            if atype == "chain" and not isinstance(a.get("actions"), list):
                return False, f"action {i} type=chain requires 'actions' (list)"
            if atype == "branch" and not isinstance(a.get("if"), dict):
                return False, f"action {i} type=branch requires 'if' (dict)"
        return True, ""

    # ── Persistence ───────────────────────────────────────────

    def _install_defaults(self):
        """Instala workflows ejemplo en primer arranque."""
        for d in DEFAULT_WORKFLOWS:
            self.create(d)

    def _load(self):
        if not WORKFLOWS_FILE.exists():
            return
        try:
            data = json.loads(WORKFLOWS_FILE.read_text(encoding="utf-8"))
            for entry in data.get("workflows", []):
                try:
                    wf = Workflow(**entry)
                    self._workflows[wf.id] = wf
                except Exception:
                    pass
            self._total_triggered = data.get("total_triggered", 0)
            self._total_succeeded = data.get("total_succeeded", 0)
            self._total_failed = data.get("total_failed", 0)
        except Exception as e:
            logger.warning(f"Failed to load workflows: {e}")

    def _save(self):
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "total_triggered": self._total_triggered,
                "total_succeeded": self._total_succeeded,
                "total_failed": self._total_failed,
                "workflows": [w.to_dict() for w in self._workflows.values()],
            }
            WORKFLOWS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save workflows: {e}")

    def _audit_log(self, run: WorkflowRun):
        try:
            with open(RUNS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(run.to_dict(), ensure_ascii=False) + "\n")
            # Prune if huge
            if RUNS_FILE.stat().st_size > 1_000_000:  # 1MB
                lines = RUNS_FILE.read_text(encoding="utf-8").strip().split("\n")
                keep = lines[-MAX_RUNS_LOG:]
                RUNS_FILE.write_text("\n".join(keep) + "\n", encoding="utf-8")
        except Exception:
            pass

    async def _broadcast_run(self, run: WorkflowRun, phase: str):
        if not self._broadcast:
            return
        try:
            await self._broadcast(
                {
                    "type": "workflow_run",
                    "phase": phase,
                    "run": run.to_dict(),
                }
            )
        except Exception:
            pass

    # ── Stats & history ───────────────────────────────────────

    def get_runs(self, n: int = 30, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
        results = []
        for r in self._runs:
            if workflow_id and r.workflow_id != workflow_id:
                continue
            results.append(r.to_dict())
            if len(results) >= n:
                break
        return results

    @property
    def stats(self) -> Dict[str, Any]:
        enabled = sum(1 for w in self._workflows.values() if w.enabled)
        return {
            "total_workflows": len(self._workflows),
            "enabled_workflows": enabled,
            "disabled_workflows": len(self._workflows) - enabled,
            "total_triggered": self._total_triggered,
            "total_succeeded": self._total_succeeded,
            "total_failed": self._total_failed,
            "total_skipped": self._total_skipped,
            "success_rate": round(self._total_succeeded / max(self._total_triggered, 1) * 100, 1),
            "runs_buffered": len(self._runs),
        }

"""
ProactiveEngine — Origin proactivo: no espera a que hables.

Inspirado en el "ambient computing" de OpenHuman. Detecta eventos del SO
en background y sugiere acciones contextuales sin que el usuario pregunte.

Eventos detectados:
  - Cambio de ventana activa → analiza app/titulo y sugiere
  - Texto copiado al clipboard → ofrece acciones (URL, codigo, email)
  - Spike de CPU/RAM → alerta
  - Ventana en pantalla completa (gaming/video) → silencia sugerencias
  - Idle prolongado → no interrumpe

Cada sugerencia:
  - Se broadcastea via WebSocket al frontend
  - Se persiste en un buffer rotativo
  - Pasa por throttling y dedup
  - Respeta "modo enfoque" del usuario

Cero LLM calls en el hot path. Las sugerencias se generan con heurísticas
declarativas. Opcionalmente, sugerencias complejas pueden usar el LLM
(throttled a 1 cada 5min para no saturar).

Uso:
    engine = ProactiveEngine(skill_executor, mind=mind)
    engine.set_broadcast(ws_broadcast_fn)
    engine.start()  # loop en background
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger("origin.proactive")

# ── Config ────────────────────────────────────────────────────
DATA_DIR = Path(__file__).parent.parent / "data" / "proactive"
EVENTS_FILE = DATA_DIR / "events.json"
RULES_FILE = DATA_DIR / "rules.json"  # Custom rules override/extend built-in ones

POLL_INTERVAL = 4.0  # Segundos entre polls
MAX_EVENTS = 200  # Buffer de eventos recientes
GLOBAL_RATE_LIMIT_S = 45.0  # Minimo entre sugerencias (anti-spam)
SAME_TYPE_RATE_LIMIT_S = 180.0  # Mismo tipo de sugerencia
DO_NOT_DISTURB_APPS = frozenset(
    {
        # Apps en las que NO interrumpir
        "explorer.exe",
        "winlogon.exe",
        "lockapp.exe",
    }
)
FULLSCREEN_BLOCK_APPS = frozenset(
    {
        # Apps fullscreen que silencian sugerencias
        "vlc.exe",
        "mpv.exe",
        "obs64.exe",
        "obs.exe",
        "obs32.exe",
        "discord.exe",  # En videocall
    }
)


# ── Event types ──────────────────────────────────────────────


@dataclass
class ProactiveEvent:
    """Evento proactivo detectado y/o sugerencia generada."""

    id: str
    kind: str  # window_change | clipboard | system_alert | idle | suggestion
    source: str  # active_window | clipboard | monitor | composite
    title: str  # Titulo corto para mostrar en UI
    message: str  # Descripcion completa
    timestamp: str = ""
    severity: str = "info"  # info | low | medium | high
    suggested_action: Optional[Dict[str, Any]] = None  # {skill, inputs} opcional
    metadata: Dict[str, Any] = field(default_factory=dict)
    dismissed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Suggestion rules ─────────────────────────────────────────
# Reglas declarativas para sugerir basado en lo que se detecta.

# Reglas para ventanas activas: (process_name_pattern, title_pattern, suggestion)
WINDOW_RULES: List[Dict[str, Any]] = [
    {
        "process": re.compile(r"(?i)code\.exe|cursor\.exe|devenv\.exe"),
        "title": re.compile(r"\.py\s+\("),
        "title_id": "ide_python",
        "message": "Editando Python. Puedo revisar el archivo o sugerir mejoras.",
        "action": {"skill": "code_doctor", "inputs": {"action": "analyze"}},
    },
    {
        "process": re.compile(r"(?i)chrome\.exe|msedge\.exe|firefox\.exe|brave\.exe"),
        "title": re.compile(r"(?i)stack ?overflow|github|documentation|docs"),
        "title_id": "browser_docs",
        "message": "Buscando documentacion. Quieres que resuma la pagina?",
        "action": None,
    },
    {
        "process": re.compile(r"(?i)acrord32\.exe|acrobat\.exe|sumatrapdf\.exe|edge\.exe.*\.pdf"),
        "title": re.compile(r"\.pdf"),
        "title_id": "pdf_open",
        "message": "Abriste un PDF. Quieres que lo resuma o extraiga puntos clave?",
        "action": None,
    },
    {
        "process": re.compile(r"(?i)winword\.exe|writer\.exe"),
        "title_id": "doc_editing",
        "message": "Editando un documento. Puedo ayudarte a redactar o corregir.",
        "action": None,
    },
    {
        "process": re.compile(r"(?i)excel\.exe|calc\.exe"),
        "title_id": "spreadsheet",
        "message": "Trabajando con hoja de calculo. Puedo ayudarte con formulas.",
        "action": None,
    },
    {
        "process": re.compile(r"(?i)slack\.exe|teams\.exe|discord\.exe"),
        "title": re.compile(r"(?i)\(\d+\)|\d+\s*new"),
        "title_id": "messaging_unread",
        "message": "Tienes mensajes sin leer. Quieres un resumen?",
        "action": None,
    },
]

# Reglas para clipboard: (pattern, suggestion)
CLIPBOARD_RULES: List[Dict[str, Any]] = [
    {
        "pattern": re.compile(r"^https?://[^\s]+$", re.MULTILINE),
        "title_id": "clip_url",
        "message": "Copiaste un URL. Quieres que lo abra, resuma o investigue?",
        "action": {"skill": "web_scraper", "inputs": {"action": "extract"}},
    },
    {
        "pattern": re.compile(r"[\w.-]+@[\w.-]+\.\w+"),
        "title_id": "clip_email",
        "message": "Copiaste un email. Quieres componer un mensaje?",
        "action": None,
    },
    {
        "pattern": re.compile(r"```[\s\S]+```|def\s+\w+|function\s+\w+|class\s+\w+"),
        "title_id": "clip_code",
        "message": "Copiaste codigo. Quieres que lo explique o revise?",
        "action": {"skill": "code_doctor", "inputs": {"action": "explain"}},
    },
    {
        "pattern": re.compile(r"(?i)error|exception|traceback|stack\s*trace"),
        "title_id": "clip_error",
        "message": "Copiaste un error. Quieres ayuda para resolverlo?",
        "action": None,
    },
    {
        "pattern": re.compile(r"^[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}|^\d{4}-\d{2}-\d{2}"),
        "title_id": "clip_date",
        "message": "Copiaste una fecha. Quieres crear un evento de calendario?",
        "action": None,
    },
]


# ── ProactiveEngine class ────────────────────────────────────


class ProactiveEngine:
    """Motor de sugerencias proactivas basado en eventos del SO.

    Hace polling de skills (active_window, clipboard) sin acoplarse a ellas
    y emite eventos via WebSocket cuando detecta algo accionable.
    """

    def __init__(self, skill_executor=None, mind=None):
        self._skill_executor = skill_executor
        self._mind = mind
        self._services_graph = None  # Optional, injected post-init
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._broadcast: Optional[Callable[[Dict], Awaitable[None]]] = None

        # State tracking
        self._last_window_key: Optional[str] = None
        self._last_clipboard_hash: Optional[str] = None
        self._last_suggestion_at: float = 0.0
        self._last_by_type: Dict[str, float] = {}
        self._fullscreen_silenced: bool = False

        # Event buffer
        self._events: deque[ProactiveEvent] = deque(maxlen=MAX_EVENTS)
        self._suggestion_count = 0
        self._dismissed_count = 0
        self._suppressed_count = 0
        self._focus_mode = False  # Si esta en True, suprime sugerencias

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Merge built-in rules with any custom rules from rules.json
        self._window_rules = list(WINDOW_RULES)
        self._clipboard_rules = list(CLIPBOARD_RULES)
        self._load_custom_rules()

        self._load_events()

        logger.info(
            f"ProactiveEngine initialized: {len(self._window_rules)} window rules, "
            f"{len(self._clipboard_rules)} clipboard rules"
        )

    # ── Custom rules ─────────────────────────────────────────

    def _load_custom_rules(self):
        """Load additional rules from rules.json and append to built-in lists.

        Format:
          {
            "window_rules": [
              {"process": "notepad.exe", "title_id": "notepad", "message": "..."}
            ],
            "clipboard_rules": [
              {"pattern": "regex_string", "title_id": "my_id", "message": "..."}
            ]
          }
        """
        if not RULES_FILE.exists():
            return
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load custom rules: {e}")
            return

        loaded_w = loaded_c = 0
        for rule in data.get("window_rules", []):
            try:
                compiled = dict(rule)
                if "process" in compiled:
                    compiled["process"] = re.compile(compiled["process"], re.IGNORECASE)
                if "title" in compiled:
                    compiled["title"] = re.compile(compiled["title"], re.IGNORECASE)
                self._window_rules.append(compiled)
                loaded_w += 1
            except Exception as e:
                logger.warning(f"Skipping invalid window rule: {e}")

        for rule in data.get("clipboard_rules", []):
            try:
                compiled = dict(rule)
                compiled["pattern"] = re.compile(compiled["pattern"])
                self._clipboard_rules.append(compiled)
                loaded_c += 1
            except Exception as e:
                logger.warning(f"Skipping invalid clipboard rule: {e}")

        if loaded_w or loaded_c:
            logger.info(f"Custom rules loaded: {loaded_w} window, {loaded_c} clipboard")

    def reload_rules(self):
        """Reload custom rules from disk (hot-reload without restart)."""
        self._window_rules = list(WINDOW_RULES)
        self._clipboard_rules = list(CLIPBOARD_RULES)
        self._load_custom_rules()

    # ── Lifecycle ─────────────────────────────────────────────

    def start(self):
        """Inicia el loop en background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"ProactiveEngine started: polling every {POLL_INTERVAL}s")

    def stop(self):
        """Detiene el loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("ProactiveEngine stopped")

    def set_broadcast(self, broadcast_fn: Callable[[Dict], Awaitable[None]]):
        """Inject la funcion de broadcast para emitir eventos."""
        self._broadcast = broadcast_fn

    def set_services_graph(self, graph):
        """Inject ServicesGraph to fire workflows on events."""
        self._services_graph = graph

    def set_focus_mode(self, enabled: bool):
        """Activa o desactiva el modo enfoque (silencia sugerencias)."""
        self._focus_mode = bool(enabled)
        logger.info(f"ProactiveEngine focus_mode={'on' if enabled else 'off'}")

    # ── Poll loop ─────────────────────────────────────────────

    async def _poll_loop(self):
        """Loop principal: detecta cambios cada POLL_INTERVAL segundos."""
        while self._running:
            try:
                if not self._focus_mode:
                    await asyncio.gather(
                        self._check_active_window(),
                        self._check_clipboard(),
                        return_exceptions=True,
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"ProactiveEngine poll error: {e}")

            try:
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break

    # ── Window change detection ──────────────────────────────

    async def _check_active_window(self):
        """Detecta cambios de ventana y dispara reglas."""
        if not self._skill_executor:
            return
        aw = self._skill_executor.get("active_window")
        if not aw:
            return

        try:
            r = await aw.execute({"action": "current"})
            if not r.get("success"):
                return
            window = r.get("result", {})
        except Exception:
            return

        process = (window.get("process") or "").lower()
        title = window.get("title") or ""
        window_key = f"{process}|{title[:80]}"

        # Skip if no change
        if window_key == self._last_window_key:
            return
        self._last_window_key = window_key

        # Fire workflows for window_change (independent of suggestions)
        if self._services_graph:
            try:
                await self._services_graph.fire(
                    "window_change",
                    {
                        "process": process,
                        "title": title,
                    },
                )
            except Exception:
                pass

        # Skip DND apps
        if process in DO_NOT_DISTURB_APPS:
            return

        # Detect fullscreen blockers
        is_fullscreen_app = process in FULLSCREEN_BLOCK_APPS
        if is_fullscreen_app:
            self._fullscreen_silenced = True
            return
        else:
            self._fullscreen_silenced = False

        # Run rules
        for rule in self._window_rules:
            proc_pat = rule.get("process")
            title_pat = rule.get("title")

            if proc_pat and not proc_pat.search(process):
                continue
            if title_pat and not title_pat.search(title):
                continue

            await self._emit_suggestion(
                source="active_window",
                kind="window_change",
                type_id=rule["title_id"],
                title=f"Contexto: {process}",
                message=rule["message"],
                metadata={"process": process, "title": title},
                action=rule.get("action"),
            )
            break  # Solo una sugerencia por cambio de ventana

    # ── Clipboard detection ──────────────────────────────────

    async def _check_clipboard(self):
        """Detecta texto nuevo en clipboard y dispara reglas."""
        if not self._skill_executor:
            return
        clip = self._skill_executor.get("clipboard")
        if not clip:
            return

        try:
            r = await clip.execute({"action": "read"})
            if not r.get("success"):
                return
            content = r.get("result", {}).get("content", "")
        except Exception:
            return

        if not content or not isinstance(content, str):
            return

        # Hash to dedupe
        import hashlib

        content_hash = hashlib.md5(content[:1000].encode("utf-8", errors="ignore")).hexdigest()
        if content_hash == self._last_clipboard_hash:
            return
        self._last_clipboard_hash = content_hash

        # Skip very short or huge content
        if len(content) < 4 or len(content) > 50000:
            return

        # Fire workflows for clipboard_change (independent of suggestions)
        if self._services_graph:
            try:
                await self._services_graph.fire(
                    "clipboard_change",
                    {
                        "content": content[:1000],
                        "length": len(content),
                    },
                )
            except Exception:
                pass

        # Run rules
        for rule in self._clipboard_rules:
            if rule["pattern"].search(content):
                await self._emit_suggestion(
                    source="clipboard",
                    kind="clipboard",
                    type_id=rule["title_id"],
                    title="Texto copiado",
                    message=rule["message"],
                    metadata={"preview": content[:200], "length": len(content)},
                    action=rule.get("action"),
                )
                break  # Solo una sugerencia por copia

    # ── Suggestion emission ──────────────────────────────────

    async def _emit_suggestion(
        self,
        source: str,
        kind: str,
        type_id: str,
        title: str,
        message: str,
        metadata: Dict[str, Any] = None,
        action: Optional[Dict[str, Any]] = None,
        severity: str = "info",
    ):
        """Emite una sugerencia respetando rate limits y dedup."""
        now = time.time()

        # Global rate limit
        if now - self._last_suggestion_at < GLOBAL_RATE_LIMIT_S:
            self._suppressed_count += 1
            return

        # Same-type rate limit
        last_same = self._last_by_type.get(type_id, 0.0)
        if now - last_same < SAME_TYPE_RATE_LIMIT_S:
            self._suppressed_count += 1
            return

        # Build event
        import uuid

        event = ProactiveEvent(
            id=f"prc_{uuid.uuid4().hex[:8]}",
            kind=kind,
            source=source,
            title=title,
            message=message,
            timestamp=datetime.now().isoformat(),
            severity=severity,
            suggested_action=action,
            metadata=metadata or {},
        )

        # Update state
        self._events.appendleft(event)
        self._last_suggestion_at = now
        self._last_by_type[type_id] = now
        self._suggestion_count += 1

        logger.info(f"Proactive suggestion: [{kind}/{type_id}] {message[:80]}")

        # Broadcast
        if self._broadcast:
            try:
                await self._broadcast(
                    {
                        "type": "proactive_suggestion",
                        "proactive_event": event.to_dict(),
                    }
                )
            except Exception as e:
                logger.debug(f"Broadcast error: {e}")

        # Fire workflows for proactive_event
        if self._services_graph:
            try:
                await self._services_graph.fire(
                    "proactive_event",
                    {
                        "kind": kind,
                        "source": source,
                        "title": title,
                        "message": message,
                        "metadata": {**(metadata or {}), "type_id": type_id},
                    },
                )
            except Exception:
                pass

        # Persist (throttled)
        if self._suggestion_count % 5 == 0:
            self._save_events()

    # ── Event management ─────────────────────────────────────

    def get_recent(self, n: int = 30, kind: Optional[str] = None) -> List[Dict[str, Any]]:
        """Devuelve eventos recientes, opcionalmente filtrados por tipo."""
        results = []
        for e in self._events:
            if kind and e.kind != kind:
                continue
            results.append(e.to_dict())
            if len(results) >= n:
                break
        return results

    def dismiss(self, event_id: str) -> bool:
        """Marca un evento como dismissado."""
        for e in self._events:
            if e.id == event_id:
                e.dismissed = True
                self._dismissed_count += 1
                self._save_events()
                return True
        return False

    async def execute_suggestion(self, event_id: str) -> Dict[str, Any]:
        """Ejecuta la accion sugerida de un evento."""
        for e in self._events:
            if e.id == event_id:
                if not e.suggested_action or not self._skill_executor:
                    return {"ok": False, "error": "No actionable suggestion"}
                skill = e.suggested_action.get("skill")
                inputs = e.suggested_action.get("inputs", {})
                if not skill:
                    return {"ok": False, "error": "No skill in suggestion"}
                result = await self._skill_executor.execute(skill, inputs)
                return {"ok": True, "result": result}
        return {"ok": False, "error": "Event not found"}

    # ── Stats ────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "focus_mode": self._focus_mode,
            "suggestions_emitted": self._suggestion_count,
            "suggestions_suppressed": self._suppressed_count,
            "dismissed": self._dismissed_count,
            "events_buffered": len(self._events),
            "fullscreen_silenced": self._fullscreen_silenced,
            "rules": {
                "window": len(self._window_rules),
                "clipboard": len(self._clipboard_rules),
                "custom_rules_file": str(RULES_FILE),
            },
            "config": {
                "poll_interval_s": POLL_INTERVAL,
                "global_rate_limit_s": GLOBAL_RATE_LIMIT_S,
                "same_type_rate_limit_s": SAME_TYPE_RATE_LIMIT_S,
            },
        }

    # ── Persistence ──────────────────────────────────────────

    def _load_events(self):
        if not EVENTS_FILE.exists():
            return
        try:
            data = json.loads(EVENTS_FILE.read_text(encoding="utf-8"))
            for entry in data.get("events", [])[-MAX_EVENTS:]:
                try:
                    self._events.appendleft(ProactiveEvent(**entry))
                except Exception:
                    pass
            self._suggestion_count = data.get("suggestion_count", 0)
            self._dismissed_count = data.get("dismissed_count", 0)
        except Exception as e:
            logger.debug(f"Failed to load proactive events: {e}")

    def _save_events(self):
        try:
            data = {
                "saved_at": datetime.now().isoformat(),
                "suggestion_count": self._suggestion_count,
                "dismissed_count": self._dismissed_count,
                "events": [e.to_dict() for e in list(self._events)[:50]],
            }
            EVENTS_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"Failed to save proactive events: {e}")

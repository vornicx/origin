"""
AutoContextLoader — Inyección automática de contexto en startup.

Inspirado en el "context bootstrapping" de OpenHuman. Al iniciar Origin,
ejecuta sources de contexto en paralelo y las inyecta en el MemoryTree
para que el primer reasoning loop tenga contexto fresco.

Sources built-in (auto-descubiertas, skip silenciosamente si no disponibles):

  System sources (siempre disponibles):
    today_summary       - Resumen del dia desde MemoryTree
    recent_files        - Archivos recientes del file_manager
    window_history      - Apps usadas hoy
    clipboard_history   - Ultimos textos copiados
    system_overview     - Estado del sistema (CPU/RAM/disco)
    datetime_context    - Fecha/hora actual + hora del dia

  Service sources (requieren credenciales):
    smtp_unread         - Emails sin leer (no implementado, futuro)
    spotify_now         - Que esta sonando
    telegram_pending    - Mensajes pendientes de Telegram

Cada source devuelve {ok: bool, content: str, metadata: dict, took_ms: float}.

Sources que tardan > 5s son canceladas. Sources que fallan son silenciadas.

El resultado se inyecta como un ContextBundle en MemoryTree (hora actual)
con el prefijo "[bootstrap]" para distinguirlo de la conversación.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Awaitable

logger = logging.getLogger("origin.auto_context")

# ── Config ────────────────────────────────────────────────────
SOURCE_TIMEOUT_S = 5.0  # Per-source timeout
MAX_SOURCE_CONTENT_LEN = 800  # Max chars per source in bundle
BOOTSTRAP_PREFIX = "[bootstrap]"
REFRESH_INTERVAL_S = 1800  # Re-bootstrap every 30min (optional)


@dataclass
class SourceResult:
    """Resultado de una source individual."""

    name: str
    ok: bool
    content: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    took_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContextBundle:
    """Bundle de contexto generado en una sesión de bootstrap."""

    id: str
    timestamp: str
    sources_ok: int = 0
    sources_failed: int = 0
    sources_skipped: int = 0
    total_chars: int = 0
    bundle_text: str = ""
    sources: List[Dict[str, Any]] = field(default_factory=list)
    took_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Source registry ─────────────────────────────────────────

ContextSource = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


class AutoContextLoader:
    """Carga y compone contexto automáticamente al inicio de Origin.

    Las sources son funciones async que reciben deps={skill_executor, mind, ...}
    y devuelven {ok, content, metadata}.
    """

    def __init__(self, skill_executor=None, mind=None):
        self._skill_executor = skill_executor
        self._mind = mind
        self._sources: Dict[str, ContextSource] = {}
        self._enabled: Dict[str, bool] = {}
        self._last_bundle: Optional[ContextBundle] = None
        self._bootstrap_count = 0
        self._refresh_task: Optional[asyncio.Task] = None

        self._register_default_sources()
        logger.info(
            f"AutoContextLoader initialized: {len(self._sources)} sources registered "
            f"({sum(1 for v in self._enabled.values() if v)} enabled)"
        )

    # ── Source registration ──────────────────────────────────

    def register_source(self, name: str, fn: ContextSource, enabled: bool = True):
        """Registra una nueva source de contexto."""
        self._sources[name] = fn
        self._enabled[name] = enabled

    def enable_source(self, name: str) -> bool:
        if name in self._sources:
            self._enabled[name] = True
            return True
        return False

    def disable_source(self, name: str) -> bool:
        if name in self._sources:
            self._enabled[name] = False
            return True
        return False

    def list_sources(self) -> List[Dict[str, Any]]:
        return [{"name": n, "enabled": self._enabled.get(n, False)} for n in self._sources]

    def set_dependencies(self, skill_executor=None, mind=None):
        """Inyecta dependencias post-init."""
        if skill_executor:
            self._skill_executor = skill_executor
        if mind:
            self._mind = mind

    # ── Default sources ──────────────────────────────────────

    def _register_default_sources(self):
        self.register_source("datetime_context", self._src_datetime, enabled=True)
        self.register_source("system_overview", self._src_system, enabled=True)
        self.register_source("today_summary", self._src_today_summary, enabled=True)
        self.register_source("recent_files", self._src_recent_files, enabled=True)
        self.register_source("window_history", self._src_window_history, enabled=True)
        self.register_source("clipboard_history", self._src_clipboard_history, enabled=True)
        self.register_source("spotify_now", self._src_spotify_now, enabled=True)
        self.register_source("telegram_pending", self._src_telegram_pending, enabled=True)

    async def _src_datetime(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Fecha y hora actual con contexto temporal."""
        now = datetime.now()
        hour = now.hour
        if 5 <= hour < 12:
            tod = "mañana"
        elif 12 <= hour < 18:
            tod = "tarde"
        elif 18 <= hour < 22:
            tod = "noche"
        else:
            tod = "madrugada"

        weekday = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"][now.weekday()]
        content = (
            f"Hoy es {weekday} {now.day} de {now.strftime('%B')} de {now.year}, "
            f"son las {now.strftime('%H:%M')} ({tod})."
        )
        return {"ok": True, "content": content, "metadata": {"weekday": weekday, "time_of_day": tod, "hour": hour}}

    async def _src_system(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Estado del sistema (CPU, RAM, disco)."""
        skill = self._get_skill("system_info")
        if not skill:
            return {"ok": False, "error": "system_info skill not available"}
        try:
            r = await skill.execute({"action": "overview"})
            if not r.get("success"):
                return {"ok": False, "error": "system_info failed"}
            data = r.get("result", {})
            cpu = data.get("cpu_percent", 0)
            mem = data.get("memory", {}).get("percent", 0)
            disk = data.get("disk", {}).get("percent", 0)
            content = f"Sistema: CPU {cpu}%, RAM {mem}%, Disco {disk}%."
            return {"ok": True, "content": content, "metadata": {"cpu": cpu, "memory": mem, "disk": disk}}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _src_today_summary(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Resumen del dia desde el MemoryTree."""
        if not self._mind or not getattr(self._mind, "memory_tree", None):
            return {"ok": False, "error": "memory_tree not available"}
        try:
            tree = self._mind.memory_tree
            layers = tree.get_context_layers()
            day_node = layers.get("today")
            if isinstance(day_node, dict) and day_node.get("summary"):
                return {
                    "ok": True,
                    "content": f"Hoy: {day_node['summary']}",
                    "metadata": {"messages": day_node.get("message_count", 0)},
                }
            # Fall back to recent hours
            recent_hours = layers.get("recent_hours", [])
            if recent_hours:
                last = recent_hours[0]
                return {
                    "ok": True,
                    "content": f"Ultima hora: {last.get('summary', '')[:300]}",
                    "metadata": {"hours": len(recent_hours)},
                }
            # Fallback to root identity if exists
            root = layers.get("root_identity")
            if root:
                return {"ok": True, "content": f"Identidad: {str(root)[:300]}", "metadata": {}}
            return {"ok": False, "error": "no day summary yet"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _src_recent_files(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Archivos recientes (Windows recent + file_manager)."""
        try:
            from pathlib import Path

            recent_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
            if not recent_dir.exists():
                return {"ok": False, "error": "recent dir not found"}

            files = sorted(
                [f for f in recent_dir.iterdir() if f.is_file()],
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )[:5]
            names = [f.stem for f in files]
            if not names:
                return {"ok": False, "error": "no recent files"}
            content = "Archivos recientes: " + ", ".join(names)
            return {"ok": True, "content": content, "metadata": {"count": len(names)}}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _src_window_history(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Apps mas usadas en la sesion."""
        skill = self._get_skill("active_window")
        if not skill:
            return {"ok": False, "error": "active_window not available"}
        try:
            r = await skill.execute({"action": "time_by_app"})
            if not r.get("success"):
                return {"ok": False, "error": "time_by_app failed"}
            data = r.get("result", {})
            apps = data.get("by_app", {}) if isinstance(data, dict) else {}
            if not apps:
                return {"ok": False, "error": "no app history yet"}
            # Top 3 apps by time
            sorted_apps = sorted(apps.items(), key=lambda x: x[1], reverse=True)[:3]
            parts = [f"{name} ({int(secs/60)}min)" for name, secs in sorted_apps if secs > 30]
            if not parts:
                return {"ok": False, "error": "insufficient data"}
            content = "Apps mas usadas hoy: " + ", ".join(parts)
            return {"ok": True, "content": content, "metadata": {"top_apps": [n for n, _ in sorted_apps]}}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _src_clipboard_history(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Ultimos elementos del portapapeles."""
        skill = self._get_skill("clipboard")
        if not skill:
            return {"ok": False, "error": "clipboard not available"}
        try:
            r = await skill.execute({"action": "history", "limit": 3})
            if not r.get("success"):
                return {"ok": False, "error": "history failed"}
            data = r.get("result", {})
            items = data.get("items", []) if isinstance(data, dict) else []
            if not items:
                return {"ok": False, "error": "no clipboard history"}
            # First 3 items, truncated
            previews = [str(it.get("content", ""))[:60].replace("\n", " ") for it in items[:3]]
            content = "Ultimos copiados: " + " | ".join(p for p in previews if p)
            return {"ok": True, "content": content, "metadata": {"count": len(items)}}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _src_spotify_now(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Que esta sonando en Spotify."""
        skill = self._get_skill("app_integrations")
        if not skill:
            return {"ok": False, "error": "app_integrations not available"}
        if not os.getenv("SPOTIPY_CLIENT_ID"):
            return {"ok": False, "error": "spotify not configured"}
        try:
            r = await asyncio.wait_for(
                skill.execute({"action": "spotify_now_playing"}),
                timeout=3.0,
            )
            if not r.get("success"):
                return {"ok": False, "error": "now_playing failed"}
            data = r.get("result", {})
            if not data.get("playing"):
                return {"ok": False, "error": "nothing playing"}
            track = data.get("track", "")
            artist = data.get("artist", "")
            content = f"Spotify: '{track}' de {artist}."
            return {"ok": True, "content": content, "metadata": {"track": track, "artist": artist}}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    async def _src_telegram_pending(self, deps: Dict[str, Any]) -> Dict[str, Any]:
        """Mensajes pendientes via Telegram bot."""
        skill = self._get_skill("telegram")
        if not skill:
            return {"ok": False, "error": "telegram not available"}
        try:
            r = await skill.execute({"action": "status"})
            if not r.get("success"):
                return {"ok": False, "error": "status failed"}
            st = r.get("result", {})
            if not st.get("configured") or not st.get("running"):
                return {"ok": False, "error": "telegram not active"}
            count = st.get("message_count", 0)
            last = st.get("last_message_at")
            content = f"Telegram: {count} mensajes procesados" + (f" (ultimo: {last})" if last else "")
            return {"ok": True, "content": content, "metadata": {"count": count}}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    def _get_skill(self, name: str):
        if not self._skill_executor:
            return None
        return self._skill_executor.get(name)

    # ── Bootstrap ────────────────────────────────────────────

    async def bootstrap(self, inject_into_memory: bool = True) -> ContextBundle:
        """Ejecuta todas las sources habilitadas en paralelo y compone bundle."""
        import uuid

        t0 = time.perf_counter()

        bundle = ContextBundle(
            id=f"ctx_{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now().isoformat(),
        )

        enabled = [name for name, en in self._enabled.items() if en]
        if not enabled:
            bundle.took_ms = round((time.perf_counter() - t0) * 1000, 1)
            return bundle

        # Execute in parallel with timeout
        tasks = [self._run_source(name) for name in enabled]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, Exception):
                bundle.sources_failed += 1
                continue
            if isinstance(res, SourceResult):
                bundle.sources.append(res.to_dict())
                if res.ok:
                    bundle.sources_ok += 1
                else:
                    bundle.sources_failed += 1

        # Compose bundle text
        parts = []
        for src in bundle.sources:
            if src.get("ok") and src.get("content"):
                content = src["content"][:MAX_SOURCE_CONTENT_LEN]
                parts.append(f"- {content}")
        bundle.bundle_text = "\n".join(parts)
        bundle.total_chars = len(bundle.bundle_text)

        bundle.took_ms = round((time.perf_counter() - t0) * 1000, 1)
        self._last_bundle = bundle
        self._bootstrap_count += 1

        logger.info(
            f"AutoContext bootstrap: {bundle.sources_ok}/{len(enabled)} sources OK, "
            f"{bundle.total_chars} chars, {bundle.took_ms}ms"
        )

        # Inject into memory tree as a bootstrap message
        if inject_into_memory and bundle.bundle_text and self._mind:
            try:
                tree = getattr(self._mind, "memory_tree", None)
                if tree:
                    full_msg = f"{BOOTSTRAP_PREFIX} Estado actual al iniciar sesion:\n{bundle.bundle_text}"
                    tree.ingest_message("system", full_msg)
                    logger.info("Bootstrap context injected into memory tree")
            except Exception as e:
                logger.debug(f"Memory tree injection failed: {e}")

        return bundle

    async def _run_source(self, name: str) -> SourceResult:
        """Ejecuta una source con timeout y captura de errores."""
        fn = self._sources.get(name)
        if not fn:
            return SourceResult(name=name, ok=False, error="source not found")

        t0 = time.perf_counter()
        try:
            deps = {"skill_executor": self._skill_executor, "mind": self._mind}
            result = await asyncio.wait_for(fn(deps), timeout=SOURCE_TIMEOUT_S)
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            return SourceResult(
                name=name,
                ok=bool(result.get("ok")),
                content=str(result.get("content", ""))[:MAX_SOURCE_CONTENT_LEN],
                metadata=result.get("metadata", {}),
                took_ms=elapsed,
                error=result.get("error"),
            )
        except asyncio.TimeoutError:
            return SourceResult(
                name=name,
                ok=False,
                error=f"timeout >{SOURCE_TIMEOUT_S}s",
                took_ms=SOURCE_TIMEOUT_S * 1000,
            )
        except Exception as e:
            elapsed = round((time.perf_counter() - t0) * 1000, 1)
            return SourceResult(name=name, ok=False, error=str(e)[:100], took_ms=elapsed)

    # ── Auto-refresh ─────────────────────────────────────────

    def start_periodic_refresh(self, interval_s: float = REFRESH_INTERVAL_S):
        """Inicia un loop que re-ejecuta bootstrap cada interval_s."""
        if self._refresh_task and not self._refresh_task.done():
            return
        self._refresh_task = asyncio.create_task(self._refresh_loop(interval_s))
        logger.info(f"AutoContext periodic refresh started: every {interval_s}s")

    def stop_periodic_refresh(self):
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None

    async def _refresh_loop(self, interval: float):
        # Wait once before first refresh (bootstrap already ran at startup)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return

        while True:
            try:
                await self.bootstrap(inject_into_memory=True)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Refresh bootstrap error: {e}")
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    # ── Accessors ────────────────────────────────────────────

    @property
    def last_bundle(self) -> Optional[Dict[str, Any]]:
        return self._last_bundle.to_dict() if self._last_bundle else None

    def get_formatted_context(self) -> str:
        """Devuelve el bundle formateado para inyeccion en prompt."""
        if not self._last_bundle or not self._last_bundle.bundle_text:
            return ""
        return f"[CONTEXTO DE SESION]\n{self._last_bundle.bundle_text}\n"

    @property
    def stats(self) -> Dict[str, Any]:
        last_summary: Dict[str, Any] = {}
        if self._last_bundle:
            last_summary = {
                "id": self._last_bundle.id,
                "timestamp": self._last_bundle.timestamp,
                "sources_ok": self._last_bundle.sources_ok,
                "sources_failed": self._last_bundle.sources_failed,
                "total_chars": self._last_bundle.total_chars,
                "took_ms": self._last_bundle.took_ms,
            }
        return {
            "bootstrap_count": self._bootstrap_count,
            "sources_registered": len(self._sources),
            "sources_enabled": sum(1 for v in self._enabled.values() if v),
            "last_bundle": last_summary,
            "refresh_active": self._refresh_task is not None and not self._refresh_task.done(),
        }

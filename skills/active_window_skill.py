"""
Active Window Skill — Conciencia de contexto en tiempo real.

Permite a Origin saber qué aplicación tienes en foco ahora mismo,
mantener un historial de tu actividad y reaccionar a cambios de foco.

Usado para:
  - "Origin, ¿qué estoy haciendo?" → app actual + título
  - Sugerencias contextuales según la app en uso
  - Tracking de tiempo por aplicación (productividad)
"""

import logging
import threading
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.active_window")

MAX_HISTORY = 200


def _win32_available() -> bool:
    try:
        import win32gui  # noqa: F401
        import win32process  # noqa: F401

        return True
    except Exception:
        return False


def _psutil_available() -> bool:
    try:
        import psutil  # noqa: F401

        return True
    except Exception:
        return False


class ActiveWindowSkill(BaseSkill):
    """
    Información sobre la ventana en foco actualmente.

    Acciones:
        current      → Ventana en foco (proceso, título, PID)
        list_visible → Lista todas las ventanas visibles
        track_start  → Inicia el rastreo de foco
        track_stop   → Detiene el rastreo
        history      → Historial de ventanas activas
        time_by_app  → Tiempo acumulado por aplicación en la sesión
        status       → Estado del módulo
    """

    VALID_ACTIONS = frozenset(
        {
            "current",
            "list_visible",
            "track_start",
            "track_stop",
            "history",
            "time_by_app",
            "status",
        }
    )

    def __init__(self):
        super().__init__(
            name="active_window",
            description=(
                "Conciencia de contexto: qué app/ventana tienes en foco ahora. "
                "Permite a Origin responder según lo que estés haciendo."
            ),
        )
        self._history: deque[Dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._time_by_app: Dict[str, float] = defaultdict(float)
        self._tracking = False
        self._track_thread: Optional[threading.Thread] = None
        self._last_app: Optional[str] = None
        self._last_check: float = 0.0

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida: '{action}'. Válidas: {sorted(self.VALID_ACTIONS)}"
        if not _win32_available():
            return False, "win32gui/win32process no disponibles (pip install pywin32)"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        action = inputs["action"]
        try:
            handler = getattr(self, f"_act_{action}")
            result = handler(inputs)
            self.execution_count += 1
            self.last_execution = datetime.now()
            return {
                "success": result.get("error") is None,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"ActiveWindow error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Low-level helpers ────────────────────────────────────────

    @staticmethod
    def _get_active_window() -> Dict[str, Any]:
        import win32gui
        import win32process

        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return {"empty": True}
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            process_name: Optional[str] = None
            process_path: Optional[str] = None
            if _psutil_available():
                import psutil

                try:
                    proc = psutil.Process(pid)
                    process_name = proc.name()
                    try:
                        process_path = proc.exe()
                    except Exception:
                        process_path = None
                except Exception:
                    pass

            return {
                "hwnd": hwnd,
                "title": title,
                "pid": pid,
                "process": process_name,
                "process_path": process_path,
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Action handlers ───────────────────────────────────────────

    def _act_current(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return self._get_active_window()

    def _act_list_visible(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        import win32gui

        windows: List[Dict[str, Any]] = []

        def cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title:
                return
            windows.append({"hwnd": hwnd, "title": title})

        try:
            win32gui.EnumWindows(cb, None)
        except Exception as e:
            return {"error": str(e)}

        limit = max(1, min(100, int(inputs.get("limit", 30))))
        return {"windows": windows[:limit], "total": len(windows)}

    def _act_track_start(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self._tracking:
            return {"already_tracking": True}
        self._tracking = True
        interval = max(0.5, float(inputs.get("interval", 1.0)))
        self._track_thread = threading.Thread(
            target=self._track_loop, args=(interval,), daemon=True, name="origin-active-window"
        )
        self._track_thread.start()
        self._last_check = time.time()
        logger.info(f"Active window tracking started (interval={interval}s)")
        return {"tracking": True, "interval_sec": interval}

    def _act_track_stop(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        self._tracking = False
        return {"tracking": False, "history_count": len(self._history)}

    def _act_history(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(1, min(MAX_HISTORY, int(inputs.get("limit", 20))))
        return {
            "history": list(self._history)[-limit:],
            "total": len(self._history),
        }

    def _act_time_by_app(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        sorted_apps = sorted(self._time_by_app.items(), key=lambda kv: kv[1], reverse=True)
        return {
            "apps": [
                {
                    "app": app or "unknown",
                    "seconds": round(secs, 1),
                    "minutes": round(secs / 60, 1),
                }
                for app, secs in sorted_apps[:20]
            ],
            "total_tracked_sec": round(sum(self._time_by_app.values()), 1),
        }

    def _act_status(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tracking": self._tracking,
            "history_count": len(self._history),
            "apps_tracked": len(self._time_by_app),
            "win32_available": _win32_available(),
        }

    # ── Tracking loop ────────────────────────────────────────────

    def _track_loop(self, interval: float) -> None:
        while self._tracking:
            try:
                now = time.time()
                win = self._get_active_window()
                app = win.get("process")

                # Accumulate time for the previously seen app
                if self._last_app is not None:
                    delta = now - self._last_check
                    self._time_by_app[self._last_app] += delta

                # Record entry on app change
                if app != self._last_app and not win.get("empty") and not win.get("error"):
                    entry = {
                        "process": app,
                        "title": win.get("title", "")[:200],
                        "timestamp": datetime.now().isoformat(),
                    }
                    self._history.append(entry)
                    logger.debug(f"Active app changed → {app}")

                self._last_app = app
                self._last_check = now
            except Exception as e:
                logger.debug(f"Track loop error: {e}")
            time.sleep(interval)

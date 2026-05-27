"""
Clipboard Skill — Acceso al portapapeles de Windows.

Permite a Origin leer y escribir el clipboard, mantener un historial
y reaccionar a cambios (útil para "traduce esto", "explica esto", etc.).

Backend: pywin32 (win32clipboard) — soporta texto, HTML y formato file-list.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, Optional

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.clipboard")

MAX_HISTORY = 50
MAX_TEXT_LENGTH = 100_000  # ~100KB cap for clipboard text


def _win32clipboard_available() -> bool:
    try:
        import win32clipboard  # noqa: F401

        return True
    except Exception:
        return False


class ClipboardSkill(BaseSkill):
    """
    Lee, escribe y monitoriza el portapapeles.

    Acciones:
        read         → Lee el contenido actual (texto)
        write        → Sobreescribe con texto nuevo
        clear        → Vacía el clipboard
        history      → Devuelve el historial reciente
        watch_start  → Inicia el monitor de cambios
        watch_stop   → Detiene el monitor
        status       → Estado del módulo
    """

    VALID_ACTIONS = frozenset(
        {
            "read",
            "write",
            "clear",
            "history",
            "watch_start",
            "watch_stop",
            "status",
        }
    )

    def __init__(self):
        super().__init__(
            name="clipboard",
            description=(
                "Lee/escribe el portapapeles de Windows y mantiene historial. "
                "Útil para 'traduce esto', 'explica el código que copié', etc."
            ),
        )
        self._history: deque[Dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self._watching = False
        self._watch_thread: Optional[threading.Thread] = None
        self._last_seen: Optional[str] = None

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida: '{action}'. Válidas: {sorted(self.VALID_ACTIONS)}"

        if not _win32clipboard_available():
            return False, "win32clipboard no disponible (pip install pywin32)"

        if action == "write" and not inputs.get("text"):
            return False, "write requiere 'text'"

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
            logger.error(f"Clipboard error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Low-level clipboard helpers ───────────────────────────────

    @staticmethod
    def _read_text() -> Optional[str]:
        """Read clipboard text. Returns None if empty or non-text."""
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                # Try Unicode text first
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                    return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
                # Fallback to ANSI text
                if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_TEXT):
                    raw = win32clipboard.GetClipboardData(win32clipboard.CF_TEXT)
                    if isinstance(raw, bytes):
                        return raw.decode("utf-8", errors="replace")
                    return str(raw)
                return None
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:
            logger.warning(f"Read clipboard failed: {e}")
            return None

    @staticmethod
    def _write_text(text: str) -> bool:
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception as e:
            logger.warning(f"Write clipboard failed: {e}")
            return False

    @staticmethod
    def _clear() -> bool:
        import win32clipboard

        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                return True
            finally:
                win32clipboard.CloseClipboard()
        except Exception:
            return False

    @staticmethod
    def _detect_kind(text: str) -> str:
        """Quick heuristic classification of clipboard content."""
        if not text:
            return "empty"
        stripped = text.strip()
        lower = stripped.lower()
        if lower.startswith(("http://", "https://", "ftp://", "file://")):
            return "url"
        if "\n" in stripped and any(
            kw in stripped for kw in ("def ", "function ", "class ", "import ", "const ", "var ", "return", "</", "{")
        ):
            return "code"
        if "@" in stripped and "." in stripped and len(stripped.split("@")) == 2 and " " not in stripped:
            return "email"
        if stripped.count("/") == 2 and any(c.isdigit() for c in stripped) and len(stripped) < 20:
            return "date"
        if "\n" in stripped:
            return "multiline_text"
        return "text"

    # ── Action handlers ───────────────────────────────────────────

    def _act_read(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = self._read_text()
        if text is None:
            return {"empty": True, "text": ""}

        truncated = text[:MAX_TEXT_LENGTH]
        return {
            "text": truncated,
            "length": len(text),
            "kind": self._detect_kind(text),
            "lines": text.count("\n") + 1,
            "truncated": len(text) > MAX_TEXT_LENGTH,
        }

    def _act_write(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = str(inputs["text"])[:MAX_TEXT_LENGTH]
        ok = self._write_text(text)
        if ok:
            self._record_entry(text, source="write")
            return {"written": True, "length": len(text)}
        return {"error": "Falló al escribir en clipboard"}

    def _act_clear(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {"cleared": self._clear()}

    def _act_history(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        limit = max(1, min(MAX_HISTORY, int(inputs.get("limit", 10))))
        return {
            "history": list(self._history)[-limit:],
            "total": len(self._history),
        }

    def _act_watch_start(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if self._watching:
            return {"already_watching": True}
        self._watching = True
        interval = max(0.2, float(inputs.get("interval", 0.5)))
        self._watch_thread = threading.Thread(
            target=self._watch_loop, args=(interval,), daemon=True, name="origin-clipboard"
        )
        self._watch_thread.start()
        logger.info(f"Clipboard watch started (interval={interval}s)")
        return {"watching": True, "interval_sec": interval}

    def _act_watch_stop(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        self._watching = False
        return {"watching": False}

    def _act_status(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "watching": self._watching,
            "history_size": len(self._history),
            "win32clipboard": _win32clipboard_available(),
        }

    # ── Watch loop ───────────────────────────────────────────────

    def _watch_loop(self, interval: float) -> None:
        while self._watching:
            try:
                text = self._read_text()
                if text and text != self._last_seen:
                    self._last_seen = text
                    self._record_entry(text, source="watch")
            except Exception as e:
                logger.debug(f"Clipboard watch tick error: {e}")
            time.sleep(interval)

    def _record_entry(self, text: str, source: str = "manual") -> None:
        preview = text[:300]
        entry = {
            "preview": preview,
            "length": len(text),
            "kind": self._detect_kind(text),
            "timestamp": datetime.now().isoformat(),
            "source": source,
        }
        # Avoid duplicates from same source
        if self._history and self._history[-1]["preview"] == preview:
            return
        self._history.append(entry)

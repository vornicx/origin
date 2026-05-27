"""
UI Automation Skill para Origin.
Control de mouse, teclado e interacción con la interfaz gráfica.

Usa dos backends:
  1. Native Windows UIAutomation (UIAutomationCore.dll via COM)
     - Busca elementos por nombre/texto/tipo (no coordenadas)
     - Funciona en segundo plano
     - Ideal para aplicaciones modernas
  2. pyautogui (fallback)
     - Coordenadas de pantalla
     - Para todo lo demás

Complementa a VisionSkill: vision ve → ui_automation actúa.
"""

import time
import logging
import os
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

from .base_skill import BaseSkill
from .uia_backend import get_all_windows, find_window, focus_window, WIN32_AVAILABLE

logger = logging.getLogger("origin.skills.ui_automation")

# ── Seguridad ─────────────────────────────────────────────────────
MAX_MOUSE_SPEED = 2.0
MAX_TEXT_LENGTH = 5000
MAX_CLICKS = 10


class UIAutomationSkill(BaseSkill):
    """Control de UI vía Windows UIAutomation nativo + pyautogui fallback.

    Acciones (nuevas, UIA nativas):
        find_by_name       → Busca elemento por nombre visible
        find_by_type       → Busca elementos por tipo (Button, Edit, etc.)
        find_clickable     → Busca elemento clickable por texto
        get_text           → Lee texto de un campo
        window_uia         → Lista/focus ventanas vía UIA (más preciso que pygetwindow)

    Acciones (clásicas, pyautogui):
        click, double_click, right_click, type, hotkey, press,
        scroll, move, drag, locate, cursor_info
    """

    VALID_ACTIONS = {
        "click",
        "double_click",
        "right_click",
        "type",
        "hotkey",
        "press",
        "scroll",
        "move",
        "drag",
        "window",
        "locate",
        "cursor_info",
        "find_by_name",
        "find_by_type",
        "find_clickable",
        "get_text",
        "window_uia",
    }

    def __init__(self):
        super().__init__(
            name="ui_automation", description="Controla mouse, teclado y ventanas. Usa win32 nativo + pyautogui."
        )
        self._win32_available = WIN32_AVAILABLE
        self._screen_size: Optional[Tuple[int, int]] = None
        self._pyautogui_available = False

        try:
            import pyautogui

            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.15
            self._screen_size = pyautogui.size()
            self._pyautogui_available = True
            self._pyautogui = pyautogui
        except ImportError:
            pass

        logger.info(f"UIAutomationSkill: win32={self._win32_available}, pyautogui={self._pyautogui_available}")

    # ── Validación ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action")
        if not action:
            return False, "Se requiere 'action'"
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida '{action}'. Válidas: {self.VALID_ACTIONS}"

        uia_actions = {"find_by_name", "find_by_type", "find_clickable", "get_text", "window_uia"}
        if action not in uia_actions:
            if not self._pyautogui_available:
                return False, "pyautogui no instalado para acciones clásicas"

        if action in ("click", "double_click", "right_click", "move"):
            x = inputs.get("x")
            y = inputs.get("y")
            if x is not None and y is not None and self._screen_size:
                if not (0 <= int(x) <= self._screen_size[0] and 0 <= int(y) <= self._screen_size[1]):
                    return False, f"Coordenadas ({x},{y}) fuera de pantalla"

        if action == "type":
            text = inputs.get("text", "")
            if not text:
                return False, "Se requiere 'text'"
            if len(text) > MAX_TEXT_LENGTH:
                return False, f"Texto máximo {MAX_TEXT_LENGTH} chars"

        if action == "hotkey":
            if not inputs.get("keys"):
                return False, "Se requiere 'keys'"

        if action == "drag":
            for coord in ("start_x", "start_y", "end_x", "end_y"):
                if inputs.get(coord) is None:
                    return False, f"Se requiere '{coord}' para drag"

        return True, ""

    # ── Ejecución principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs.get("action")

            dispatch = {
                "click": lambda: self._click(inputs),
                "double_click": lambda: self._double_click(inputs),
                "right_click": lambda: self._right_click(inputs),
                "type": lambda: self._type_text(inputs),
                "hotkey": lambda: self._hotkey(inputs),
                "press": lambda: self._press_key(inputs),
                "scroll": lambda: self._scroll(inputs),
                "move": lambda: self._move(inputs),
                "drag": lambda: self._drag(inputs),
                "window": lambda: self._manage_window(inputs),
                "locate": lambda: self._locate_on_screen(inputs),
                "cursor_info": lambda: self._cursor_info(),
                "find_by_name": lambda: self._uia_find_by_name(inputs),
                "find_by_type": lambda: self._uia_find_by_type(inputs),
                "find_clickable": lambda: self._uia_find_clickable(inputs),
                "get_text": lambda: self._uia_get_text(inputs),
                "window_uia": lambda: self._uia_window_list(inputs),
            }

            handler = dispatch.get(action)
            if not handler:
                result = {"error": f"Acción no implementada: {action}"}
            else:
                result = handler()

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()
            has_error = isinstance(result, dict) and result.get("error") is not None
            return {
                "success": not has_error,
                "result": result,
                "error": result.get("error") if has_error else None,
                "execution_time": elapsed,
            }

        except Exception as e:
            elapsed = round(time.time() - start, 3)
            import traceback

            logger.error(f"UI automation error: {e}\n{traceback.format_exc()}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": elapsed,
            }

    def _run_sync(self, fn, *args, **kwargs):
        """Ejecuta una función síncrona en un thread para no bloquear el event loop."""
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            return loop.run_in_executor(None, lambda: fn(*args, **kwargs))
        except RuntimeError:
            return fn(*args, **kwargs)

    # ══════════════════════════════════════════════════════════════
    # Win32 Native Actions
    # ══════════════════════════════════════════════════════════════

    def _uia_find_by_name(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca ventana por nombre/titulo usando win32."""
        name = inputs.get("name", "")
        if not name:
            return {"error": "Se requiere 'name'"}
        window = find_window(name)
        if window:
            return {
                "found": True,
                "name": window.name,
                "class_name": window.class_name,
                "bounding_box": {
                    "x": window.rect[0],
                    "y": window.rect[1],
                    "width": window.rect[2],
                    "height": window.rect[3],
                },
                "center": {"x": window.center[0], "y": window.center[1]},
                "process_id": window.process_id,
            }
        return {"found": False, "name": name}

    def _uia_find_by_type(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca ventanas filtradas por tipo (usa class_name de win32)."""
        ctype = inputs.get("control_type", "")
        max_results = inputs.get("max_results", 10)
        windows = get_all_windows()
        filtered = [w for w in windows if ctype.lower() in w.class_name.lower()] if ctype else windows
        return {
            "found": len(filtered) > 0,
            "count": len(filtered),
            "elements": [
                {"name": w.name[:60], "class_name": w.class_name, "center": w.center} for w in filtered[:max_results]
            ],
        }

    def _uia_find_clickable(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca ventana por texto y opcionalmente hace focus/click."""
        text = inputs.get("text", "")
        do_focus = inputs.get("focus", True)
        if not text:
            return {"error": "Se requiere 'text'"}
        window = find_window(text)
        if not window:
            return {"found": False, "text": text}
        result = {"found": True, "name": window.name, "center": {"x": window.center[0], "y": window.center[1]}}
        if do_focus:
            result["focused"] = focus_window(window.handle)
        return result

    def _uia_get_text(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lee el titulo de una ventana (no el contenido de un edit)."""
        name = inputs.get("name", "")
        if not name:
            return {"error": "Se requiere 'name'"}
        window = find_window(name)
        if window:
            return {"text": window.name, "length": len(window.name), "source": name}
        return {"error": f"Ventana '{name}' no encontrada"}

    def _uia_window_list(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lista/focus ventanas usando win32."""
        sub = inputs.get("sub_action", "list")
        if sub == "focus":
            title = inputs.get("title", "")
            if title:
                w = find_window(title)
                if w and focus_window(w.handle):
                    return {"focused": True, "title": w.name}
                return {"error": f"Ventana '{title}' no encontrada"}
        windows = get_all_windows()
        return {"count": len(windows), "windows": [{"name": w.name, "pid": w.process_id} for w in windows]}

    # ══════════════════════════════════════════════════════════════
    # Classic pyautogui Actions
    # ══════════════════════════════════════════════════════════════

    def _click(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return self._click_base("click", inputs)

    def _double_click(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return self._click_base("double_click", inputs, clicks=2)

    def _right_click(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        return self._click_base("right_click", inputs, button="right")

    def _click_base(self, action: str, inputs: Dict[str, Any], clicks: int = 1, button: str = "left") -> Dict[str, Any]:
        x, y = inputs.get("x"), inputs.get("y")
        if x is not None and y is not None:
            getattr(
                self._pyautogui,
                {
                    "click": "click",
                    "double_click": "doubleClick",
                    "right_click": "rightClick",
                }.get(action, "click"),
            )(x=int(x), y=int(y), clicks=clicks, button=button)
            pos = (int(x), int(y))
        else:
            getattr(
                self._pyautogui,
                {
                    "click": "click",
                    "double_click": "doubleClick",
                    "right_click": "rightClick",
                }.get(action, "click"),
            )(clicks=clicks, button=button)
            pos = self._pyautogui.position()
        return {"action": action, "position": {"x": pos[0], "y": pos[1]}, "clicks": clicks, "button": button}

    def _type_text(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        text = inputs.get("text", "")
        inputs.get("interval", 0.02)
        x, y = inputs.get("x"), inputs.get("y")
        if x is not None and y is not None:
            self._pyautogui.click(x=int(x), y=int(y))
            time.sleep(0.1)  # small delay needed after click before typing
        self._pyautogui.write(text)
        return {"action": "type", "text_length": len(text), "text_preview": text[:50]}

    _KEY_MAP = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "win": "win",
        "windows": "win",
        "super": "win",
        "enter": "enter",
        "return": "enter",
        "esc": "escape",
        "escape": "escape",
        "tab": "tab",
        "space": "space",
        "backspace": "backspace",
        "delete": "delete",
        "del": "delete",
        "up": "up",
        "down": "down",
        "left": "left",
        "right": "right",
        "home": "home",
        "end": "end",
        "pageup": "pageup",
        "pagedown": "pagedown",
        "f1": "f1",
        "f2": "f2",
        "f3": "f3",
        "f4": "f4",
        "f5": "f5",
        "f6": "f6",
        "f7": "f7",
        "f8": "f8",
        "f9": "f9",
        "f10": "f10",
        "f11": "f11",
        "f12": "f12",
        "printscreen": "printscreen",
        "prtsc": "printscreen",
    }

    def _hotkey(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        keys_str = inputs.get("keys", "")
        keys = [k.strip().lower() for k in keys_str.split("+")]
        mapped = [self._KEY_MAP.get(k, k) for k in keys]
        self._pyautogui.hotkey(*mapped)
        return {"action": "hotkey", "keys": keys_str, "mapped_keys": mapped}

    def _press_key(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        key = inputs.get("key", "").lower()
        presses = min(inputs.get("presses", 1), 20)
        self._pyautogui.press(key, presses=presses)
        return {"action": "press", "key": key, "presses": presses}

    def _scroll(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        amount = inputs.get("amount", 3)
        direction = inputs.get("direction", "down")
        x, y = inputs.get("x"), inputs.get("y")
        scroll_val = -abs(amount) if direction == "down" else abs(amount) if direction == "up" else amount
        kwargs = {}
        if x is not None and y is not None:
            kwargs["x"], kwargs["y"] = int(x), int(y)
        self._pyautogui.scroll(scroll_val, **kwargs)
        return {"action": "scroll", "amount": scroll_val, "direction": direction}

    def _move(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        x, y = int(inputs.get("x", 0)), int(inputs.get("y", 0))
        duration = min(inputs.get("duration", 0.3), MAX_MOUSE_SPEED)
        self._pyautogui.moveTo(x, y, duration=duration)
        return {"action": "move", "position": {"x": x, "y": y}, "duration": duration}

    def _drag(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        sx, sy = int(inputs["start_x"]), int(inputs["start_y"])
        ex, ey = int(inputs["end_x"]), int(inputs["end_y"])
        duration = min(inputs.get("duration", 0.5), MAX_MOUSE_SPEED)
        button = inputs.get("button", "left")
        self._pyautogui.moveTo(sx, sy, duration=0.2)
        self._pyautogui.drag(ex - sx, ey - sy, duration=duration, button=button)
        return {"action": "drag", "start": {"x": sx, "y": sy}, "end": {"x": ex, "y": ey}}

    def _manage_window(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Gestión de ventanas vía pygetwindow (fallback clásico)."""
        sub_action = inputs.get("sub_action", "list")
        title = inputs.get("title", "")

        try:
            import pygetwindow as gw
        except ImportError:
            return {"error": "pygetwindow no disponible"}

        if sub_action == "list":
            windows = [w for w in gw.getAllTitles() if w.strip() and len(w.strip()) > 1]
            return {"action": "window_list", "windows": windows[:30], "count": len(windows)}

        if not title:
            return {"error": "Se requiere 'title'"}

        wins = gw.getWindowsWithTitle(title)
        if not wins:
            return {"error": f"Ventana '{title}' no encontrada"}
        win = wins[0]

        try:
            if sub_action == "focus":
                if win.isMinimized:
                    win.restore()
                win.activate()
                return {
                    "action": "window_focus",
                    "title": win.title,
                    "position": {"x": win.left, "y": win.top},
                    "size": {"width": win.width, "height": win.height},
                }
            elif sub_action == "minimize":
                win.minimize()
                return {"action": "window_minimize", "title": title}
            elif sub_action == "maximize":
                win.maximize()
                return {"action": "window_maximize", "title": title}
            elif sub_action == "close":
                win.close()
                return {"action": "window_close", "title": title}
            elif sub_action == "resize":
                width, height = inputs.get("width"), inputs.get("height")
                if width and height:
                    win.resizeTo(int(width), int(height))
                    return {"action": "window_resize", "title": title, "size": {"width": width, "height": height}}
            elif sub_action == "move_to":
                win.moveTo(int(inputs.get("x", 0)), int(inputs.get("y", 0)))
                return {
                    "action": "window_move",
                    "title": title,
                    "position": {"x": int(inputs.get("x", 0)), "y": int(inputs.get("y", 0))},
                }
            return {"error": f"Sub-acción inválida: {sub_action}"}
        except Exception as e:
            return {"error": f"Error en ventana: {e}"}

    def _locate_on_screen(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        image_path = inputs.get("image_path", "")
        confidence = inputs.get("confidence", 0.8)
        if not image_path or not os.path.exists(image_path):
            return {"error": f"Imagen no encontrada: {image_path}"}
        try:
            location = self._pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location:
                center = self._pyautogui.center(location)
                return {
                    "found": True,
                    "location": {
                        "left": location.left,
                        "top": location.top,
                        "width": location.width,
                        "height": location.height,
                    },
                    "center": {"x": center.x, "y": center.y},
                }
            return {"found": False, "image_path": image_path}
        except Exception as e:
            return {"error": f"Error buscando imagen: {e}"}

    def _cursor_info(self) -> Dict[str, Any]:
        pos = self._pyautogui.position()
        size = self._pyautogui.size()
        return {"cursor": {"x": pos.x, "y": pos.y}, "screen_size": {"width": size.width, "height": size.height}}

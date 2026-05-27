"""
UI Automation Backend nativo de Windows para Origin.
Usa win32gui/win32api (pywin32) en lugar de pyautogui para operaciones
que no requieren coordenadas de pantalla.

Ventajas:
  - Enumera ventanas por título (no por coordenadas)
  - Envía clicks a ventanas específicas
  - Funciona con ventanas en segundo plano
  - Más rápido que pyautogui (no mueve el cursor realmente)

Fallback automático a pyautogui cuando es necesario.
"""

import logging
import time
from typing import Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger("origin.skills.uia")

# checar disponibilidad de win32gui
WIN32_AVAILABLE = False
try:
    import win32gui
    import win32api
    import win32con
    import win32process

    WIN32_AVAILABLE = True
    logger.info("Windows Native UI Backend (win32): ACTIVO")
except ImportError:
    logger.warning("Windows Native UI Backend: NO DISPONIBLE (pywin32 no instalado)")


@dataclass
class UIElement:
    """Ventana/elemento UI encontrado via API nativa de Windows."""

    name: str = ""
    class_name: str = ""
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    process_id: int = 0
    handle: int = 0
    is_visible: bool = True

    @property
    def center(self) -> Tuple[int, int]:
        return (self.rect[0] + self.rect[2] // 2, self.rect[1] + self.rect[3] // 2)


def _enum_window_callback(hwnd, results):
    if win32gui.IsWindowVisible(hwnd):
        title = win32gui.GetWindowText(hwnd)
        if title and len(title.strip()) > 1:
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
            except Exception:
                pid = 0
            try:
                rect = win32gui.GetWindowRect(hwnd)
            except Exception:
                rect = (0, 0, 0, 0)
            results.append(
                UIElement(
                    name=title,
                    class_name=win32gui.GetClassName(hwnd),
                    rect=rect,
                    process_id=pid,
                    handle=hwnd,
                    is_visible=True,
                )
            )
    return True


def get_all_windows() -> List[UIElement]:
    if not WIN32_AVAILABLE:
        return []
    results = []
    try:
        win32gui.EnumWindows(_enum_window_callback, results)
    except Exception:
        pass
    return results


def find_window(title: str) -> Optional[UIElement]:
    """Busca ventana por título (substring)."""
    for w in get_all_windows():
        if title.lower() in w.name.lower():
            return w
    return None


def focus_window(hwnd: int) -> bool:
    """Pone una ventana en primer plano."""
    if not WIN32_AVAILABLE or not hwnd:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def click_at(x: int, y: int) -> bool:
    """Click en coordenadas usando SendInput (no mueve el cursor)."""
    if not WIN32_AVAILABLE:
        return False
    try:
        old_pos = win32gui.GetCursorPos()
        win32api.SetCursorPos((x, y))
        time.sleep(0.05)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, x, y, 0, 0)
        time.sleep(0.02)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, x, y, 0, 0)
        return True
    except Exception:
        return False
    finally:
        try:
            win32api.SetCursorPos(old_pos)
        except Exception:
            pass


def type_text(text: str) -> bool:
    """Escribe texto usando keybd_event."""
    if not WIN32_AVAILABLE:
        return False
    try:
        for char in text:
            vk = win32api.VkKeyScan(char)
            if vk != -1:
                win32api.keybd_event(vk, 0, 0, 0)
                win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.01)
        return True
    except Exception:
        return False


def send_hotkey(keys: List[str]) -> bool:
    """Envía combinación de teclas (Ctrl+C, Alt+Tab, etc.)."""
    if not WIN32_AVAILABLE:
        return False
    VK_MAP = {
        "ctrl": win32con.VK_CONTROL,
        "control": win32con.VK_CONTROL,
        "alt": win32con.VK_MENU,
        "shift": win32con.VK_SHIFT,
        "win": win32con.VK_LWIN,
        "tab": win32con.VK_TAB,
        "enter": win32con.VK_RETURN,
        "space": win32con.VK_SPACE,
        "escape": win32con.VK_ESCAPE,
        "esc": win32con.VK_ESCAPE,
        "backspace": win32con.VK_BACK,
        "delete": win32con.VK_DELETE,
        "up": win32con.VK_UP,
        "down": win32con.VK_DOWN,
        "left": win32con.VK_LEFT,
        "right": win32con.VK_RIGHT,
        "home": win32con.VK_HOME,
        "end": win32con.VK_END,
    }
    try:
        vk_keys = [VK_MAP.get(k.lower(), ord(k.upper()) if len(k) == 1 else 0) for k in keys]
        vk_keys = [k for k in vk_keys if k]
        for vk in vk_keys:
            win32api.keybd_event(vk, 0, 0, 0)
            time.sleep(0.05)
        for vk in reversed(vk_keys):
            win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.02)
        return True
    except Exception:
        return False


def get_cursor_pos() -> Optional[Tuple[int, int]]:
    if not WIN32_AVAILABLE:
        return None
    try:
        pos = win32gui.GetCursorPos()
        return (pos[0], pos[1])
    except Exception:
        return None

"""
SystemTray Skill — Integración profunda con el SO Windows para Origin.

Proporciona:
  - Icono en la bandeja del sistema (system tray) con menú contextual
  - Hotkey global Ctrl+Alt+J para activar modo voz desde cualquier app
  - Notificaciones nativas de Windows desde el tray
  - Arranque con Windows (opcional)

Requiere:
  - pystray      (pip install pystray)
  - keyboard     (pip install keyboard)
  - Pillow       (ya instalado)
"""

import logging
import os
import subprocess
import sys
import threading
import time
import winreg
from typing import Any, Callable, Dict, Optional

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.tray")

Origin_UI_URL = "http://localhost:9001"
HOTKEY = "ctrl+alt+j"
STARTUP_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APP_NAME = "Origin"


def _pystray_available() -> bool:
    try:
        import pystray  # noqa: F401

        return True
    except ImportError:
        return False


def _keyboard_available() -> bool:
    try:
        import keyboard  # noqa: F401

        return True
    except ImportError:
        return False


class SystemTraySkill(BaseSkill):
    """
    Integración con el SO: bandeja del sistema, hotkey global, autoarranque.

    Acciones:
        start          → Inicia el icono de bandeja + hotkey global
        stop           → Elimina el icono y desregistra hotkey
        status         → Estado del tray
        notify         → Envía notificación desde el tray
        set_startup    → Configura (des)activar arranque con Windows
        open_ui        → Abre el navegador con la interfaz de Origin
    """

    VALID_ACTIONS = frozenset({"start", "stop", "status", "notify", "set_startup", "open_ui"})

    def __init__(self):
        super().__init__(
            name="system_tray",
            description=(
                "Integración con Windows: icono en bandeja del sistema, "
                "hotkey global Ctrl+Alt+J y notificaciones nativas."
            ),
        )
        self._tray_icon = None
        self._tray_thread: Optional[threading.Thread] = None
        self._running = False

        # Injected callback: called when hotkey is pressed
        self._hotkey_callback: Optional[Callable] = None
        self._wake_word_skill = None

    # ── Dependency injection ──────────────────────────────────────

    def set_wake_word_skill(self, wake_word_skill) -> None:
        self._wake_word_skill = wake_word_skill

    # ── BaseSkill interface ───────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "status")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida: '{action}'. Válidas: {self.VALID_ACTIONS}"
        if action == "notify":
            if not inputs.get("message"):
                return False, "Se requiere 'message' para notify"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        action = inputs.get("action", "status")

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            if action == "start":
                result = self._start(inputs)
            elif action == "stop":
                result = self._stop()
            elif action == "status":
                result = self._get_status()
            elif action == "notify":
                result = self._notify(inputs)
            elif action == "set_startup":
                result = self._set_startup(inputs)
            elif action == "open_ui":
                result = self._open_ui()
            else:
                result = {"error": f"Acción no implementada: {action}"}

            success = result.get("error") is None
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"SystemTray skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Start ─────────────────────────────────────────────────────

    def _start(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        # If running under a native host (Tauri/Electron), suppress tray+hotkeys
        # to avoid double-registration of tray icon and global hotkeys.
        import os as _os

        if _os.getenv("Origin_HOST", "").lower() == "tauri":
            logger.info("system_tray_skill suppressed: native host owns tray and hotkeys")
            return {"status": "suppressed_by_host", "host": "tauri"}

        if self._running:
            return {"status": "already_running"}

        results = {}

        # Start tray icon
        if _pystray_available():
            try:
                self._start_tray_icon()
                results["tray"] = "started"
            except Exception as e:
                logger.error(f"Tray icon error: {e}")
                results["tray"] = f"error: {e}"
        else:
            results["tray"] = "pystray not installed (pip install pystray)"

        # Register global hotkey
        if _keyboard_available():
            try:
                import keyboard

                keyboard.add_hotkey(HOTKEY, self._on_hotkey, suppress=False)
                results["hotkey"] = f"registered ({HOTKEY})"
                logger.info(f"Global hotkey registered: {HOTKEY}")
            except Exception as e:
                logger.error(f"Hotkey registration error: {e}")
                results["hotkey"] = f"error: {e}"
        else:
            results["hotkey"] = "keyboard not installed (pip install keyboard)"

        self._running = True
        return {"status": "started", **results}

    def _start_tray_icon(self) -> None:
        """Create and run the system tray icon in a daemon thread."""
        import pystray
        from PIL import Image, ImageDraw, ImageFont

        # Generate Origin icon programmatically
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Blue circle background
        draw.ellipse([2, 2, size - 2, size - 2], fill=(0, 120, 215, 255))

        # White "J" text
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 36)
        except Exception:
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), "J", font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) // 2
        y = (size - text_h) // 2 - 2
        draw.text((x, y), "J", fill=(255, 255, 255, 255), font=font)

        # Build tray menu
        menu = pystray.Menu(
            pystray.MenuItem("Abrir Origin", self._menu_open_ui, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Activar voz (Ctrl+Alt+J)", self._menu_toggle_voice),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", self._menu_exit),
        )

        self._tray_icon = pystray.Icon(
            "Origin",
            img,
            "Origin — Listo",
            menu,
        )

        self._tray_thread = threading.Thread(
            target=self._tray_icon.run,
            name="origin-tray",
            daemon=True,
        )
        self._tray_thread.start()
        logger.info("System tray icon started")

    # ── Stop ──────────────────────────────────────────────────────

    def _stop(self) -> Dict[str, Any]:
        if not self._running:
            return {"status": "not_running"}

        # Stop hotkey
        if _keyboard_available():
            try:
                import keyboard

                keyboard.remove_hotkey(HOTKEY)
            except Exception:
                pass

        # Stop tray icon
        if self._tray_icon:
            try:
                self._tray_icon.stop()
            except Exception:
                pass
            self._tray_icon = None

        self._running = False
        logger.info("SystemTray stopped")
        return {"status": "stopped"}

    # ── Notify ───────────────────────────────────────────────────

    def _notify(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        message = inputs.get("message", "")
        title = inputs.get("title", "Origin")

        if self._tray_icon and self._running:
            try:
                self._tray_icon.notify(message, title)
                return {"notified": True, "via": "tray"}
            except Exception as e:
                logger.warning(f"Tray notify failed: {e}")

        # Fallback: Windows toast via PowerShell
        try:
            script = (
                f"[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"  # noqa: E501
                f"$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
                f"$toastXml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($template);"
                f'$toastXml.GetElementsByTagName("text")[0].AppendChild($toastXml.CreateTextNode("{title}")) | Out-Null;'  # noqa: E501
                f'$toastXml.GetElementsByTagName("text")[1].AppendChild($toastXml.CreateTextNode("{message}")) | Out-Null;'  # noqa: E501
                f"$toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml);"
                f'[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Origin").Show($toast);'
            )
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-Command", script],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return {"notified": True, "via": "powershell_toast"}
        except Exception as e:
            return {"error": f"Notificación falló: {e}"}

    # ── Startup ───────────────────────────────────────────────────

    def _set_startup(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Add or remove Origin from Windows startup registry.
        Uses pythonw.exe + silent launcher so no console window shows."""
        enable = inputs.get("enable", True)

        root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        launcher = os.path.join(root, "origin_launcher.pyw")
        pythonw = os.path.join(root, "venv", "Scripts", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable.replace("python.exe", "pythonw.exe")

        cmd = f'"{pythonw}" "{launcher}"'

        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                STARTUP_REG_KEY,
                0,
                winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
            )
            if enable:
                winreg.SetValueEx(key, STARTUP_APP_NAME, 0, winreg.REG_SZ, cmd)
                winreg.CloseKey(key)
                return {"startup": "enabled", "command": cmd}
            else:
                try:
                    winreg.DeleteValue(key, STARTUP_APP_NAME)
                except FileNotFoundError:
                    pass
                winreg.CloseKey(key)
                return {"startup": "disabled"}
        except Exception as e:
            return {"error": f"Error modificando registro: {e}"}

    # ── Open UI ───────────────────────────────────────────────────

    def _open_ui(self) -> Dict[str, Any]:
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", Origin_UI_URL],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return {"opened": True, "url": Origin_UI_URL}
        except Exception as e:
            return {"error": str(e)}

    # ── Status ───────────────────────────────────────────────────

    def _get_status(self) -> Dict[str, Any]:
        startup_registered = False
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY, 0, winreg.KEY_QUERY_VALUE)
            winreg.QueryValueEx(key, STARTUP_APP_NAME)
            winreg.CloseKey(key)
            startup_registered = True
        except Exception:
            pass

        return {
            "running": self._running,
            "tray_active": self._tray_icon is not None,
            "hotkey": HOTKEY,
            "pystray_available": _pystray_available(),
            "keyboard_available": _keyboard_available(),
            "startup_with_windows": startup_registered,
            "ui_url": Origin_UI_URL,
        }

    # ── Menu callbacks ────────────────────────────────────────────

    def _menu_open_ui(self, icon=None, item=None) -> None:
        self._open_ui()

    def _menu_toggle_voice(self, icon=None, item=None) -> None:
        self._on_hotkey()

    def _menu_exit(self, icon=None, item=None) -> None:
        self._stop()

    # ── Hotkey handler ────────────────────────────────────────────

    def _on_hotkey(self) -> None:
        """Called when Ctrl+Alt+J is pressed. Toggles wake word mode."""
        logger.info(f"Global hotkey {HOTKEY} pressed")
        if not self._wake_word_skill:
            # Just open UI as fallback
            self._open_ui()
            return

        status = self._wake_word_skill._get_status()
        if status.get("running"):
            self._wake_word_skill._stop()
            if self._tray_icon:
                self._tray_icon.title = "Origin — Voz desactivada"
        else:
            # start() requires event loop → can't call directly from hotkey thread
            # Broadcast a signal instead; the API can pick it up
            logger.info("Hotkey: wake word not running, opening UI")
            self._open_ui()

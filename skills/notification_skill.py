"""
NotificationSkill — Notificaciones de escritorio, sonidos y alertas.

Acciones:
  toast    → Notificación toast de Windows (título, mensaje, icono)
  sound    → Reproduce un sonido del sistema o beep personalizado
  alert    → Toast + sonido combinados (para alertas urgentes)
  history  → Muestra historial reciente de notificaciones enviadas
"""

import logging
import time
import threading
from typing import Dict, Any
from datetime import datetime
from collections import deque

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")

# ── Sound presets (frequency_hz, duration_ms) ────────────────
SOUND_PRESETS = {
    "info": [(600, 200)],
    "success": [(523, 150), (659, 150), (784, 200)],
    "warning": [(440, 300), (440, 300)],
    "error": [(200, 500)],
    "alert": [(880, 150), (0, 100), (880, 150), (0, 100), (880, 200)],
    "startup": [(523, 100), (587, 100), (659, 100), (784, 200)],
    "beep": [(800, 200)],
}


class NotificationSkill(BaseSkill):
    """Envía notificaciones de escritorio y sonidos del sistema."""

    def __init__(self):
        super().__init__(
            name="notification", description="Envía notificaciones toast de Windows, reproduce sonidos y alertas"
        )
        self._history: deque = deque(maxlen=50)

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid_actions = ("toast", "sound", "alert", "history")
        if action not in valid_actions:
            return False, f"Acción inválida: '{action}'. Válidas: {valid_actions}"

        if action == "toast":
            if not inputs.get("title") and not inputs.get("message"):
                return False, "Se requiere 'title' o 'message' para toast"

        if action == "sound":
            preset = inputs.get("preset", "beep")
            if preset not in SOUND_PRESETS and preset != "custom":
                return False, f"Preset inválido: '{preset}'. Válidos: {list(SOUND_PRESETS.keys()) + ['custom']}"

        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs["action"]

        try:
            if action == "toast":
                result = self._send_toast(inputs)
            elif action == "sound":
                result = self._play_sound(inputs)
            elif action == "alert":
                result = self._send_alert(inputs)
            elif action == "history":
                result = self._get_history()
            else:
                result = {"error": f"Unknown action: {action}"}

            elapsed = round(time.time() - start, 3)
            self.execution_count += 1
            self.last_execution = datetime.now()

            success = "error" not in result
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": elapsed,
            }

        except Exception as e:
            logger.error(f"Notification skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Actions ──────────────────────────────────────────────

    def _send_toast(self, inputs: Dict[str, Any]) -> dict:
        """Envía notificación toast de Windows."""
        title = inputs.get("title", "Origin")
        message = inputs.get("message", "")
        icon = inputs.get("icon", "info")  # info, success, warning, error

        try:
            from winotify import Notification, audio

            toast = Notification(
                app_id="Origin",
                title=title,
                msg=message or "Notification from Origin",
                duration="short",
            )

            # Map icon type to system audio
            audio_map = {
                "info": audio.Default,
                "success": audio.Default,
                "warning": audio.Reminder,
                "error": audio.IM,
                "alert": audio.LoopingAlarm,
            }
            toast.set_audio(audio_map.get(icon, audio.Default), loop=False)
            toast.show()

            entry = {
                "type": "toast",
                "title": title,
                "message": message,
                "icon": icon,
                "timestamp": datetime.now().isoformat(),
            }
            self._history.append(entry)

            logger.info(f"Toast sent: {title}")
            return {"sent": True, "title": title, "message": message, "icon": icon}

        except ImportError:
            # Fallback: use ctypes MessageBox for critical alerts
            logger.warning("winotify not available, using fallback")
            return self._fallback_notify(title, message)
        except Exception as e:
            logger.error(f"Toast error: {e}")
            return {"sent": False, "error": str(e)}

    @staticmethod
    def _sanitize_ps_string(text: str) -> str:
        """Sanitize string for safe PowerShell interpolation.
        Prevents injection by escaping quotes, backticks, and dollar signs."""
        if not text:
            return ""
        # Remove null bytes, limit length
        text = text.replace("\x00", "")[:500]
        # Escape PowerShell special characters
        text = text.replace("`", "``")  # backtick (PS escape char)
        text = text.replace('"', '`"')  # double quotes
        text = text.replace("$", "`$")  # variable expansion
        text = text.replace("#", "`#")  # comment char
        # Remove any remaining control characters
        text = "".join(c for c in text if ord(c) >= 32 or c in ("\n", "\r", "\t"))
        return text

    def _fallback_notify(self, title: str, message: str) -> dict:
        """Fallback usando PowerShell para toast notifications.
        Inputs are sanitized to prevent PowerShell injection."""
        import subprocess

        # SECURITY: Sanitize inputs before PowerShell interpolation
        safe_title = self._sanitize_ps_string(title)
        safe_message = self._sanitize_ps_string(message)

        ps_script = f"""
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null  # noqa: E501
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)  # noqa: E501
        $text = $template.GetElementsByTagName("text")
        $text.Item(0).AppendChild($template.CreateTextNode("{safe_title}")) > $null
        $text.Item(1).AppendChild($template.CreateTextNode("{safe_message}")) > $null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Origin").Show($toast)
        """
        try:
            subprocess.Popen(
                ["powershell", "-WindowStyle", "Hidden", "-NoProfile", "-Command", ps_script],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return {"sent": True, "title": title, "message": message, "method": "powershell_fallback"}
        except Exception as e:
            return {"sent": False, "error": f"Fallback failed: {e}"}

    def _play_sound(self, inputs: Dict[str, Any]) -> dict:
        """Reproduce un sonido del sistema."""
        import winsound

        preset = inputs.get("preset", "beep")
        frequency = inputs.get("frequency", 800)
        duration = inputs.get("duration", 200)

        if preset == "custom":
            tones = [(frequency, duration)]
        elif preset in SOUND_PRESETS:
            tones = SOUND_PRESETS[preset]
        else:
            tones = SOUND_PRESETS["beep"]

        # Play in background thread to not block
        def _play():
            for freq, dur in tones:
                if freq == 0:
                    import time as t

                    t.sleep(dur / 1000)
                else:
                    winsound.Beep(freq, dur)

        thread = threading.Thread(target=_play, daemon=True)
        thread.start()

        entry = {
            "type": "sound",
            "preset": preset,
            "tones": len(tones),
            "timestamp": datetime.now().isoformat(),
        }
        self._history.append(entry)

        return {"played": True, "preset": preset, "tones": len(tones)}

    def _send_alert(self, inputs: Dict[str, Any]) -> dict:
        """Combina toast + sonido para alertas urgentes."""
        # Send toast
        toast_result = self._send_toast(inputs)

        # Play alert sound
        sound_preset = inputs.get("sound_preset", "alert")
        sound_result = self._play_sound({"preset": sound_preset})

        return {
            "alerted": True,
            "toast": toast_result,
            "sound": sound_result,
        }

    def _get_history(self) -> dict:
        """Retorna historial de notificaciones."""
        return {
            "total": len(self._history),
            "recent": list(self._history)[-10:],
        }

    def get_skill_docs(self) -> Dict[str, Any]:
        """Documentación para el LLM."""
        return {
            "description": self.description,
            "actions": ["toast", "sound", "alert", "history"],
            "inputs_toast": {
                "action": "toast",
                "title": "Título de la notificación",
                "message": "Texto del cuerpo",
                "icon": "info|success|warning|error|alert (default: info)",
            },
            "inputs_sound": {
                "action": "sound",
                "preset": "info|success|warning|error|alert|startup|beep (default: beep)",
                "frequency": "(solo si preset=custom) Hz, default 800",
                "duration": "(solo si preset=custom) ms, default 200",
            },
            "inputs_alert": {
                "action": "alert",
                "title": "Título de la alerta",
                "message": "Texto urgente",
                "sound_preset": "preset de sonido (default: alert)",
            },
            "inputs_history": {
                "action": "history",
            },
            "example_toast": {
                "action": "toast",
                "title": "Descarga completa",
                "message": "El archivo se descargó correctamente",
                "icon": "success",
            },
            "example_sound": {
                "action": "sound",
                "preset": "success",
            },
            "example_alert": {
                "action": "alert",
                "title": "CPU Alta",
                "message": "El uso de CPU supera el 90%",
                "sound_preset": "warning",
            },
        }

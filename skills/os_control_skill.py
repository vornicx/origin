"""
OS Control Skill — Control profundo del sistema operativo Windows.

Cubre:
  - Volumen del sistema (pycaw + Core Audio API)
  - Brillo de pantalla (WMI)
  - Energía: bloquear, suspender, hibernar, reiniciar, apagar
  - Batería: nivel, plug, tiempo restante
  - Red: info conexión actual, WiFi disponibles, conectar a SSID
  - Displays: lista de monitores, resolución
  - Audio devices: lista, dispositivo por defecto
  - Power plans: ver y cambiar plan de energía
"""

import ctypes
import logging
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.os_control")


def _pycaw_available() -> bool:
    try:
        from pycaw.pycaw import AudioUtilities  # noqa: F401

        return True
    except Exception:
        return False


def _sbc_available() -> bool:
    try:
        import screen_brightness_control  # noqa: F401

        return True
    except Exception:
        return False


class OSControlSkill(BaseSkill):
    """
    Control nativo de Windows: volumen, brillo, energía, red, batería.

    Acciones:
        volume          → get/set/mute/up/down volumen del sistema
        brightness      → get/set brillo de monitor
        lock            → bloquear pantalla
        sleep           → suspender el equipo
        hibernate       → hibernar
        restart         → reiniciar (con confirmación)
        shutdown        → apagar (con confirmación)
        battery         → estado batería
        network         → info red actual
        wifi_list       → escanear redes WiFi
        wifi_connect    → conectar a SSID guardada
        displays        → info de monitores
        audio_devices   → dispositivos de audio
        power_plan      → ver/cambiar plan de energía
        idle_time       → tiempo desde última actividad del usuario
    """

    VALID_ACTIONS = frozenset(
        {
            "volume",
            "brightness",
            "lock",
            "sleep",
            "hibernate",
            "restart",
            "shutdown",
            "battery",
            "network",
            "wifi_list",
            "wifi_connect",
            "displays",
            "audio_devices",
            "power_plan",
            "idle_time",
        }
    )

    def __init__(self):
        super().__init__(
            name="os_control",
            description=(
                "Control nativo de Windows: volumen, brillo, energía (lock/sleep/restart), "
                "batería, WiFi, displays, dispositivos de audio."
            ),
        )

    # ── BaseSkill interface ───────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        if action not in self.VALID_ACTIONS:
            return False, f"Acción inválida: '{action}'. Válidas: {sorted(self.VALID_ACTIONS)}"

        if action in ("restart", "shutdown") and not inputs.get("confirm"):
            return False, f"'{action}' requiere confirm=true por seguridad"

        if action == "wifi_connect" and not inputs.get("ssid"):
            return False, "wifi_connect requiere 'ssid'"

        if action == "volume":
            op = inputs.get("op", "get")
            if op == "set" and "level" not in inputs:
                return False, "volume set requiere 'level' (0-100)"

        if action == "brightness":
            op = inputs.get("op", "get")
            if op == "set" and "level" not in inputs:
                return False, "brightness set requiere 'level' (0-100)"

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

            success = result.get("error") is None
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": round(time.time() - start, 3),
            }
        except Exception as e:
            logger.error(f"OSControl error in {action}: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Volume (pycaw) ───────────────────────────────────────────

    def _act_volume(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if not _pycaw_available():
            return {"error": "pycaw no instalado"}

        from pycaw.pycaw import AudioUtilities

        # Modern pycaw API exposes EndpointVolume directly on the device
        device = AudioUtilities.GetSpeakers()
        vol = device.EndpointVolume

        op = inputs.get("op", "get")
        if op == "get":
            return {
                "level": round(vol.GetMasterVolumeLevelScalar() * 100, 1),
                "muted": bool(vol.GetMute()),
            }
        elif op == "set":
            level = max(0, min(100, int(inputs["level"])))
            vol.SetMasterVolumeLevelScalar(level / 100.0, None)
            return {"level": level, "set": True}
        elif op == "mute":
            vol.SetMute(1, None)
            return {"muted": True}
        elif op == "unmute":
            vol.SetMute(0, None)
            return {"muted": False}
        elif op == "toggle_mute":
            new = 0 if vol.GetMute() else 1
            vol.SetMute(new, None)
            return {"muted": bool(new)}
        elif op == "up":
            step = inputs.get("step", 10) / 100.0
            current = vol.GetMasterVolumeLevelScalar()
            new = min(1.0, current + step)
            vol.SetMasterVolumeLevelScalar(new, None)
            return {"level": round(new * 100, 1)}
        elif op == "down":
            step = inputs.get("step", 10) / 100.0
            current = vol.GetMasterVolumeLevelScalar()
            new = max(0.0, current - step)
            vol.SetMasterVolumeLevelScalar(new, None)
            return {"level": round(new * 100, 1)}
        return {"error": f"op desconocido: '{op}'. Válidos: get, set, mute, unmute, toggle_mute, up, down"}

    # ── Brightness (WMI / screen_brightness_control) ─────────────

    def _act_brightness(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if not _sbc_available():
            return {"error": "screen_brightness_control no instalado"}

        import screen_brightness_control as sbc

        op = inputs.get("op", "get")
        display = inputs.get("display")  # int or None for all

        try:
            if op == "get":
                levels = sbc.get_brightness(display=display)
                return {"levels": levels, "primary": levels[0] if levels else None}
            elif op == "set":
                level = max(0, min(100, int(inputs["level"])))
                sbc.set_brightness(level, display=display)
                return {"level": level, "set": True}
            elif op == "up":
                step = inputs.get("step", 10)
                current = sbc.get_brightness(display=display)
                new = min(100, current[0] + step) if current else step
                sbc.set_brightness(new, display=display)
                return {"level": new}
            elif op == "down":
                step = inputs.get("step", 10)
                current = sbc.get_brightness(display=display)
                new = max(0, current[0] - step) if current else 0
                sbc.set_brightness(new, display=display)
                return {"level": new}
            elif op == "list":
                return {"displays": sbc.list_monitors()}
        except Exception as e:
            return {"error": f"Error de brillo: {e}"}

        return {"error": f"op desconocido: '{op}'"}

    # ── Power control ────────────────────────────────────────────

    def _act_lock(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            ctypes.windll.user32.LockWorkStation()
            return {"locked": True}
        except Exception as e:
            return {"error": f"Lock falló: {e}"}

    def _act_sleep(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            # SetSuspendState: bHibernate, bForce, bWakeupEventsDisabled
            ctypes.windll.PowrProf.SetSuspendState(False, True, False)
            return {"sleeping": True}
        except Exception as e:
            # Fallback via rundll32
            try:
                subprocess.Popen(
                    ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return {"sleeping": True, "method": "rundll32"}
            except Exception as e2:
                return {"error": f"Sleep falló: {e} / {e2}"}

    def _act_hibernate(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            ctypes.windll.PowrProf.SetSuspendState(True, True, False)
            return {"hibernating": True}
        except Exception as e:
            return {"error": f"Hibernate falló: {e}"}

    def _act_restart(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        delay = max(0, int(inputs.get("delay_sec", 5)))
        try:
            subprocess.Popen(
                ["shutdown", "/r", "/t", str(delay), "/d", "p:0:0"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return {"restart_scheduled": True, "delay_sec": delay}
        except Exception as e:
            return {"error": f"Restart falló: {e}"}

    def _act_shutdown(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        delay = max(0, int(inputs.get("delay_sec", 5)))
        try:
            subprocess.Popen(
                ["shutdown", "/s", "/t", str(delay), "/d", "p:0:0"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return {"shutdown_scheduled": True, "delay_sec": delay}
        except Exception as e:
            return {"error": f"Shutdown falló: {e}"}

    # ── Battery ──────────────────────────────────────────────────

    def _act_battery(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        bat = psutil.sensors_battery()
        if bat is None:
            return {"error": "No hay batería (equipo de escritorio)"}
        return {
            "percent": round(bat.percent, 1),
            "plugged_in": bat.power_plugged,
            "seconds_left": bat.secsleft if bat.secsleft != psutil.POWER_TIME_UNLIMITED else None,
            "time_left": (
                "ilimitado"
                if bat.secsleft == psutil.POWER_TIME_UNLIMITED
                else f"{bat.secsleft // 3600}h {(bat.secsleft % 3600) // 60}m"
                if bat.secsleft > 0
                else "desconocido"
            ),
        }

    # ── Network ──────────────────────────────────────────────────

    def _act_network(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        info: Dict[str, Any] = {"interfaces": [], "stats": {}}

        # netifaces equivalent via psutil
        for iface, addrs in psutil.net_if_addrs().items():
            iface_data = {"name": iface, "addresses": []}
            for a in addrs:
                if a.family.name in ("AF_INET", "AF_INET6"):
                    iface_data["addresses"].append(
                        {
                            "family": a.family.name,
                            "address": a.address,
                            "netmask": a.netmask,
                        }
                    )
            if iface_data["addresses"]:
                info["interfaces"].append(iface_data)

        # Active connection via netsh
        try:
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stderr=subprocess.DEVNULL,
                timeout=5,
            ).decode("utf-8", errors="ignore")

            wifi: Dict[str, Optional[str]] = {}
            for line in out.splitlines():
                line = line.strip()
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip()
                if key == "ssid":
                    wifi["ssid"] = value
                elif key == "bssid":
                    wifi["bssid"] = value
                elif key == "signal":
                    wifi["signal"] = value
                elif key == "state":
                    wifi["state"] = value
                elif key == "radio type":
                    wifi["radio_type"] = value
            if wifi:
                info["wifi"] = wifi
        except Exception as e:
            info["wifi_error"] = str(e)

        # IO counters
        try:
            io = psutil.net_io_counters()
            info["stats"] = {
                "bytes_sent_mb": round(io.bytes_sent / 1024 / 1024, 1),
                "bytes_recv_mb": round(io.bytes_recv / 1024 / 1024, 1),
                "packets_sent": io.packets_sent,
                "packets_recv": io.packets_recv,
            }
        except Exception:
            pass

        return info

    def _act_wifi_list(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            out = subprocess.check_output(
                ["netsh", "wlan", "show", "networks", "mode=bssid"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stderr=subprocess.DEVNULL,
                timeout=10,
            ).decode("utf-8", errors="ignore")

            networks: List[Dict[str, str]] = []
            current: Optional[Dict[str, str]] = None
            for line in out.splitlines():
                stripped = line.strip()
                if stripped.startswith("SSID ") and "BSSID" not in stripped:
                    if current:
                        networks.append(current)
                    current = {"ssid": stripped.split(":", 1)[1].strip()}
                elif current and ":" in stripped:
                    key, _, value = stripped.partition(":")
                    k = key.strip().lower()
                    v = value.strip()
                    if k in ("authentication", "encryption", "signal", "radio type"):
                        current[k.replace(" ", "_")] = v
            if current:
                networks.append(current)

            return {"networks": networks, "count": len(networks)}
        except subprocess.TimeoutExpired:
            return {"error": "netsh wlan timeout (radio apagada?)"}
        except Exception as e:
            return {"error": str(e)}

    def _act_wifi_connect(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        ssid = inputs["ssid"]
        try:
            # Connect to a profile that already exists
            out = subprocess.check_output(
                ["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stderr=subprocess.STDOUT,
                timeout=10,
            ).decode("utf-8", errors="ignore")
            return {"ssid": ssid, "output": out.strip()}
        except subprocess.CalledProcessError as e:
            return {"error": f"Connect falló: {e.output.decode('utf-8', errors='ignore')}"}
        except Exception as e:
            return {"error": str(e)}

    # ── Displays ─────────────────────────────────────────────────

    def _act_displays(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        try:
            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            virtual_w = user32.GetSystemMetrics(78)
            virtual_h = user32.GetSystemMetrics(79)
            monitor_count = user32.GetSystemMetrics(80)

            displays = {
                "primary": {"width": screen_w, "height": screen_h},
                "virtual": {"width": virtual_w, "height": virtual_h},
                "count": monitor_count,
            }

            # Per-display info via screen_brightness_control if available
            if _sbc_available():
                import screen_brightness_control as sbc

                try:
                    raw = sbc.list_monitors_info()
                    # sbc returns a "method" field that holds a class object —
                    # not JSON-serializable. Stringify or drop non-primitive values.
                    cleaned = []
                    for m in raw:
                        cleaned.append({
                            k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                            for k, v in m.items()
                        })
                    displays["monitors"] = cleaned
                except Exception:
                    pass

            return displays
        except Exception as e:
            return {"error": str(e)}

    # ── Audio devices ────────────────────────────────────────────

    def _act_audio_devices(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        if not _pycaw_available():
            return {"error": "pycaw no instalado"}
        try:
            from pycaw.pycaw import AudioUtilities

            sessions = AudioUtilities.GetAllSessions()
            apps = []
            for s in sessions:
                if s.Process:
                    apps.append(
                        {
                            "process": s.Process.name(),
                            "pid": s.Process.pid,
                            "display_name": s.DisplayName or "",
                        }
                    )

            speakers = AudioUtilities.GetSpeakers()
            default_out = speakers.GetId() if hasattr(speakers, "GetId") else None

            return {
                "active_sessions": apps,
                "session_count": len(apps),
                "default_device_id": default_out,
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Power plan ───────────────────────────────────────────────

    def _act_power_plan(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        op = inputs.get("op", "get")
        try:
            if op == "get":
                out = (
                    subprocess.check_output(
                        ["powercfg", "/getactivescheme"],
                        creationflags=subprocess.CREATE_NO_WINDOW,
                        stderr=subprocess.DEVNULL,
                    )
                    .decode("utf-8", errors="ignore")
                    .strip()
                )
                return {"active_plan": out}
            elif op == "list":
                out = subprocess.check_output(
                    ["powercfg", "/list"],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                ).decode("utf-8", errors="ignore")
                return {"plans": out.strip()}
            elif op == "set":
                guid = inputs.get("guid", "")
                if not guid:
                    return {"error": "set requiere 'guid' del plan"}
                subprocess.check_call(
                    ["powercfg", "/setactive", guid],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return {"set": True, "guid": guid}
        except Exception as e:
            return {"error": str(e)}
        return {"error": f"op desconocido: '{op}'"}

    # ── Idle time ────────────────────────────────────────────────

    def _act_idle_time(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Time in seconds since the last user input (mouse/keyboard)."""
        try:

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_ulong)]

            li = LASTINPUTINFO()
            li.cbSize = ctypes.sizeof(LASTINPUTINFO)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
            millis = ctypes.windll.kernel32.GetTickCount() - li.dwTime
            return {"idle_seconds": round(millis / 1000, 1)}
        except Exception as e:
            return {"error": str(e)}

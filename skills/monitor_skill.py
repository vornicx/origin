"""
MonitorSkill — Monitoreo en background de recursos del sistema con alertas automáticas.

Acciones:
  start       → Inicia monitoreo en background (CPU, RAM, disco, red)
  stop        → Detiene el monitoreo
  status      → Estado actual del monitor + métricas en vivo
  thresholds  → Ver/modificar umbrales de alerta
  history     → Historial de alertas disparadas
  snapshot    → Captura instantánea de todas las métricas
"""

import logging
import time
import asyncio
import threading
from typing import Dict, Any, Optional, Callable, Awaitable
from datetime import datetime
from collections import deque

import psutil

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills")

# ── Default thresholds ────────────────────────────────────────
DEFAULT_THRESHOLDS = {
    "cpu_percent": 90.0,  # % uso CPU
    "ram_percent": 85.0,  # % uso RAM
    "disk_percent": 90.0,  # % uso disco
    "cpu_temp": 85.0,  # °C (si disponible)
    "process_count": 300,  # Número de procesos
    "net_error_rate": 50,  # Errores de red por intervalo
}

# Cooldown entre alertas del mismo tipo (segundos)
ALERT_COOLDOWN = 300  # 5 minutos


class MonitorSkill(BaseSkill):
    """Monitorea recursos del sistema en background y lanza alertas."""

    def __init__(self):
        super().__init__(
            name="monitor", description="Monitorea CPU, RAM, disco y red en background con alertas automáticas"
        )
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._interval: float = 15.0  # Segundos entre checks
        self._thresholds: Dict[str, float] = dict(DEFAULT_THRESHOLDS)
        self._alert_history: deque = deque(maxlen=100)
        self._metrics_history: deque = deque(maxlen=200)
        self._last_alert_time: Dict[str, float] = {}
        self._notification_callback: Optional[Callable] = None
        self._ws_broadcast: Optional[Callable[..., Awaitable]] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_notification_callback(self, callback: Callable):
        """Inyecta la NotificationSkill para disparar alertas."""
        self._notification_callback = callback

    def set_ws_broadcast(self, broadcast_fn: Callable[..., Awaitable], loop: asyncio.AbstractEventLoop):
        """Inyecta función de broadcast WebSocket para push al frontend."""
        self._ws_broadcast = broadcast_fn
        self._loop = loop

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "")
        valid_actions = ("start", "stop", "status", "thresholds", "history", "snapshot")
        if action not in valid_actions:
            return False, f"Acción inválida: '{action}'. Válidas: {valid_actions}"

        if action == "thresholds" and inputs.get("set"):
            for key in inputs["set"]:
                if key not in DEFAULT_THRESHOLDS:
                    return False, f"Threshold desconocido: '{key}'. Válidos: {list(DEFAULT_THRESHOLDS.keys())}"
                if not isinstance(inputs["set"][key], (int, float)):
                    return False, f"Threshold '{key}' debe ser numérico"

        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        is_valid, error_msg = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error_msg, "execution_time": 0}

        start = time.time()
        action = inputs["action"]

        try:
            if action == "start":
                result = self._start_monitor(inputs)
            elif action == "stop":
                result = self._stop_monitor()
            elif action == "status":
                result = self._get_status()
            elif action == "thresholds":
                result = self._handle_thresholds(inputs)
            elif action == "history":
                result = self._get_alert_history(inputs)
            elif action == "snapshot":
                result = self._take_snapshot()
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
            logger.error(f"Monitor skill error: {e}")
            return {
                "success": False,
                "result": None,
                "error": str(e),
                "execution_time": round(time.time() - start, 3),
            }

    # ── Actions ──────────────────────────────────────────────

    def _start_monitor(self, inputs: Dict[str, Any]) -> dict:
        """Inicia el loop de monitoreo en background."""
        if self._running:
            return {"started": False, "message": "Monitor ya está corriendo", "interval": self._interval}

        self._interval = inputs.get("interval", 15.0)
        self._interval = max(5.0, min(300.0, self._interval))  # Clamp 5s-5min

        # Update thresholds if provided
        if inputs.get("thresholds"):
            for k, v in inputs["thresholds"].items():
                if k in self._thresholds:
                    self._thresholds[k] = float(v)

        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="origin-monitor")
        self._thread.start()

        logger.info(f"Monitor started with interval={self._interval}s")
        return {
            "started": True,
            "interval": self._interval,
            "thresholds": dict(self._thresholds),
        }

    def _stop_monitor(self) -> dict:
        """Detiene el monitoreo."""
        if not self._running:
            return {"stopped": False, "message": "Monitor no está corriendo"}

        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

        logger.info("Monitor stopped")
        return {
            "stopped": True,
            "total_alerts": len(self._alert_history),
            "total_snapshots": len(self._metrics_history),
        }

    def _get_status(self) -> dict:
        """Estado actual del monitor + métricas en vivo."""
        snapshot = self._take_snapshot()
        return {
            "running": self._running,
            "interval": self._interval,
            "thresholds": dict(self._thresholds),
            "total_alerts": len(self._alert_history),
            "total_snapshots": len(self._metrics_history),
            "current_metrics": snapshot,
            "last_alert": self._alert_history[-1] if self._alert_history else None,
        }

    def _handle_thresholds(self, inputs: Dict[str, Any]) -> dict:
        """Ver o modificar umbrales de alerta."""
        if inputs.get("set"):
            for k, v in inputs["set"].items():
                if k in self._thresholds:
                    old = self._thresholds[k]
                    self._thresholds[k] = float(v)
                    logger.info(f"Threshold updated: {k} = {old} → {v}")
            return {"updated": True, "thresholds": dict(self._thresholds)}
        return {"thresholds": dict(self._thresholds)}

    def _get_alert_history(self, inputs: Dict[str, Any]) -> dict:
        """Historial de alertas."""
        limit = inputs.get("limit", 20)
        alerts = list(self._alert_history)[-limit:]
        return {
            "total_alerts": len(self._alert_history),
            "showing": len(alerts),
            "alerts": alerts,
        }

    def _take_snapshot(self) -> dict:
        """Captura instantánea de todas las métricas."""
        cpu_percent = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")
        net = psutil.net_io_counters()
        procs = len(psutil.pids())

        # CPU temperature (may not be available)
        cpu_temp = None
        try:
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    if entries:
                        cpu_temp = entries[0].current
                        break
        except (AttributeError, Exception):
            pass

        # GPU utilization
        gpu_percent = self._get_gpu_percent()

        # Net speed (MB/s) — delta since last snapshot
        net_speed_mbps = self._calc_net_speed(net)

        # Uptime
        uptime_seconds = None
        try:
            uptime_seconds = round(time.time() - psutil.boot_time())
        except Exception:
            pass

        # Top processes by CPU
        top_procs = []
        try:
            for proc in sorted(
                psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]),
                key=lambda p: p.info.get("cpu_percent", 0) or 0,
                reverse=True,
            )[:5]:
                top_procs.append(
                    {
                        "pid": proc.info["pid"],
                        "name": proc.info["name"],
                        "cpu": round(proc.info.get("cpu_percent", 0) or 0, 1),
                        "ram": round(proc.info.get("memory_percent", 0) or 0, 1),
                    }
                )
        except Exception:
            pass

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "cpu_percent": cpu_percent,
            "ram_percent": ram.percent,
            "ram_used_gb": round(ram.used / (1024**3), 2),
            "ram_total_gb": round(ram.total / (1024**3), 2),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024**3), 2),
            "process_count": procs,
            "cpu_temp": cpu_temp,
            "gpu_percent": gpu_percent,
            "net_speed_mbps": net_speed_mbps,
            "uptime_seconds": uptime_seconds,
            "net_bytes_sent_mb": round(net.bytes_sent / (1024**2), 1),
            "net_bytes_recv_mb": round(net.bytes_recv / (1024**2), 1),
            "net_errors": net.errin + net.errout,
            "top_processes": top_procs,
        }

        return snapshot

    def _get_gpu_percent(self) -> Optional[float]:
        """GPU utilization via nvidia-smi (NVIDIA) or fallback."""
        try:
            import subprocess
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                vals = [float(v.strip()) for v in r.stdout.strip().split("\n") if v.strip()]
                if vals:
                    return round(sum(vals) / len(vals), 1)
        except Exception:
            pass
        return None

    def _calc_net_speed(self, net_counters) -> float:
        """Calculate net throughput in MB/s since last call."""
        now = time.time()
        if not hasattr(self, "_last_net_counters"):
            self._last_net_counters = net_counters
            self._last_net_time = now
            return 0.0
        dt = now - self._last_net_time
        if dt <= 0:
            return 0.0
        sent_delta = net_counters.bytes_sent - self._last_net_counters.bytes_sent
        recv_delta = net_counters.bytes_recv - self._last_net_counters.bytes_recv
        self._last_net_counters = net_counters
        self._last_net_time = now
        return round((sent_delta + recv_delta) / (1024 * 1024 * dt), 2)

    # ── Background monitor loop ──────────────────────────────

    def _monitor_loop(self):
        """Loop principal del monitor. Corre en thread separado."""
        logger.info("Monitor loop started")

        while self._running:
            try:
                snapshot = self._take_snapshot()
                self._metrics_history.append(snapshot)
                self._check_thresholds(snapshot)
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            # Sleep in small chunks so we can stop quickly
            for _ in range(int(self._interval * 2)):
                if not self._running:
                    break
                time.sleep(0.5)

        logger.info("Monitor loop stopped")

    def _check_thresholds(self, snapshot: dict):
        """Verifica umbrales y genera alertas si se superan."""
        now = time.time()

        checks = [
            (
                "cpu_percent",
                snapshot.get("cpu_percent", 0),
                self._thresholds["cpu_percent"],
                "CPU Alta",
                f"Uso de CPU: {snapshot.get('cpu_percent', 0):.1f}%",
            ),
            (
                "ram_percent",
                snapshot.get("ram_percent", 0),
                self._thresholds["ram_percent"],
                "RAM Alta",
                f"Uso de RAM: {snapshot.get('ram_percent', 0):.1f}% ({snapshot.get('ram_used_gb', 0):.1f}GB)",
            ),
            (
                "disk_percent",
                snapshot.get("disk_percent", 0),
                self._thresholds["disk_percent"],
                "Disco Lleno",
                f"Uso de disco: {snapshot.get('disk_percent', 0):.1f}% (libre: {snapshot.get('disk_free_gb', 0):.1f}GB)",  # noqa: E501
            ),
            (
                "process_count",
                snapshot.get("process_count", 0),
                self._thresholds["process_count"],
                "Muchos Procesos",
                f"Procesos activos: {snapshot.get('process_count', 0)}",
            ),
        ]

        if snapshot.get("cpu_temp") is not None:
            checks.append(
                (
                    "cpu_temp",
                    snapshot["cpu_temp"],
                    self._thresholds["cpu_temp"],
                    "Temperatura CPU Alta",
                    f"Temperatura: {snapshot['cpu_temp']:.1f}°C",
                )
            )

        for key, value, threshold, title, message in checks:
            if value >= threshold:
                # Check cooldown
                last_time = self._last_alert_time.get(key, 0)
                if now - last_time < ALERT_COOLDOWN:
                    continue

                self._last_alert_time[key] = now
                alert = {
                    "metric": key,
                    "value": value,
                    "threshold": threshold,
                    "title": f"Origin: {title}",
                    "message": message,
                    "timestamp": datetime.now().isoformat(),
                    "severity": "critical" if value >= threshold * 1.1 else "warning",
                }
                self._alert_history.append(alert)
                logger.warning(f"Monitor alert: {title} - {message}")

                # Fire notification
                self._fire_alert(alert)

    def _fire_alert(self, alert: dict):
        """Dispara la notificación y push al frontend."""
        # 1. Toast notification via NotificationSkill callback
        if self._notification_callback:
            try:
                self._notification_callback(
                    {
                        "action": "alert",
                        "title": alert["title"],
                        "message": alert["message"],
                        "icon": "error" if alert["severity"] == "critical" else "warning",
                        "sound_preset": "alert" if alert["severity"] == "critical" else "warning",
                    }
                )
            except Exception as e:
                logger.error(f"Alert notification callback failed: {e}")

        # 2. WebSocket push to frontend
        if self._ws_broadcast and self._loop:
            try:
                ws_msg = {
                    "type": "monitor_alert",
                    "alert": alert,
                }
                asyncio.run_coroutine_threadsafe(
                    self._ws_broadcast(ws_msg),
                    self._loop,
                )
            except Exception as e:
                logger.error(f"Alert WS broadcast failed: {e}")

    def get_skill_docs(self) -> Dict[str, Any]:
        """Documentación para el LLM."""
        return {
            "description": self.description,
            "actions": ["start", "stop", "status", "thresholds", "history", "snapshot"],
            "inputs_start": {
                "action": "start",
                "interval": "Segundos entre checks (default 15, min 5, max 300)",
                "thresholds": "(opcional) dict de umbrales a modificar, ej: {'cpu_percent': 80}",
            },
            "inputs_stop": {
                "action": "stop",
            },
            "inputs_status": {
                "action": "status",
            },
            "inputs_thresholds": {
                "action": "thresholds",
                "set": "(opcional) dict de umbrales a cambiar, ej: {'ram_percent': 90}",
            },
            "inputs_history": {
                "action": "history",
                "limit": "(opcional) número de alertas a mostrar (default 20)",
            },
            "inputs_snapshot": {
                "action": "snapshot",
            },
            "CRITICO": "Para 'start' basta con action='start'. El usuario puede pedir ajustar interval y thresholds.",
            "example_start": {
                "action": "start",
                "interval": 30,
            },
            "example_thresholds": {
                "action": "thresholds",
                "set": {"cpu_percent": 80, "ram_percent": 75},
            },
            "example_snapshot": {
                "action": "snapshot",
            },
        }

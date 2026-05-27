"""
EventLogSkill — Integración con el Windows Event Log.

Permite a Origin:
  - Leer entradas del Event Log (System, Application, Security)
  - Escribir entradas al Event Log de Origin
  - Monitorear eventos específicos (errores, warnings)
  - Reaccionar a eventos del sistema (sleep, battery, etc.)

Requisito: pywin32 (ya instalado)
"""

import logging
import asyncio
from typing import Dict, Any
from datetime import datetime

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.eventlog")

EVENTLOG_AVAILABLE = False
try:
    import win32evtlog
    import win32evtlogutil

    EVENTLOG_AVAILABLE = True
except ImportError:
    pass


# Register Origin as an event source
_EVENT_SOURCE = "Origin"
_EVENT_LOG_NAME = "Application"

if EVENTLOG_AVAILABLE:
    try:
        win32evtlogutil.AddSourceToRegistry(_EVENT_SOURCE, _EVENT_LOG_NAME, ".\\")
    except Exception:
        pass


_MSG_CACHE = {
    "system": None,
    "application": None,
    "security": None,
}


class EventLogSkill(BaseSkill):
    """Integración con el Windows Event Log.

    Acciones:
        read        → Lee entradas del Event Log
        write       → Escribe una entrada al log
        monitor     → Monitorea eventos específicos
        status      → Estado del sistema de eventos
    """

    VALID_ACTIONS = {"read", "write", "monitor", "status"}

    def __init__(self):
        super().__init__(
            name="eventlog", description="Lee y escribe en el Windows Event Log. Monitorea eventos del sistema."
        )
        self._monitor_task = None
        self._running = False
        self._alert_callback = None
        self._event_types = {
            "error": win32evtlog.EVENTLOG_ERROR_TYPE if EVENTLOG_AVAILABLE else 1,
            "warning": win32evtlog.EVENTLOG_WARNING_TYPE if EVENTLOG_AVAILABLE else 2,
            "info": win32evtlog.EVENTLOG_INFORMATION_TYPE if EVENTLOG_AVAILABLE else 4,
        }

    def set_alert_callback(self, callback):
        """Callback para notificar eventos críticos."""
        self._alert_callback = callback

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple:
        action = inputs.get("action", "")
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"
        if action == "read":
            log = inputs.get("log", "system")
            if log not in ("system", "application", "security"):
                return False, "Log invalido: system, application, o security"
        if action == "write" and not inputs.get("message"):
            return False, "Se requiere 'message' para write"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        import time

        start = time.time()
        self.execution_count += 1
        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}
        try:
            action = inputs["action"]
            if action == "read":
                result = await self._read(inputs)
            elif action == "write":
                result = self._write(inputs)
            elif action == "monitor":
                result = await self._monitor(inputs)
            elif action == "status":
                result = self._status()
            else:
                result = {"error": f"Accion no implementada: {action}"}
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
            logger.error(f"EventLog error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": elapsed}

    async def _read(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lee entradas del Event Log."""
        if not EVENTLOG_AVAILABLE:
            return {"error": "pywin32 no disponible para Event Log"}

        log_name = inputs.get("log", "system")
        max_events = min(inputs.get("max_events", 50), 200)
        event_type = inputs.get("event_type", "")  # error, warning, info, o vacio = todos

        try:
            handle = win32evtlog.OpenEventLog(None, log_name)
            flags = win32evtlog.EVENTLOG_BACKWARDS_READ | win32evtlog.EVENTLOG_SEQUENTIAL_READ
            events = []
            while len(events) < max_events:
                chunk = win32evtlog.ReadEventLog(handle, flags, 0)
                if not chunk:
                    break
                for event in chunk:
                    if event_type:
                        et = event.EventType
                        if event_type == "error" and et != win32evtlog.EVENTLOG_ERROR_TYPE:
                            continue
                        if event_type == "warning" and et != win32evtlog.EVENTLOG_WARNING_TYPE:
                            continue
                        if event_type == "info" and et != win32evtlog.EVENTLOG_INFORMATION_TYPE:
                            continue
                    events.append(
                        {
                            "event_id": event.EventID,
                            "source": event.SourceName,
                            "type": ["error", "warning", "info", "audit_success", "audit_failure"][event.EventType - 1]
                            if 1 <= event.EventType <= 5
                            else "unknown",
                            "time": event.TimeGenerated.Format()
                            if hasattr(event.TimeGenerated, "Format")
                            else str(event.TimeGenerated),
                            "category": event.EventCategory,
                            "computer": event.ComputerName,
                            "message": str(event.StringInserts) if event.StringInserts else "",
                        }
                    )
                    if len(events) >= max_events:
                        break
            win32evtlog.CloseEventLog(handle)
            return {"log": log_name, "events": events, "count": len(events)}
        except Exception as e:
            return {"error": f"Error leyendo Event Log: {e}"}

    def _write(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Escribe una entrada al Windows Event Log."""
        if not EVENTLOG_AVAILABLE:
            return {"error": "pywin32 no disponible"}

        message = inputs.get("message", "")
        event_type = inputs.get("event_type", "info")
        event_id = inputs.get("event_id", 1000)
        category = inputs.get("category", 0)

        type_map = {
            "error": win32evtlog.EVENTLOG_ERROR_TYPE,
            "warning": win32evtlog.EVENTLOG_WARNING_TYPE,
            "info": win32evtlog.EVENTLOG_INFORMATION_TYPE,
        }
        evt_type = type_map.get(event_type, win32evtlog.EVENTLOG_INFORMATION_TYPE)

        try:
            win32evtlogutil.ReportEvent(
                _EVENT_LOG_NAME,
                event_id,
                category,
                evt_type,
                [_EVENT_SOURCE],
                message,
            )
            return {"written": True, "event_type": event_type, "event_id": event_id, "message": message[:100]}
        except Exception as e:
            return {"error": f"Error escribiendo Event Log: {e}"}

    async def _monitor(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Inicia/detiene monitoreo de eventos del sistema."""
        action = inputs.get("sub_action", "start")
        if action == "start":
            if not self._running:
                self._running = True
                self._monitor_task = asyncio.create_task(self._monitor_loop(inputs))
                return {"monitoring": True, "log": inputs.get("log", "system"), "filter": inputs.get("filter", "error")}
            return {"monitoring": True, "already_running": True}
        elif action == "stop":
            self._running = False
            if self._monitor_task:
                self._monitor_task.cancel()
                self._monitor_task = None
            return {"monitoring": False}
        return {"error": "sub_action debe ser start o stop"}

    async def _monitor_loop(self, inputs: Dict[str, Any]):
        """Bucle de monitoreo de eventos."""
        log_name = inputs.get("log", "system")
        poll_interval = inputs.get("interval", 30)

        try:
            while self._running:
                events = await self._read({"log": log_name, "max_events": 5, "event_type": "error"})
                if events.get("count", 0) > 0 and self._alert_callback:
                    try:
                        await self._alert_callback({"type": "eventlog_alert", "log": log_name, "events": events})
                    except Exception:
                        pass
                await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            pass

    def _status(self) -> Dict[str, Any]:
        """Estado del sistema de Event Log."""
        return {
            "available": EVENTLOG_AVAILABLE,
            "event_source": _EVENT_SOURCE,
            "monitoring": self._running,
            "total_actions": self.execution_count,
        }

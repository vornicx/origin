import time
import platform
import os
from datetime import datetime
from typing import Dict, Any

from .base_skill import BaseSkill


class SystemInfoSkill(BaseSkill):
    """
    Skill: Información del sistema operativo, hardware y procesos.
    No requiere dependencias externas.
    """

    def __init__(self):
        super().__init__(
            name="system_info", description="Obtiene información del sistema: OS, CPU, RAM, disco, procesos"
        )

    # Aliases para acciones comunes
    _ACTION_ALIASES = {
        "os": "overview",
        "system": "overview",
        "info": "overview",
        "cpu": "overview",
        "memory": "overview",
        "ram": "overview",
        "disk": "disk",
        "storage": "disk",
        "processes": "processes",
        "procs": "processes",
        "top": "processes",
        "network": "network",
        "net": "network",
        "ip": "network",
    }

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "overview")
        resolved = self._ACTION_ALIASES.get(action, action)
        valid = {"overview", "disk", "processes", "network"}
        if resolved not in valid:
            return False, f"Accion invalida '{action}'. Validas: {list(self._ACTION_ALIASES.keys())}"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs.get("action", "overview")
            action = self._ACTION_ALIASES.get(action, action)
            result = {}

            if action == "overview":
                result = self._get_overview()
            elif action == "disk":
                result = self._get_disk_info()
            elif action == "processes":
                result = self._get_top_processes()
            elif action == "network":
                result = self._get_network_info()

            elapsed = round(time.time() - start, 4)
            self.last_execution = datetime.now()
            return {"success": True, "result": result, "error": None, "execution_time": elapsed}

        except Exception as e:
            elapsed = round(time.time() - start, 4)
            return {"success": False, "result": None, "error": str(e), "execution_time": elapsed}

    def _get_overview(self) -> Dict[str, Any]:
        import shutil

        uname = platform.uname()
        disk = shutil.disk_usage("/")

        info = {
            "os": f"{uname.system} {uname.release}",
            "machine": uname.machine,
            "processor": uname.processor or platform.processor(),
            "python_version": platform.python_version(),
            "hostname": uname.node,
            "cpu_count": os.cpu_count(),
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "disk_used_percent": round(disk.used / disk.total * 100, 1),
        }

        # RAM info via psutil si está disponible
        try:
            import psutil

            mem = psutil.virtual_memory()
            info["ram_total_gb"] = round(mem.total / (1024**3), 1)
            info["ram_available_gb"] = round(mem.available / (1024**3), 1)
            info["ram_used_percent"] = mem.percent
            info["cpu_percent"] = psutil.cpu_percent(interval=0.5)
        except ImportError:
            info["ram_info"] = "psutil not installed (pip install psutil for detailed RAM/CPU)"

        return info

    def _get_disk_info(self) -> Dict[str, Any]:
        import shutil

        disk = shutil.disk_usage("/")
        return {
            "total_gb": round(disk.total / (1024**3), 1),
            "used_gb": round(disk.used / (1024**3), 1),
            "free_gb": round(disk.free / (1024**3), 1),
            "used_percent": round(disk.used / disk.total * 100, 1),
        }

    def _get_top_processes(self) -> Dict[str, Any]:
        try:
            import psutil

            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    info = p.info
                    if info["cpu_percent"] and info["cpu_percent"] > 0:
                        procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            procs.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
            return {"top_processes": procs[:10], "total_processes": len(procs)}
        except ImportError:
            return {"error": "psutil not installed", "suggestion": "pip install psutil"}

    def _get_network_info(self) -> Dict[str, Any]:
        import socket

        hostname = socket.gethostname()
        try:
            local_ip = socket.gethostbyname(hostname)
        except socket.gaierror:
            local_ip = "unknown"
        return {
            "hostname": hostname,
            "local_ip": local_ip,
        }

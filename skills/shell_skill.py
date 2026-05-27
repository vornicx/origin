"""
Shell/System Skill para Origin.
Ejecucion controlada de comandos del sistema, apertura de apps y gestion de procesos.
Inspirado en Open-Interface, construido desde cero con controles de seguridad.
"""

import time
import asyncio
import logging
import os
import re
import shutil
from typing import Dict, Any, List, Optional, Set
from datetime import datetime

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.shell")

# ── Timeout maximo por comando (segundos) ──────────────────────────
MAX_TIMEOUT = 120
DEFAULT_TIMEOUT = 30

# ── Comandos/patrones PROHIBIDOS (destructivos o peligrosos) ───────
_BLOCKED_COMMANDS: Set[str] = {
    # Formateo / destruccion de disco
    "format",
    "diskpart",
    "fdisk",
    "mkfs",
    # Borrado masivo
    "rd /s",
    "rmdir /s",
    # Registro de Windows (riesgo de inutilizar el SO)
    "reg delete",
    "regedit",
    # Shutdown / reinicio (salvo que se pida explicitamente)
    "shutdown",
    "restart",
    # Modificacion de bootloader / firmware
    "bcdedit",
    "bcdboot",
    # Acceso a credenciales
    "mimikatz",
    "sekurlsa",
    "lsadump",
    # Crypto-mining
    "xmrig",
    "minergate",
    # PowerShell destructivos
    "remove-item -recurse -force c:",
    "remove-item -recurse -force /",
    "clear-disk",
    # Fork bombs y similares
    ":(){ :|:& };:",
    "%0|%0",
}

# ── Patrones regex prohibidos ──────────────────────────────────────
_BLOCKED_PATTERNS: List[str] = [
    r"rm\s+-rf\s+[/\\]",  # rm -rf /
    r"del\s+/[sfq]\s+[a-zA-Z]:\\",  # del /s C:\
    r"format\s+[a-zA-Z]:",  # format C:
    r">\s*/dev/sda",  # > /dev/sda
    r"dd\s+if=.*of=/dev/",  # dd overwrite disk
    r"mkfs\.",  # mkfs.ext4
    r"net\s+user\s+.*\s+/add",  # crear usuarios
    r"net\s+localgroup\s+administrators",  # elevar privilegios
    r"icacls.*(/grant|/deny).*everyone",  # cambiar permisos globales
    r"schtasks\s+/create",  # crear tareas programadas
    r"sc\s+(create|config|delete)",  # manipular servicios
    r"wmic\s+.*delete",  # WMIC delete
    r"cipher\s+/w:",  # wipe disco
    r"powershell.*-enc\s+",  # scripts codificados (ofuscacion)
    r"curl.*\|\s*(bash|sh|powershell)",  # pipe remoto a shell
    r"wget.*\|\s*(bash|sh|powershell)",
    r"iex\s*\(",  # Invoke-Expression (inyeccion PS)
]

# ── Apps conocidas para apertura rapida (Windows) ──────────────────
_APP_ALIASES: Dict[str, str] = {
    # Sistema
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "terminal": "wt.exe",
    "cmd": "cmd.exe",
    "powershell": "powershell.exe",
    "task manager": "taskmgr.exe",
    "taskmgr": "taskmgr.exe",
    "settings": "start ms-settings:",
    "control panel": "control.exe",
    # Navegadores
    "browser": "start msedge",
    "edge": "start msedge",
    "msedge": "start msedge",
    "chrome": "start chrome",
    "google chrome": "start chrome",
    "firefox": "start firefox",
    # Redes sociales / Sitios populares
    "youtube": "start https://www.youtube.com",
    "google": "start https://www.google.com",
    "github": "start https://www.github.com",
    "gmail": "start https://mail.google.com",
    "facebook": "start https://www.facebook.com",
    "twitter": "start https://www.twitter.com",
    "linkedin": "start https://www.linkedin.com",
    "instagram": "start https://www.instagram.com",
    "whatsapp": "start https://www.whatsapp.com",
    "discord": "start https://discord.com",
    "slack": "start https://slack.com",
    # Desarrollo
    "code": "code",
    "vscode": "code",
    "visual studio": "code",
    "git": "git.exe",
}


class ShellSkill(BaseSkill):
    """
    Skill: Ejecucion de comandos del sistema con seguridad.

    Acciones:
        run     - Ejecuta un comando shell y retorna stdout/stderr
        open    - Abre una aplicacion por nombre o ruta
        kill    - Termina un proceso por nombre o PID
        list    - Lista procesos activos (top por CPU/memoria)
        which   - Busca la ruta de un ejecutable
        env     - Muestra variable(s) de entorno (no sensibles)

    Seguridad:
        - Comandos destructivos bloqueados (rm -rf /, format, etc.)
        - Timeout configurable (max 120s)
        - Salida truncada a 10,000 chars
        - No permite elevacion de privilegios
        - Variables de entorno sensibles censuradas
    """

    VALID_ACTIONS = {"run", "open", "kill", "list", "which", "env"}
    MAX_OUTPUT = 10_000

    def __init__(self):
        super().__init__(name="shell", description="Ejecuta comandos del sistema, abre apps, gestiona procesos")

    # ── Validacion ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "run")
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"

        if action == "run":
            cmd = inputs.get("command", "").strip()
            if not cmd:
                return False, "Se requiere 'command' no vacio"
            if len(cmd) > 2000:
                return False, "Comando demasiado largo (max 2000 chars)"

            # Verificar comandos bloqueados
            blocked = self._check_blocked(cmd)
            if blocked:
                return False, f"Comando bloqueado por seguridad: {blocked}"

        elif action == "open":
            app = inputs.get("app", "") or inputs.get("target", "")
            app = app.strip()
            if not app:
                return False, "Se requiere 'app' o 'target' (nombre o ruta de la aplicacion)"

        elif action == "kill":
            target = inputs.get("target", "").strip()
            pid = inputs.get("pid")
            if not target and not pid:
                return False, "Se requiere 'target' (nombre) o 'pid' (numero)"

        return True, ""

    def _check_blocked(self, command: str) -> Optional[str]:
        """Verifica si un comando esta bloqueado. Retorna razon o None."""
        cmd_lower = command.lower().strip()

        # Check exacto contra lista negra
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return f"patron prohibido: '{blocked}'"

        # Check regex
        for pattern in _BLOCKED_PATTERNS:
            if re.search(pattern, cmd_lower, re.IGNORECASE):
                return "coincide con patron de seguridad"

        return None

    # ── Ejecucion principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs.get("action", "run")

            if action == "run":
                result = await self._run_command(inputs)
            elif action == "open":
                result = await self._open_app(inputs)
            elif action == "kill":
                result = await self._kill_process(inputs)
            elif action == "list":
                result = await self._list_processes(inputs)
            elif action == "which":
                result = self._which(inputs)
            elif action == "env":
                result = self._get_env(inputs)
            else:
                result = {"error": f"Accion '{action}' no implementada"}

            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()

            success = "error" not in result or result.get("error") is None
            return {
                "success": success,
                "result": result,
                "error": result.get("error"),
                "execution_time": elapsed,
            }

        except Exception as e:
            elapsed = round(time.time() - start, 3)
            import traceback

            logger.error(f"Shell skill error: {e}\n{traceback.format_exc()}")
            return {
                "success": False,
                "result": None,
                "error": str(e) if str(e) else type(e).__name__,
                "execution_time": elapsed,
            }

    # ── run: ejecutar comando ──────────────────────────────────────

    async def _run_command(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Ejecuta un comando y captura stdout + stderr."""
        command = inputs["command"].strip()
        timeout = min(inputs.get("timeout", DEFAULT_TIMEOUT), MAX_TIMEOUT)
        cwd = inputs.get("cwd")

        # Validar directorio de trabajo
        if cwd and not os.path.isdir(cwd):
            return {"error": f"Directorio no existe: {cwd}"}

        try:
            import shlex

            args = shlex.split(command, posix=False)
            if args and args[0].lower().endswith(".exe") and os.path.isfile(args[0]):
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            # Truncar salida si es muy larga
            stdout_truncated = len(stdout) > self.MAX_OUTPUT
            stderr_truncated = len(stderr) > self.MAX_OUTPUT

            return {
                "command": command,
                "exit_code": proc.returncode,
                "stdout": stdout[: self.MAX_OUTPUT],
                "stderr": stderr[: self.MAX_OUTPUT],
                "stdout_truncated": stdout_truncated,
                "stderr_truncated": stderr_truncated,
                "error": None if proc.returncode == 0 else f"Exit code: {proc.returncode}",
            }

        except asyncio.TimeoutError:
            # Matar proceso que excedio timeout
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "command": command,
                "error": f"Timeout: el comando excedio {timeout}s",
                "exit_code": -1,
                "stdout": "",
                "stderr": "",
            }

    # ── open: abrir aplicacion ─────────────────────────────────────

    async def _open_app(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Abre una aplicacion por nombre o ruta."""
        # Aceptar tanto "app" como "target" como nombre del parámetro
        app = inputs.get("app") or inputs.get("target")
        if not app:
            return {"error": "Se requiere parámetro 'app' o 'target'"}
        app = app.strip()
        args = inputs.get("args", "")

        # Resolver alias
        resolved = _APP_ALIASES.get(app.lower(), app)

        # Si es ruta completa, verificar que existe
        if os.path.sep in resolved or ":" in resolved:
            if not os.path.exists(resolved) and not resolved.startswith("start "):
                return {"error": f"Aplicacion no encontrada: {resolved}"}

        # Construir comando
        if resolved.startswith("start "):
            cmd = f"{resolved} {args}".strip()
        elif os.path.sep in resolved or ":" in resolved:
            cmd = f'start "" "{resolved}" {args}'.strip()
        else:
            cmd = f"start {resolved} {args}".strip()

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)

            return {
                "app": app,
                "resolved": resolved,
                "command": cmd,
                "launched": True,
                "error": None,
            }
        except asyncio.TimeoutError:
            # start suele retornar rapido; timeout aqui es aceptable
            return {
                "app": app,
                "resolved": resolved,
                "launched": True,
                "note": "App lanzada (el proceso sigue corriendo)",
                "error": None,
            }

    # ── kill: matar proceso ────────────────────────────────────────

    async def _kill_process(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Termina un proceso por nombre o PID."""
        target = inputs.get("target", "").strip()
        pid = inputs.get("pid")

        if pid:
            cmd = f"taskkill /PID {int(pid)} /F"
        elif target:
            # Sanitizar nombre
            safe_name = re.sub(r"[^a-zA-Z0-9._-]", "", target)
            if not safe_name:
                return {"error": "Nombre de proceso invalido"}
            cmd = f"taskkill /IM {safe_name} /F"
        else:
            return {"error": "Se requiere 'target' o 'pid'"}

        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)

        return {
            "target": target or f"PID {pid}",
            "command": cmd,
            "exit_code": proc.returncode,
            "output": stdout.decode("utf-8", errors="replace").strip(),
            "killed": proc.returncode == 0,
            "error": None if proc.returncode == 0 else stderr.decode("utf-8", errors="replace").strip(),
        }

    # ── list: listar procesos ──────────────────────────────────────

    async def _list_processes(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Lista procesos ordenados por uso de recursos."""
        top_n = min(inputs.get("top", 15), 50)
        sort_by = inputs.get("sort", "memory")  # memory | cpu

        try:
            import psutil

            procs = []
            for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info", "status"]):
                try:
                    info = p.info
                    mem_mb = info["memory_info"].rss / (1024 * 1024) if info.get("memory_info") else 0
                    procs.append(
                        {
                            "pid": info["pid"],
                            "name": info["name"],
                            "cpu_percent": info.get("cpu_percent", 0) or 0,
                            "memory_mb": round(mem_mb, 1),
                            "status": info.get("status", "unknown"),
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            key = "memory_mb" if sort_by == "memory" else "cpu_percent"
            procs.sort(key=lambda x: x[key], reverse=True)

            return {
                "processes": procs[:top_n],
                "total_running": len(procs),
                "sorted_by": sort_by,
                "error": None,
            }

        except ImportError:
            # Fallback sin psutil: usar tasklist
            proc = await asyncio.create_subprocess_shell(
                "tasklist /FO CSV /NH",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            lines = stdout.decode("utf-8", errors="replace").strip().split("\n")

            procs = []
            for line in lines[:top_n]:
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 5:
                    procs.append(
                        {
                            "name": parts[0],
                            "pid": parts[1],
                            "memory": parts[4],
                        }
                    )

            return {
                "processes": procs,
                "total_running": len(lines),
                "sorted_by": "default",
                "note": "Instala psutil para info detallada de CPU",
                "error": None,
            }

    # ── which: buscar ejecutable ───────────────────────────────────

    def _which(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Busca la ruta de un ejecutable en PATH."""
        name = inputs.get("name", "").strip()
        if not name:
            return {"error": "Se requiere 'name'"}

        path = shutil.which(name)
        return {
            "name": name,
            "path": path,
            "found": path is not None,
            "error": None,
        }

    # ── env: variables de entorno ──────────────────────────────────

    # Variables que se censuran por seguridad
    _SENSITIVE_VARS = {
        "api_key",
        "secret",
        "password",
        "token",
        "credential",
        "private_key",
        "access_key",
        "auth",
    }

    def _get_env(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Retorna variables de entorno, censurando las sensibles."""
        var_name = inputs.get("name", "").strip()

        if var_name:
            # Una sola variable
            val = os.environ.get(var_name)
            if val is None:
                return {"name": var_name, "value": None, "found": False, "error": None}

            is_sensitive = any(s in var_name.lower() for s in self._SENSITIVE_VARS)
            return {
                "name": var_name,
                "value": "***CENSURADO***" if is_sensitive else val,
                "found": True,
                "censored": is_sensitive,
                "error": None,
            }
        else:
            # Listar todas (censurando sensibles)
            env_list = {}
            for k, v in sorted(os.environ.items()):
                is_sensitive = any(s in k.lower() for s in self._SENSITIVE_VARS)
                env_list[k] = "***CENSURADO***" if is_sensitive else v[:200]

            return {
                "variables": env_list,
                "total": len(env_list),
                "error": None,
            }

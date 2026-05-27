"""
Silent launcher para Origin — ejecutado via pythonw.exe (sin consola).

Lanza el backend FastAPI en background y muestra notificación de inicio.
Usado por start.bat, origin.cmd, y el registro de arranque de Windows.

Comportamiento:
  - Inicia uvicorn api.main:app en segundo plano
  - Muestra toast notification "Origin activado"
  - El tray icon se auto-inicia (vía api.main lifespan)
  - Wake word "Hey Origin" se auto-inicia (vía api.main lifespan)
"""

import os
import subprocess
import sys
import time
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHONW = ROOT / "venv" / "Scripts" / "pythonw.exe"
UV_PYTHONW = ROOT / ".uv-python" / "cpython-3.11.15-windows-x86_64-none" / "pythonw.exe"
UV_PYTHONW_V2 = ROOT / ".uv-python" / "cpython-3.11-windows-x86_64-none" / "pythonw.exe"


def _python_works(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        result = subprocess.run(
            [str(path), "--version"],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=4,
        )
        return result.returncode == 0
    except Exception:
        return False


def _show_notification():
    """Muestra notificación toast de que Origin está activo."""
    try:
        from winotify import Notification
        notif = Notification(
            app_id="Origin",
            title="Origin activado",
            msg="Icono 'J' en la bandeja del sistema. Di 'Hey Origin' o pulsa Ctrl+Alt+J.",
            duration="short",
            icon=str(ROOT / "frontend" / "public" / "favicon.ico") if (ROOT / "frontend" / "public" / "favicon.ico").exists() else None,
        )
        notif.add_actions(label="Abrir UI", launch="http://localhost:9001")
        notif.show()
    except Exception:
        pass  # winotify no disponible


def _wait_then_notify():
    """Espera a que el servidor esté listo y muestra notificación."""
    max_wait = 30
    for _ in range(max_wait):
        try:
            import socket
            s = socket.socket()
            s.settimeout(0.5)
            s.connect(("127.0.0.1", 9001))
            s.close()
            time.sleep(1)
            _show_notification()
            return
        except Exception:
            time.sleep(1)


def main():
    # Ensure we're in the project root (critical for relative paths in subprocess)
    os.chdir(str(ROOT))

    py = str(UV_PYTHONW) if _python_works(UV_PYTHONW) else str(UV_PYTHONW_V2) if _python_works(UV_PYTHONW_V2) else str(VENV_PYTHONW) if _python_works(VENV_PYTHONW) else sys.executable
    env = os.environ.copy()

    # Build PYTHONPATH: site-packages + pywin32 subdirs (needed when bypassing venv activation)
    site = ROOT / "venv" / "Lib" / "site-packages"
    pythonpath_parts = [str(site)]
    for sub in ("win32", os.path.join("win32", "lib"), "Pythonwin"):
        p = site / sub
        if p.exists():
            pythonpath_parts.append(str(p))
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)

    creationflags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creationflags = subprocess.CREATE_NO_WINDOW

    proc = subprocess.Popen(
        [py, "-m", "uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "9001"],
        cwd=str(ROOT),
        env=env,
        creationflags=creationflags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    # Thread: esperar servidor y mostrar notificación
    threading.Thread(target=_wait_then_notify, daemon=True).start()

    # Esperar al proceso (mantiene pythonw vivo)
    proc.wait()


if __name__ == "__main__":
    main()

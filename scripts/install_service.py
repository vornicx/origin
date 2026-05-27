"""
Instala Origin como Windows Service nativo.

Ventajas sobre Scheduled Task:
  - Arranca antes del login del usuario
  - Se reinicia automáticamente si crashea (con políticas de reinicio)
  - Aparece en services.msc
  - Logging nativo al Windows Event Log
  - Control de permisos granular
  - Dependencia con otros servicios (ej: PostgreSQL)

Usa nssm (Non-Sucking Service Manager) si está disponible.
Si nssm no está, usa sc.exe (built-in de Windows).

Uso:
    python scripts/install_service.py install
    python scripts/install_service.py remove
    python scripts/install_service.py status
"""

import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
SERVICE_NAME = "Origin"
SERVICE_DISPLAY_NAME = "Origin - Personal AI Assistant"
SERVICE_DESCRIPTION = "Asistente IA personal con habilidades de automatizacion, control de sistema y voz."

NSSM_PATH = ROOT / "tools" / "nssm.exe"


def _check_nssm() -> bool:
    """Verifica si nssm está disponible."""
    if NSSM_PATH.exists():
        return True
    try:
        subprocess.run(["nssm", "version"], capture_output=True)
        return True
    except FileNotFoundError:
        return False


def _install_with_nssm():
    """Instala como servicio usando nssm."""
    nssm = str(NSSM_PATH) if NSSM_PATH.exists() else "nssm"
    launcher = str(ROOT / "origin_launcher.pyw")

    subprocess.run([nssm, "install", SERVICE_NAME, str(VENV_PYTHON), launcher], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "DisplayName", SERVICE_DISPLAY_NAME], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "Description", SERVICE_DESCRIPTION], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "Start", "SERVICE_AUTO_START"], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppDirectory", str(ROOT)], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppStdout", str(ROOT / "service.log")], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppStderr", str(ROOT / "service.log")], check=True)

    # Restart policy: restart after 10s, 3 retries, then 60s
    subprocess.run([nssm, "set", SERVICE_NAME, "AppRestartDelay", "10000"], check=True)
    subprocess.run([nssm, "set", SERVICE_NAME, "AppActions", "Exit/30000/Restart/3/60000/Restart"], check=True)

    print(f"[OK] Servicio '{SERVICE_NAME}' instalado con nssm")
    print(f"    Iniciar: nssm start {SERVICE_NAME}")
    print(f"    Detener: nssm stop {SERVICE_NAME}")


def _install_with_sc():
    """Instala como servicio usando sc.exe (built-in)."""
    launcher = str(VENV_PYTHON) + " " + str(ROOT / "origin_launcher.pyw")

    subprocess.run([
        "sc", "create", SERVICE_NAME,
        "binPath=", launcher,
        "start=", "auto",
        "DisplayName=", SERVICE_DISPLAY_NAME,
    ], check=True)

    subprocess.run([
        "sc", "description", SERVICE_NAME, SERVICE_DESCRIPTION,
    ], check=True)

    # Restart policy via sc failure
    subprocess.run([
        "sc", "failure", SERVICE_NAME,
        "reset=", "86400",
        "actions=", "restart/10000/restart/30000/restart/60000",
    ], check=True)

    print(f"[OK] Servicio '{SERVICE_NAME}' instalado con sc.exe")
    print(f"    Iniciar: sc start {SERVICE_NAME}")
    print(f"    Detener: sc stop {SERVICE_NAME}")


def install():
    """Instala Origin como Windows Service."""
    print(f"Instalando servicio Windows: {SERVICE_NAME}")
    print("-" * 50)

    if not VENV_PYTHON.exists():
        print(f"[ERROR] No se encuentra: {VENV_PYTHON}")
        print("        Crea el venv: python -m venv venv")
        return False

    # Verificar permisos de administrador
    try:
        import ctypes
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if not is_admin:
        print("[ERROR] Se requieren permisos de administrador.")
        print("        Ejecuta como Administrador.")
        return False

    if _check_nssm():
        _install_with_nssm()
    else:
        _install_with_sc()

    print()
    print("Para completar la instalacion:")
    print("  sc start Origin    (iniciar servicio)")
    print("  sc stop Origin     (detener servicio)")
    print("  sc query Origin    (ver estado)")
    print()
    print("O desde PowerShell:")
    print("  Start-Service Origin")
    print("  Stop-Service Origin")
    print("  Get-Service Origin")
    print()
    print("NOTA: Si usas sc.exe, el servicio se ejecuta con python.exe")
    print("      (no pythonw.exe). Veras una consola al iniciar. Es normal.")
    return True


def remove():
    """Elimina el servicio."""
    print(f"Eliminando servicio: {SERVICE_NAME}")
    subprocess.run(["sc", "stop", SERVICE_NAME], capture_output=True)
    if _check_nssm():
        nssm = str(NSSM_PATH) if NSSM_PATH.exists() else "nssm"
        subprocess.run([nssm, "remove", SERVICE_NAME, "confirm"], check=True)
    else:
        subprocess.run(["sc", "delete", SERVICE_NAME], check=True)
    print(f"[OK] Servicio '{SERVICE_NAME}' eliminado")


def status():
    """Estado del servicio."""
    result = subprocess.run(["sc", "query", SERVICE_NAME], capture_output=True, text=True)
    if result.returncode == 0:
        print(result.stdout)
    else:
        print(f"Servicio '{SERVICE_NAME}' no instalado")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: python {sys.argv[0]} [install|remove|status]")
        sys.exit(1)

    action = sys.argv[1]
    if action == "install":
        install()
    elif action == "remove":
        remove()
    elif action == "status":
        status()
    else:
        print(f"Accion desconocida: {action}")
        sys.exit(1)

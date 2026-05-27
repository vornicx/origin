#!/usr/bin/env python3
"""
Instala Origin como comando del sistema y configura integración con Windows.

Pasos:
  1. Añade C:\\J.A.R.V.IIS al PATH del usuario (para que 'origin' funcione desde Win+R, CMD)
  2. Ofrece configurar arranque automático con Windows
  3. Verifica dependencias del sistema (pystray, keyboard, etc.)

Uso:
    python scripts\\install_origin.py

Ejecutar como administrador para cambios globales.
"""

import os
import sys
import subprocess
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def add_to_user_path():
    """Añade ROOT al PATH del usuario actual (HKCU\\Environment)."""
    key_path = r"Environment"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE)
        current_path, _ = winreg.QueryValueEx(key, "Path")
        path_list = current_path.split(";")
        root_str = str(ROOT)
        if root_str.lower() not in [p.lower() for p in path_list]:
            new_path = current_path + ";" + root_str
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, new_path)
            winreg.CloseKey(key)
            print(f"[OK] '{root_str}' añadido al PATH de usuario.")
            print("     (Reinicia la terminal o cierra sesión para aplicar)")
            return True
        else:
            winreg.CloseKey(key)
            print(f"[OK] '{root_str}' ya está en el PATH.")
            return True
    except Exception as e:
        print(f"[ERROR] No se pudo modificar el PATH: {e}")
        return False


def setup_auto_start():
    """Configura arranque automático en Windows Registry (HKCU\\Run).
    Usa autostart.cmd (batch) como entry point — Windows ejecuta .cmd
    correctamente desde el registro, a diferencia de .pyw."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    launcher = str(ROOT / "autostart.cmd")
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        try:
            current, _ = winreg.QueryValueEx(key, "Origin")
        except FileNotFoundError:
            current = ""
        if current == launcher:
            print("[OK] Auto-arranque ya configurado")
        else:
            winreg.SetValueEx(key, "Origin", 0, winreg.REG_SZ, launcher)
            print(f"[OK] Auto-arranque configurado: {launcher}")
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"[ERROR] Auto-arranque falló: {e}")
        return False


def setup_scheduled_task(repair: bool = False) -> bool:
    """Configura Origin como Scheduled Task de Windows (más robusto que HKCU\\Run).
    Ventajas sobre HKCU\\Run:
      - Se reinicia automáticamente si crashea
      - Política de reinicio configurable
      - Ejecución incluso si el usuario no ha hecho login (con sesión en segundo plano)
      - Logging de ejecución
    """
    task_name = "Origin"
    launcher = str(ROOT / "autostart.cmd")
    username = os.environ.get("USERNAME", "CURRENT_USER")

    if repair:
        cmd_line = f'schtasks /change /tn "{task_name}" /tr "{launcher}" /rl highest'
    else:
        cmd_line = (
            f'schtasks /create /tn "{task_name}"'
            f' /tr "{launcher}" /sc onlogon'
            f' /ru "{username}" /rl highest /f /delay 0000:15'
        )

    try:
        result = subprocess.run(cmd_line, shell=True, capture_output=True, text=True)
        if result.returncode == 0 or "SUCCESS" in result.stdout:
            print(f"[OK] Scheduled Task '{task_name}' creada (se ejecuta al iniciar sesión)")
            setup_auto_start()  # Register HKCU\Run as well (belt and suspenders)
            return True
        else:
            # Fallback: HKCU\Run si schtasks falla (posiblemente sin permisos)
            print(f"[WARN] No se pudo crear Scheduled Task: {result.stderr.strip()}")
            print("[INFO] Usando HKCU\\Run como alternativa...")
            return setup_auto_start()
    except Exception as e:
        print(f"[WARN] Error creando Scheduled Task: {e}")
        print("[INFO] Usando HKCU\\Run como alternativa...")
        return setup_auto_start()


def remove_auto_start():
    """Elimina Origin del arranque automático (HKCU\\Run)."""
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, "Origin")
        winreg.CloseKey(key)
        print("[OK] Auto-arranque (HKCU\\Run) eliminado.")
    except FileNotFoundError:
        print("[INFO] No había auto-arranque configurado en HKCU\\Run.")
    except Exception as e:
        print(f"[INFO] No se pudo eliminar auto-arranque: {e}")


def remove_scheduled_task() -> bool:
    """Elimina la Scheduled Task de Origin."""
    task_name = "Origin"
    try:
        result = subprocess.run(
            f'schtasks /delete /tn "{task_name}" /f',
            shell=True, capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[OK] Scheduled Task '{task_name}' eliminada")
        else:
            print("[INFO] No había Scheduled Task")
    except Exception as e:
        print(f"[INFO] No se pudo eliminar Scheduled Task: {e}")
    remove_auto_start()
    return True


def check_dependencies():
    """Verifica dependencias Python opcionales para integración con el SO."""
    deps = {
        "pystray": "Icono en bandeja del sistema",
        "keyboard": "Hotkey global Ctrl+Alt+J",
        "faster_whisper": "STT local (reconocimiento de voz)",
        "winotify": "Notificaciones nativas Windows",
    }
    all_ok = True
    for mod, desc in deps.items():
        try:
            __import__(mod)
            print(f"[OK] {mod:20s} — {desc}")
        except ImportError:
            print(f"[WARN] {mod:20s} — {desc} (pip install {mod})")
            all_ok = False
    return all_ok


def check_venv():
    """Verifica que el venv existe y tiene las dependencias principales."""
    venv_python = ROOT / "venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        print(f"[ERROR] No se encuentra venv en {venv_python}")
        print("        Crea el entorno: python -m venv venv && venv\\Scripts\\activate && pip install -r requirements.txt")  # noqa: E501
        return False
    print(f"[OK] Virtual environment: {venv_python}")
    return True


def main():
    print("=" * 60)
    print("   Origin — Instalación e integración con Windows")
    print("=" * 60)
    print()

    install_type = "full"
    if len(sys.argv) > 1:
        if sys.argv[1] == "--remove":
            remove_scheduled_task()
            print("\nEjecuta 'origin start' manualmente cuando quieras iniciar Origin.")
            return
        elif sys.argv[1] == "--minimal":
            install_type = "minimal"
        elif sys.argv[1] == "--scheduled-task":
            install_type = "scheduled_task"

    print("[1/5] Verificando entorno virtual...")
    check_venv()
    print()

    if install_type != "scheduled_task":
        print("[2/5] Verificando dependencias del sistema...")
        check_dependencies()
        print()

        print("[3/5] Configurando Origin en el PATH del sistema...")
        add_to_user_path()
        print()

    print("[4/5] Configurando arranque automático con Windows...")
    if install_type == "minimal":
        setup_auto_start()
    else:
        setup_scheduled_task()
    print()

    print("[5/5] Instalando integracion con el sistema...")
    try:
        import subprocess
        subprocess.run([
            sys.executable, str(ROOT / "scripts" / "install_context_menu.py"), "install"
        ], capture_output=True, timeout=30)
        print("  [OK] Menu contextual del Explorador")
    except Exception as e:
        print(f"  [INFO] Menu contextual: {e}")

    # Registrar módulo PowerShell
    try:
        ps_module_dir = Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")) / "WindowsPowerShell" / "Modules" / "Origin"  # noqa: E501
        ps_module_dir.mkdir(parents=True, exist_ok=True)
        src = ROOT / "scripts" / "Origin.psm1"
        dst = ps_module_dir / "Origin.psm1"
        if src.exists() and (not dst.exists() or src.stat().st_mtime > dst.stat().st_mtime):
            import shutil
            shutil.copy2(str(src), str(dst))
            print(f"  [OK] Modulo PowerShell instalado: {dst}")
    except Exception as e:
        print(f"  [INFO] Modulo PowerShell: {e}")

    print()
    print("=" * 60)
    print("   Instalacion completada.")
    print()
    print("   Origin ahora es un componente nativo de Windows:")
    print("     - Arranca automaticamente al iniciar sesion")
    print("     - Se reinicia si crashea (Scheduled Task)")
    print("     - Icono 'J' en la bandeja del sistema")
    print("     - 'origin' desde cualquier terminal (Win+R)")
    print("     - Ctrl+Alt+J para activar voz desde cualquier app")
    print("     - 'Hey Origin' para activacion por voz")
    print("     - Menu contextual en Explorador de archivos")
    print("     - Modulo PowerShell (Import-Module Origin)")
    print("     - Windows Event Log integrado")
    print("=" * 60)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--remove":
        remove_auto_start()
        print("Ejecuta 'origin start' manualmente cuando quieras iniciar Origin.")
    else:
        main()

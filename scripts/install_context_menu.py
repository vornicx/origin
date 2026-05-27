"""
Registra Origin en el menú contextual del Explorador de Windows.

Añade opciones como:
  - "Ask Origin" al hacer clic derecho en cualquier archivo
  - "Analyze with Origin" para archivos de código
  - "Send to Origin" desde el menú Enviar a

Uso:
    python scripts/install_context_menu.py install
    python scripts/install_context_menu.py remove
"""

import os
import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
Origin_URL = "http://localhost:9001"

# GUID único para Origin
Origin_GUID = "{B4F5A3C2-D1E0-4F6A-8B7C-9D0E1F2A3B4C}"
Origin_APP_USER_MODEL_ID = "Origin"


def _add_reg(key_path, name, value):
    """Añade valor al registro."""
    try:
        key = winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path)
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.CloseKey(key)
        return True
    except Exception as e:
        print(f"  [WARN] Error en {key_path}: {e}")
        return False


def _remove_reg(key_path, name=None):
    """Elimina valor o clave del registro."""
    try:
        if name:
            key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, key_path, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, name)
            winreg.CloseKey(key)
        else:
            winreg.DeleteKey(winreg.HKEY_CLASSES_ROOT, key_path)
        return True
    except Exception:
        return False


def install():
    """Instala las entradas de menú contextual."""
    print("Instalando integracion con Explorador de Windows...")
    print("-" * 50)

    # 1. Menú contextual para todos los archivos (*)
    print("  [1/4] Menu contextual para archivos...")
    _add_reg("*\\shell\\Origin_Ask", "", "Ask Origin")
    _add_reg("*\\shell\\Origin_Ask\\command", "", f'cmd /c start {Origin_URL}/?file="%1"')
    _add_reg("*\\shell\\Origin_Ask", "Icon", f"{ROOT}\\frontend\\public\\favicon.ico")

    # 2. Menú contextual para archivos de código
    print("  [2/4] Menu contextual para codigo...")
    for ext in [".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".md", ".rs", ".go"]:
        _add_reg(f"{ext}\\shell\\Origin_Analyze", "", "Analyze with Origin")
        _add_reg(f"{ext}\\shell\\Origin_Analyze\\command", "", f'cmd /c start {Origin_URL}/?analyze="%1"')

    # 3. Menú para directorios
    print("  [3/4] Menu contextual para directorios...")
    _add_reg("Directory\\shell\\Origin_Explore", "", "Explore with Origin")
    _add_reg("Directory\\shell\\Origin_Explore\\command", "", f'cmd /c start {Origin_URL}/?dir="%1"')

    # 4. Enviar a
    print("  [4/4] Menu Enviar a...")
    send_to = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "SendTo"
    shortcut_path = send_to / "Origin.urllink"
    try:
        shortcut_path.write_text(f"[InternetShortcut]\nURL={Origin_URL}\n", encoding="utf-8")
        print(f"  [OK] Acceso directo en SendTo: {shortcut_path}")
    except Exception as e:
        print(f"  [WARN] Error en SendTo: {e}")

    print()
    print("[OK] Integracion con Explorador completada.")
    print("    Haz clic derecho en cualquier archivo > Ask Origin")
    print("    O Enviar a > Origin")


def remove():
    """Elimina las entradas de menú contextual."""
    print("Eliminando integracion con Explorador de Windows...")
    _remove_reg("*\\shell\\Origin_Ask\\command")
    _remove_reg("*\\shell\\Origin_Ask")
    for ext in [".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".json", ".md", ".rs", ".go"]:
        _remove_reg(f"{ext}\\shell\\Origin_Analyze\\command")
        _remove_reg(f"{ext}\\shell\\Origin_Analyze")
    _remove_reg("Directory\\shell\\Origin_Explore\\command")
    _remove_reg("Directory\\shell\\Origin_Explore")
    print("[OK] Integracion eliminada.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Uso: python {sys.argv[0]} [install|remove]")
        sys.exit(1)
    if sys.argv[1] == "install":
        install()
    elif sys.argv[1] == "remove":
        remove()
    else:
        print(f"Accion desconocida: {sys.argv[1]}")

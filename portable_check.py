#!/usr/bin/env python3
"""
portable_check.py — Verifica que Origin puede arrancar desde cualquier ruta/unidad.

Uso:
    python portable_check.py
    python portable_check.py --fix      # aplica correcciones automáticas
    python portable_check.py --summary  # solo PASS/FAIL sin detalles

Salida: lista de comprobaciones con [OK] / [WARN] / [FAIL].
Código de salida: 0 = todo OK, 1 = algún FAIL.
"""

import os
import sys
import subprocess
import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIX_MODE = "--fix" in sys.argv
SUMMARY_MODE = "--summary" in sys.argv

_PASS = "[OK]  "
_WARN = "[WARN]"
_FAIL = "[FAIL]"

results = []


def check(label: str, ok: bool, detail: str = "", warn: bool = False):
    tag = _PASS if ok else (_WARN if warn else _FAIL)
    msg = f"{tag} {label}"
    if detail and not SUMMARY_MODE:
        msg += f"\n       {detail}"
    results.append((ok or warn, msg))
    print(msg)


# ── 1. Python interpreters ────────────────────────────────────────────────────

UV_PY = ROOT / ".uv-python" / "cpython-3.11.15-windows-x86_64-none" / "python.exe"
UV_PY_V2 = ROOT / ".uv-python" / "cpython-3.11-windows-x86_64-none" / "python.exe"
UV_PYW = UV_PY.with_name("pythonw.exe")
UV_PYW_V2 = UV_PY_V2.with_name("pythonw.exe")
VENV_PY = ROOT / "venv" / "Scripts" / "python.exe"
VENV_PYW = ROOT / "venv" / "Scripts" / "pythonw.exe"

# Use whichever uv-python exists
if not UV_PY.exists() and UV_PY_V2.exists():
    UV_PY = UV_PY_V2
    UV_PYW = UV_PYW_V2


def _python_works(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        r = subprocess.run(
            [str(path), "--version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        return r.returncode == 0
    except Exception:
        return False


uv_ok = _python_works(UV_PY)
check("Portable Python (.uv-python)", uv_ok,
      str(UV_PY) if uv_ok else f"NOT FOUND: {UV_PY}")

check("Portable pythonw (.uv-python)", _python_works(UV_PYW),
      str(UV_PYW), warn=True)

venv_ok = _python_works(VENV_PY)
venv_label = "venv Python"
if not venv_ok:
    cfg = ROOT / "venv" / "pyvenv.cfg"
    home = ""
    if cfg.exists():
        for line in cfg.read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("home"):
                home = line.split("=", 1)[-1].strip()
                break
    detail = f"pyvenv.cfg home = {home} (non-portable, scripts use .uv-python instead)"
    check(venv_label, False, detail, warn=True)
else:
    check(venv_label, True, str(VENV_PY))

# ── 2. PYTHONPATH — packages importable ──────────────────────────────────────

SITE = ROOT / "venv" / "Lib" / "site-packages"
check("venv site-packages directory", SITE.is_dir(), str(SITE))

if SITE.is_dir() and uv_ok:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SITE)
    core_pkgs = ["fastapi", "uvicorn", "pydantic", "sqlalchemy",
                 "aiohttp", "dotenv", "cryptography", "httpx"]
    code = (
        "import sys,os; sys.path.insert(0, '.'); "
        "missing=[]; "
        + "".join(f"exec('try:\\n __import__(\\'{p}\\')\\nexcept ImportError: missing.append(\\'{p}\\')');" for p in core_pkgs)
        + "print('MISSING:'+','.join(missing) if missing else 'OK')"
    )
    try:
        r = subprocess.run(
            [str(UV_PY), "-c", code],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
        )
        out = r.stdout.decode(errors="replace").strip()
        ok = out == "OK"
        check("Core packages via PYTHONPATH", ok,
              out if not ok else "fastapi, uvicorn, pydantic, sqlalchemy, aiohttp …")
    except Exception as e:
        check("Core packages via PYTHONPATH", False, str(e))

# ── 3. App config loads ───────────────────────────────────────────────────────

if uv_ok:
    env2 = os.environ.copy()
    env2["PYTHONPATH"] = str(SITE)
    code2 = (
        "import sys,os; os.chdir(r'" + str(ROOT).replace("\\", "\\\\") + "'); "
        "sys.path.insert(0,'.'); "
        "from core.config import settings, PROJECT_ROOT; "
        "print('ROOT:'+str(PROJECT_ROOT))"
    )
    try:
        r2 = subprocess.run(
            [str(UV_PY), "-c", code2],
            cwd=str(ROOT), env=env2,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        )
        out2 = r2.stdout.decode(errors="replace").strip()
        ok2 = "ROOT:" in out2
        check("core.config loads", ok2,
              out2.replace("ROOT:", "PROJECT_ROOT = ") if ok2 else r2.stderr.decode(errors="replace")[:200])
    except Exception as e:
        check("core.config loads", False, str(e))

# ── 4. Critical source files ──────────────────────────────────────────────────

for rel in ["api/main.py", "core/config.py", "core/mind.py",
            "origin_launcher.pyw", "origin.cmd", "start.bat", "stop.bat", "autostart.cmd"]:
    p = ROOT / rel
    check(f"File: {rel}", p.exists(), str(p))

# ── 5. Frontend distribution ──────────────────────────────────────────────────

dist = ROOT / "frontend" / "dist"
check("frontend/dist (built UI)", dist.is_dir(),
      "Run: cd frontend && npm run build" if not dist.is_dir() else str(dist))

index = dist / "index.html"
check("frontend/dist/index.html", index.exists(), warn=not index.exists())

# Tauri binary (optional)
tauri_debug = ROOT / "frontend" / "src-tauri" / "target" / "debug" / "origin.exe"
tauri_release = ROOT / "frontend" / "src-tauri" / "target" / "release" / "origin.exe"
tauri_ok = tauri_debug.exists() or tauri_release.exists()
check("Tauri binary (origin.exe)", tauri_ok,
      "Not built — use 'origin dev-web' for browser mode (portable default)",
      warn=not tauri_ok)

# ── 6. Data directory ─────────────────────────────────────────────────────────

data = ROOT / "data"
check("data/ directory", data.is_dir())

db = data / "origin.db"
check("data/origin.db", db.exists(),
      "DB will be created on first run" if not db.exists() else f"{db.stat().st_size // 1024} KB",
      warn=not db.exists())

env_file = ROOT / ".env"
env_ok = env_file.exists()
check(".env file", env_ok,
      "Copy .env.example to .env and fill API keys" if not env_ok else str(env_file))

# ── 7. No hardcoded absolute paths in launch scripts ─────────────────────────

launch_files = ["origin.cmd", "start.bat", "stop.bat", "autostart.cmd", "origin_launcher.pyw"]
abs_pattern_bad = r"C:\Users\\" + "\\"  # type: ignore  # double-escaped for display
found_hardcode = []
for fname in launch_files:
    p = ROOT / fname
    if not p.exists():
        continue
    text = p.read_text(encoding="utf-8", errors="replace")
    for bad in ["C:\\Users\\", "C:/Users/", "C:\\Origin\\", "C:/Origin/"]:
        if bad.lower() in text.lower():
            found_hardcode.append(f"{fname}: contains '{bad}'")

check("No hardcoded user paths in launchers",
      not found_hardcode,
      "; ".join(found_hardcode) if found_hardcode else "")

# ── 8. venv/pyvenv.cfg portability ───────────────────────────────────────────

cfg_path = ROOT / "venv" / "pyvenv.cfg"
if cfg_path.exists():
    cfg_text = cfg_path.read_text(encoding="utf-8-sig")  # strips BOM if present
    cfg_home = ""
    for line in cfg_text.splitlines():
        if line.lower().startswith("home"):
            cfg_home = line.split("=", 1)[-1].strip()
            break

    cfg_portable = (
        str(ROOT).lower() in cfg_home.lower()
        or str(UV_PY.parent).lower() in cfg_home.lower()
    )

    if cfg_portable:
        check("venv/pyvenv.cfg points to bundled Python", True, cfg_home)
    else:
        detail_cfg = f"home = {cfg_home} (points outside project)"
        if FIX_MODE and uv_ok:
            new_home = str(UV_PY.parent)
            new_exe = str(UV_PY)
            new_lines = []
            for l in cfg_text.splitlines():
                ll = l.lower()
                if ll.startswith("home"):
                    new_lines.append(f"home = {new_home}")
                elif ll.startswith("executable"):
                    new_lines.append(f"executable = {new_exe}")
                elif ll.startswith("command"):
                    new_lines.append(f"command = {new_exe} -m venv {ROOT / 'venv'}")
                else:
                    new_lines.append(l)
            cfg_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            check("venv/pyvenv.cfg updated to bundled Python", True,
                  f"home now = {new_home}")
        else:
            hint = "  (run with --fix to update automatically)"
            check("venv/pyvenv.cfg points to bundled Python", False,
                  detail_cfg + hint, warn=True)

# ── 9. Simulate path-change: verify ROOT detection ───────────────────────────

if uv_ok:
    # Run portable_check's own ROOT detection from a different CWD (e.g. C:\)
    env3 = os.environ.copy()
    env3["PYTHONPATH"] = str(SITE)
    code3 = (
        f"from pathlib import Path; "
        f"root = Path(r'{str(__file__)}').resolve().parent; "
        f"uv = root / '.uv-python' / 'cpython-3.11.15-windows-x86_64-none' / 'python.exe'; "
        f"print('ROOT_DETECT:OK' if uv.exists() else 'ROOT_DETECT:FAIL:' + str(uv))"
    )
    try:
        r3 = subprocess.run(
            [str(UV_PY), "-c", code3],
            cwd="C:\\",  # different CWD
            env=env3,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5,
        )
        out3 = r3.stdout.decode(errors="replace").strip()
        check("Path detection from different CWD", "ROOT_DETECT:OK" in out3, out3)
    except Exception as e:
        check("Path detection from different CWD", False, str(e))

# ── Summary ───────────────────────────────────────────────────────────────────

print()
print("=" * 60)
total = len(results)
passed = sum(1 for ok, _ in results if ok)
failed = total - passed
if failed == 0:
    print(f"  PORTABLE: {passed}/{total} checks passed — ready for USB deployment")
else:
    print(f"  {passed}/{total} checks passed, {failed} issues found")
    print("  Run with --fix to apply automatic corrections")
print("=" * 60)

sys.exit(0 if failed == 0 else 1)

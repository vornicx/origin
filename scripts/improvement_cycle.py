#!/usr/bin/env python3
"""
improvement_cycle.py — Ciclo de mejora externo para Origin.

Script autónomo (sin dependencias de Origin) que:
1. Ejecuta pytest en tests/ y captura resultados
2. Escanea archivos .log en busca de tracebacks y errores
3. Cuenta skills registradas vs skills en disco
4. Ejecuta ast.parse en skills/ y core/ para detectar errores sintácticos
5. Llama al endpoint /dashboard/improvement del servidor (si está vivo)
6. Produce un reporte JSON con indicadores de salud
7. Imprime el reporte a stdout

Uso:
    python improvement_cycle.py
"""

import ast
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Configuración ──────────────────────────────────────────────────────────────
ORIGIN_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ORIGIN_ROOT / "tests"
SKILLS_DIR = ORIGIN_ROOT / "skills"
CORE_DIR = ORIGIN_ROOT / "core"
IMPROVEMENT_URL = "http://localhost:9001/dashboard/improvement"


# ── 1. Ejecutar pytest ────────────────────────────────────────────────────────
def run_tests() -> dict:
    """Corre pytest en tests/ y retorna {passed, failed, errors, total}."""
    summary = {"passed": 0, "failed": 0, "errors": 0, "total": 0}
    result_file = ORIGIN_ROOT / ".improvement_test_results.json"

    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "pytest",
                str(TESTS_DIR),
                "--tb=short",
                "--no-header",
                "-q",
                "--json-report",
                f"--json-report-file={result_file}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=ORIGIN_ROOT,
        )

        # Parsear JSON report si existe
        if result_file.exists():
            raw = result_file.read_text(encoding="utf-8")
            data = json.loads(raw)
            summary["passed"] = data.get("summary", {}).get("passed", 0)
            summary["failed"] = data.get("summary", {}).get("failed", 0)
            summary["errors"] = data.get("summary", {}).get("errors", 0)
            summary["total"] = data.get("summary", {}).get("total", 0)
            result_file.unlink(missing_ok=True)
        else:
            # Fallback: parsear stdout
            _parse_pytest_output(proc.stdout, summary)
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception) as exc:
        summary["errors"] = 1
        summary["error_detail"] = str(exc)

    summary["total"] = summary["passed"] + summary["failed"] + summary["errors"]
    return summary


def _parse_pytest_output(output: str, summary: dict) -> None:
    """Parseo de emergencia si no hay --json-report."""
    # Busca línea tipo: "= 1 passed, 2 failed in 3.42s ="
    m = re.search(
        r"=+\s*(?:(\d+)\s+passed)?.*?(?:(\d+)\s+failed)?.*?(?:(\d+)\s+errors)?",
        output,
    )
    if m:
        summary["passed"] = int(m.group(1)) if m.group(1) else 0
        summary["failed"] = int(m.group(2)) if m.group(2) else 0
        summary["errors"] = int(m.group(3)) if m.group(3) else 0


# ── 2. Escanear archivos .log ─────────────────────────────────────────────────
def scan_logs() -> list[dict]:
    """Busca tracebacks y errores en archivos .log dentro de ORIGIN_ROOT."""
    errors: list[dict] = []
    traceback_pattern = re.compile(
        r"(?:Traceback\s*\(most\s*recent\s*call\s*last\)|Error|Exception|ERROR|CRITICAL)",
        re.IGNORECASE,
    )

    for log_file in sorted(ORIGIN_ROOT.rglob("*.log")):
        try:
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        for i, line in enumerate(lines, start=1):
            if traceback_pattern.search(line):
                errors.append({
                    "file": str(log_file.relative_to(ORIGIN_ROOT)),
                    "line": i,
                    "error_text": line.strip()[:200],
                })
    return errors


# ── 3. Skills: registradas vs en disco ────────────────────────────────────────
def count_skills() -> dict:
    """Compara skills registradas (desde la DB o disco) con las que hay en disco."""
    on_disk: set[str] = set()
    if SKILLS_DIR.is_dir():
        for py_file in SKILLS_DIR.rglob("*.py"):
            # Solo archivos que parezcan skills (no __init__, no _base, etc.)
            if py_file.stem.startswith("skill_") or py_file.stem.startswith("skills_"):
                on_disk.add(py_file.stem.replace("skill_", "").replace("skills_", ""))

    # Leer skills registradas desde los nombres de directorios en skills/
    # También chequeamos el archivo skills.json si existe
    registered_names: set[str] = set()
    if SKILLS_DIR.is_dir():
        for d in SKILLS_DIR.iterdir():
            if d.is_dir():
                registered_names.add(d.name)

    # skills.json en la raíz
    skills_json = ORIGIN_ROOT / "skills.json"
    if skills_json.exists():
        try:
            data = json.loads(skills_json.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for s in data:
                    if isinstance(s, dict) and "name" in s:
                        registered_names.add(s["name"])
                    elif isinstance(s, str):
                        registered_names.add(s)
            elif isinstance(data, dict):
                for key in ("skills", "registered", "list", "names"):
                    val = data.get(key)
                    if isinstance(val, list):
                        registered_names.update(val)
        except Exception:
            pass

    unregistered = sorted(on_disk - registered_names)
    return {
        "registered_count": len(registered_names),
        "on_disk_count": len(on_disk),
        "unregistered": unregistered,
    }


# ── 4. ast.parse en skills/ y core/ ───────────────────────────────────────────
def check_syntax_errors() -> list[dict]:
    """Ejecuta ast.parse en todos los .py de skills/ y core/."""
    syntax_errors: list[dict] = []

    for folder in [SKILLS_DIR, CORE_DIR]:
        if not folder.is_dir():
            continue
        for py_file in sorted(folder.rglob("*.py")):
            try:
                source = py_file.read_text(encoding="utf-8", errors="replace")
                ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                syntax_errors.append({
                    "file": str(py_file.relative_to(ORIGIN_ROOT)),
                    "error": f"SyntaxError at line {exc.lineno}: {exc.msg}",
                })
            except Exception as exc:
                syntax_errors.append({
                    "file": str(py_file.relative_to(ORIGIN_ROOT)),
                    "error": f"Parse error: {exc}",
                })

    return syntax_errors


# ── 5. Llamar al endpoint de self-improvement ─────────────────────────────────
def call_self_improvement() -> dict:
    """Llama a /dashboard/improvement si el servidor está activo."""
    try:
        import urllib.request

        req = urllib.request.Request(IMPROVEMENT_URL, method="POST", data=b"{}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body)
            return {"status": data.get("status", "ok")}
    except Exception as exc:
        return {"status": f"unreachable: {exc}"}


# ── 6. overall_health ─────────────────────────────────────────────────────────
def compute_health(
    test_summary: dict,
    log_errors: list,
    skills: dict,
    syntax_errors: list,
    self_improvement: dict,
) -> str:
    """Determina salud general del sistema."""
    if (
        test_summary.get("failed", 0) > 0
        or test_summary.get("errors", 0) > 0
        or len(syntax_errors) > 0
    ):
        return "critical"

    if (
        len(log_errors) > 0
        or "unreachable" in self_improvement.get("status", "")
        or len(skills.get("unregistered", [])) > 0
    ):
        return "degraded"

    return "healthy"


# ── 7. Reporte principal ──────────────────────────────────────────────────────
def main():
    print(f"[improvement_cycle] Iniciando ciclo de mejora...", file=sys.stderr)

    test_summary = run_tests()
    print(f"[improvement_cycle] Tests: {test_summary}", file=sys.stderr)

    log_errors = scan_logs()
    print(f"[improvement_cycle] Errores en logs: {len(log_errors)}", file=sys.stderr)

    skills = count_skills()
    print(f"[improvement_cycle] Skills: registradas={skills['registered_count']}, "
          f"disco={skills['on_disk_count']}, "
          f"unregistradas={len(skills['unregistered'])}", file=sys.stderr)

    syntax_errors = check_syntax_errors()
    print(f"[improvement_cycle] Errores sintaxis: {len(syntax_errors)}", file=sys.stderr)

    self_improvement = call_self_improvement()
    print(f"[improvement_cycle] Self-improvement: {self_improvement['status']}",
          file=sys.stderr)

    overall_health = compute_health(
        test_summary, log_errors, skills, syntax_errors, self_improvement
    )
    print(f"[improvement_cycle] Salud: {overall_health}", file=sys.stderr)

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "test_summary": test_summary,
        "log_errors": log_errors,
        "skills": skills,
        "syntax_errors": syntax_errors,
        "self_improvement": self_improvement,
        "overall_health": overall_health,
    }

    # Imprimir JSON a stdout
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""
Code Doctor Skill para Origin.
Analisis estatico de proyectos React usando react-doctor.
Asigna health score (0-100) con diagnosticos accionables por categoria.
"""

import time
import asyncio
import json
import os
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import defaultdict

from .base_skill import BaseSkill

logger = logging.getLogger("origin.skills.code_doctor")

# ── Configuracion ──────────────────────────────────────────────────
DEFAULT_TIMEOUT = 120  # react-doctor puede tardar en proyectos grandes
ORIGIN_ROOT = Path(__file__).resolve().parent.parent
ORIGIN_FRONTEND = str(ORIGIN_ROOT / "frontend")

# ── Mapeo de categorias a iconos/prioridades ───────────────────────
_CATEGORY_META = {
    "Security": {"icon": "🔒", "priority": 1, "severity": "critical"},
    "Correctness": {"icon": "🐛", "priority": 2, "severity": "high"},
    "Performance": {"icon": "⚡", "priority": 3, "severity": "medium"},
    "Accessibility": {"icon": "♿", "priority": 4, "severity": "medium"},
    "Architecture": {"icon": "🏗️", "priority": 5, "severity": "low"},
    "Dead Code": {"icon": "💀", "priority": 6, "severity": "low"},
    "Bundle Size": {"icon": "📦", "priority": 7, "severity": "info"},
}


class CodeDoctorSkill(BaseSkill):
    """
    Skill: Diagnostico de salud de proyectos React via react-doctor.

    Acciones:
        scan        - Analisis completo: score, diagnosticos, resumen por categoria
        score       - Solo el score numerico (rapido)
        diagnostics - Diagnosticos detallados filtrados por categoria/severidad
        diff        - Analiza solo archivos cambiados vs rama base
        staged      - Analiza solo archivos staged (pre-commit)
        compare     - Compara scores entre dos scans (before/after)

    Requisitos:
        - Node.js >= 22
        - npx disponible en PATH
        - Proyecto React con package.json
    """

    VALID_ACTIONS = {"scan", "score", "diagnostics", "diff", "staged", "compare"}

    def __init__(self):
        super().__init__(
            name="code_doctor",
            description="Diagnostica la salud de proyectos React: score, errores, performance, seguridad, accesibilidad",  # noqa: E501
        )
        self._last_scan: Optional[Dict] = None  # Cache del ultimo scan para comparaciones

    # ── Validacion ─────────────────────────────────────────────────

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        action = inputs.get("action", "scan")
        if action not in self.VALID_ACTIONS:
            return False, f"Accion invalida '{action}'. Validas: {self.VALID_ACTIONS}"

        path = inputs.get("path", ORIGIN_FRONTEND)
        if not os.path.isdir(path):
            return False, f"Directorio no encontrado: {path}"

        pkg_json = os.path.join(path, "package.json")
        if not os.path.exists(pkg_json):
            return False, f"No se encontro package.json en {path}. No es un proyecto React valido."

        return True, ""

    # ── Ejecucion principal ────────────────────────────────────────

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            action = inputs.get("action", "scan")
            path = inputs.get("path", ORIGIN_FRONTEND)

            if action == "scan":
                result = await self._full_scan(path, inputs)
            elif action == "score":
                result = await self._quick_score(path)
            elif action == "diagnostics":
                result = await self._filtered_diagnostics(path, inputs)
            elif action == "diff":
                result = await self._diff_scan(path, inputs)
            elif action == "staged":
                result = await self._staged_scan(path)
            elif action == "compare":
                result = await self._compare(path, inputs)
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
            logger.error(f"Code doctor error: {e}")
            return {"success": False, "result": None, "error": str(e), "execution_time": elapsed}

    # ── Ejecutar react-doctor CLI ──────────────────────────────────

    async def _run_react_doctor(self, path: str, extra_flags: List[str] = None) -> Dict[str, Any]:
        """
        Ejecuta react-doctor via npx y retorna el JSON parseado.
        """
        cmd_parts = ["npx", "react-doctor@latest", path, "--json", "--yes"]
        if extra_flags:
            cmd_parts.extend(extra_flags)

        cmd = " ".join(cmd_parts)
        logger.info(f"Running: {cmd}")

        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=path,
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=DEFAULT_TIMEOUT)

            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()

            if not stdout:
                return {"error": f"react-doctor no produjo output. stderr: {stderr[:500]}"}

            # Parsear JSON (puede tener output extra antes del JSON)
            json_start = stdout.find("{")
            if json_start == -1:
                return {"error": f"No se encontro JSON en la salida: {stdout[:500]}"}

            report = json.loads(stdout[json_start:])

            # Verificar si hay error en el reporte
            if report.get("error"):
                return {"error": f"react-doctor error: {report['error']}"}

            return report

        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass
            return {"error": f"Timeout ({DEFAULT_TIMEOUT}s) ejecutando react-doctor"}
        except json.JSONDecodeError as e:
            return {"error": f"Error parseando JSON de react-doctor: {e}"}

    # ── scan: analisis completo ────────────────────────────────────

    async def _full_scan(self, path: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Analisis completo con score, diagnosticos y resumen por categoria."""
        report = await self._run_react_doctor(path)
        if "error" in report and report.get("error"):
            return report

        # Cache para comparaciones futuras
        self._last_scan = report

        summary = report.get("summary", {})
        diagnostics = report.get("diagnostics", [])
        projects = report.get("projects", [])

        # Agrupar diagnosticos por categoria
        by_category = defaultdict(list)
        for diag in diagnostics:
            cat = diag.get("category", "Other")
            by_category[cat].append(
                {
                    "file": diag.get("filePath", ""),
                    "line": diag.get("line", 0),
                    "rule": diag.get("rule", ""),
                    "severity": diag.get("severity", "warning"),
                    "message": diag.get("message", ""),
                    "help": diag.get("help", ""),
                }
            )

        # Ordenar categorias por prioridad
        categories_sorted = []
        for cat, issues in sorted(by_category.items(), key=lambda x: _CATEGORY_META.get(x[0], {}).get("priority", 99)):
            meta = _CATEGORY_META.get(cat, {"icon": "📋", "priority": 99, "severity": "info"})
            categories_sorted.append(
                {
                    "category": cat,
                    "icon": meta["icon"],
                    "severity": meta["severity"],
                    "count": len(issues),
                    "issues": issues[:10],  # Limitar issues por categoria
                }
            )

        # Info del proyecto
        project_info = {}
        if projects:
            proj = projects[0].get("project", {})
            project_info = {
                "name": proj.get("projectName", ""),
                "react_version": proj.get("reactVersion", ""),
                "framework": proj.get("framework", "unknown"),
                "has_typescript": proj.get("hasTypeScript", False),
                "source_files": proj.get("sourceFileCount", 0),
            }

        # Generar recomendaciones
        recommendations = self._generate_recommendations(diagnostics, summary)

        return {
            "score": summary.get("score", 0),
            "score_label": summary.get("scoreLabel", "Unknown"),
            "health_bar": self._render_health_bar(summary.get("score", 0)),
            "total_errors": summary.get("errorCount", 0),
            "total_warnings": summary.get("warningCount", 0),
            "affected_files": summary.get("affectedFileCount", 0),
            "total_diagnostics": summary.get("totalDiagnosticCount", 0),
            "categories": categories_sorted,
            "project": project_info,
            "recommendations": recommendations,
            "scan_time_ms": report.get("elapsedMilliseconds", 0),
            "react_doctor_version": report.get("version", ""),
        }

    # ── score: solo puntuacion ─────────────────────────────────────

    async def _quick_score(self, path: str) -> Dict[str, Any]:
        """Solo retorna el score numerico (mas rapido)."""
        report = await self._run_react_doctor(path)
        if "error" in report and report.get("error"):
            return report

        summary = report.get("summary", {})
        score = summary.get("score", 0)

        return {
            "score": score,
            "score_label": summary.get("scoreLabel", "Unknown"),
            "health_bar": self._render_health_bar(score),
            "errors": summary.get("errorCount", 0),
            "warnings": summary.get("warningCount", 0),
        }

    # ── diagnostics: filtrados ─────────────────────────────────────

    async def _filtered_diagnostics(self, path: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Diagnosticos filtrados por categoria y/o severidad."""
        report = await self._run_react_doctor(path)
        if "error" in report and report.get("error"):
            return report

        diagnostics = report.get("diagnostics", [])

        # Filtros
        category = inputs.get("category", "").lower()
        severity = inputs.get("severity", "").lower()
        file_filter = inputs.get("file", "")

        filtered = []
        for diag in diagnostics:
            if category and diag.get("category", "").lower() != category:
                continue
            if severity and diag.get("severity", "").lower() != severity:
                continue
            if file_filter and file_filter not in diag.get("filePath", ""):
                continue
            filtered.append(
                {
                    "file": diag.get("filePath", ""),
                    "line": diag.get("line", 0),
                    "rule": diag.get("rule", ""),
                    "severity": diag.get("severity", ""),
                    "category": diag.get("category", ""),
                    "message": diag.get("message", ""),
                    "help": diag.get("help", ""),
                }
            )

        return {
            "diagnostics": filtered[:50],
            "total_filtered": len(filtered),
            "total_unfiltered": len(diagnostics),
            "filters_applied": {
                "category": category or None,
                "severity": severity or None,
                "file": file_filter or None,
            },
        }

    # ── diff: solo cambios vs rama base ────────────────────────────

    async def _diff_scan(self, path: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Analiza solo archivos que cambiaron respecto a la rama base."""
        base = inputs.get("base", "main")
        report = await self._run_react_doctor(path, ["--diff", base])
        if "error" in report and report.get("error"):
            return report

        summary = report.get("summary", {})
        diagnostics = report.get("diagnostics", [])

        return {
            "mode": "diff",
            "base_branch": base,
            "score": summary.get("score", 0),
            "score_label": summary.get("scoreLabel", ""),
            "new_issues": len(diagnostics),
            "diagnostics": [
                {
                    "file": d.get("filePath", ""),
                    "line": d.get("line", 0),
                    "rule": d.get("rule", ""),
                    "severity": d.get("severity", ""),
                    "message": d.get("message", ""),
                }
                for d in diagnostics[:30]
            ],
        }

    # ── staged: pre-commit ─────────────────────────────────────────

    async def _staged_scan(self, path: str) -> Dict[str, Any]:
        """Analiza solo archivos staged para pre-commit."""
        report = await self._run_react_doctor(path, ["--staged"])
        if "error" in report and report.get("error"):
            return report

        summary = report.get("summary", {})
        diagnostics = report.get("diagnostics", [])

        return {
            "mode": "staged",
            "score": summary.get("score", 0),
            "issues_in_staged": len(diagnostics),
            "safe_to_commit": len(diagnostics) == 0,
            "diagnostics": [
                {
                    "file": d.get("filePath", ""),
                    "line": d.get("line", 0),
                    "severity": d.get("severity", ""),
                    "message": d.get("message", ""),
                }
                for d in diagnostics[:20]
            ],
        }

    # ── compare: comparar con scan anterior ────────────────────────

    async def _compare(self, path: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Compara el score actual con el ultimo scan cacheado."""
        # Scan actual
        report = await self._run_react_doctor(path)
        if "error" in report and report.get("error"):
            return report

        current_summary = report.get("summary", {})
        current_score = current_summary.get("score", 0)
        current_diags = len(report.get("diagnostics", []))

        if self._last_scan is None:
            # No hay scan previo — guardar este como referencia
            self._last_scan = report
            return {
                "current_score": current_score,
                "previous_score": None,
                "delta": None,
                "note": "Primer scan registrado. Ejecuta de nuevo tras hacer cambios para comparar.",
                "current_diagnostics": current_diags,
            }

        prev_summary = self._last_scan.get("summary", {})
        prev_score = prev_summary.get("score", 0)
        prev_diags = len(self._last_scan.get("diagnostics", []))

        delta = current_score - prev_score

        # Actualizar cache
        self._last_scan = report

        return {
            "current_score": current_score,
            "previous_score": prev_score,
            "delta": delta,
            "improved": delta > 0,
            "trend": "mejorando" if delta > 0 else "empeorando" if delta < 0 else "estable",
            "current_diagnostics": current_diags,
            "previous_diagnostics": prev_diags,
            "diagnostics_delta": current_diags - prev_diags,
        }

    # ── Generador de recomendaciones ───────────────────────────────

    def _generate_recommendations(self, diagnostics: List[Dict], summary: Dict) -> List[Dict[str, str]]:
        """Genera recomendaciones accionables basadas en los diagnosticos."""
        recs = []
        score = summary.get("score", 100)

        # Contar por categoria
        cat_counts = defaultdict(int)
        rule_counts = defaultdict(int)
        for d in diagnostics:
            cat_counts[d.get("category", "Other")] += 1
            rule_counts[d.get("rule", "")] += 1

        # Recomendaciones por prioridad
        if cat_counts.get("Security", 0) > 0:
            recs.append(
                {
                    "priority": "critical",
                    "action": f"Corregir {cat_counts['Security']} problemas de seguridad",
                    "detail": "Vulnerabilidades de seguridad deben resolverse inmediatamente.",
                }
            )

        if cat_counts.get("Correctness", 0) > 0:
            recs.append(
                {
                    "priority": "high",
                    "action": f"Resolver {cat_counts['Correctness']} errores de correctitud",
                    "detail": "Bugs potenciales en uso de hooks, estado o efectos.",
                }
            )

        if cat_counts.get("Performance", 0) > 0:
            recs.append(
                {
                    "priority": "medium",
                    "action": f"Optimizar {cat_counts['Performance']} issues de rendimiento",
                    "detail": "Renders innecesarios o patrones ineficientes detectados.",
                }
            )

        if cat_counts.get("Dead Code", 0) > 3:
            recs.append(
                {
                    "priority": "low",
                    "action": f"Limpiar {cat_counts['Dead Code']} elementos de dead code",
                    "detail": "Imports, tipos o archivos sin usar que ensucian el proyecto.",
                }
            )

        if score == 100:
            recs.append(
                {
                    "priority": "none",
                    "action": "Proyecto impecable",
                    "detail": "Score perfecto. No se encontraron problemas.",
                }
            )
        elif score >= 90:
            recs.append(
                {
                    "priority": "info",
                    "action": "Proyecto en excelente estado",
                    "detail": "Solo ajustes menores necesarios.",
                }
            )

        # Top rule mas repetida
        if rule_counts:
            top_rule = max(rule_counts, key=rule_counts.get)
            if rule_counts[top_rule] > 2:
                recs.append(
                    {
                        "priority": "info",
                        "action": f"Regla mas frecuente: '{top_rule}' ({rule_counts[top_rule]} veces)",
                        "detail": "Considera resolver este patron sistematicamente.",
                    }
                )

        return recs

    # ── Helpers visuales ───────────────────────────────────────────

    @staticmethod
    def _render_health_bar(score: int) -> str:
        """Genera barra visual de salud."""
        filled = round(score / 5)  # 20 bloques total
        empty = 20 - filled

        if score >= 90:
            face = "😀"
        elif score >= 70:
            face = "😐"
        elif score >= 50:
            face = "😟"
        else:
            face = "😱"

        bar = "█" * filled + "░" * empty
        return f"{face} [{bar}] {score}/100"

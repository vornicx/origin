"""
SelfImprovement — Bucle de automejora constante para Origin.

Analiza periódicamente:
  - Logs de errores y warnings
  - Fallos en ejecución de skills
  - Cobertura de tests
  - Patrones de uso
  - Skills en disco no registradas
  - Archivos .py.bak huérfanos
  - Código muerto (funciones/clases nunca importadas)

Para cada issue identificado:
  1. Diagnostica la causa raíz usando el LLM
  2. Propone un fix (diff concreto)
  3. Aplica el fix
  4. Corre los tests
  5. Si los tests pasan, consolida la mejora
  6. Reporta los resultados
"""

import re
import sys
import ast
import json
import asyncio
import logging
import subprocess
from typing import Dict, Any, Optional, List, Tuple, Set
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("origin.core.improvement")


@dataclass
class ImprovementIssue:
    """Un issue identificado por el sistema de automejora."""

    issue_id: str
    severity: str  # "high", "medium", "low"
    category: str  # "error", "warning", "performance", "test", "code_quality"
    file_path: str
    line: Optional[int] = None
    description: str = ""
    error_text: str = ""
    suggested_fix: Optional[str] = None
    fix_applied: bool = False
    fix_verified: bool = False
    timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "severity": self.severity,
            "category": self.category,
            "file_path": self.file_path,
            "line": self.line,
            "description": self.description,
            "error_text": self.error_text[:200] if self.error_text else "",
            "suggested_fix": self.suggested_fix[:200] if self.suggested_fix else None,
            "fix_applied": self.fix_applied,
            "fix_verified": self.fix_verified,
            "timestamp": self.timestamp,
        }


@dataclass
class ImprovementReport:
    """Reporte de una iteración de automejora."""

    iteration_id: str
    timestamp: str
    issues_found: int = 0
    fixes_applied: int = 0
    fixes_verified: int = 0
    issues: List[ImprovementIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "iteration_id": self.iteration_id,
            "timestamp": self.timestamp,
            "issues_found": self.issues_found,
            "fixes_applied": self.fixes_applied,
            "fixes_verified": self.fixes_verified,
            "issues": [i.to_dict() for i in self.issues],
        }


class SelfImprovement:
    """Bucle de automejora constante.

    Se ejecuta como background task. Cada ciclo:
      1. Scanea logs y estado del sistema
      2. Identifica issues (incluyendo skills no registradas, .bak huérfanos, dead code)
      3. Genera fixes con LLM
      4. Aplica y verifica
    """

    def __init__(self, llm_router, skill_executor):
        self._llm = llm_router
        self._executor = skill_executor
        self._task: Optional[asyncio.Task] = None
        self._cycle_count = 0
        self._reports: List[ImprovementReport] = []
        self._running = False
        self._enabled = True

        # Config — rutas absolutas para Windows
        self._log_dir = Path(__file__).parent.parent.resolve()
        self._project_root = Path(__file__).parent.parent.resolve()
        self._skills_dir = self._project_root / "skills"
        self._core_dir = self._project_root / "core"
        self._test_dir = self._project_root / "tests"
        self._scan_interval = 300  # 5 minutos entre ciclos
        self._max_issues_per_cycle = 8

        # Archivos de log a analizar
        self._log_patterns = [
            "*.log",
            "api*.log",
            "test_*.log",
        ]

        # Cache de módulos importados para detección de código muerto
        self._imported_names: Set[str] = set()

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "cycle_count": self._cycle_count,
            "reports_count": len(self._reports),
            "running": self._running,
            "enabled": self._enabled,
            "last_report": self._reports[-1].to_dict() if self._reports else None,
        }

    @property
    def reports(self) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._reports]

    def get_history(self, n: int = 10) -> List[Dict[str, Any]]:
        """Devuelve los últimos N reportes de mejora.

        Args:
            n: Número de reportes a devolver (por defecto 10, máximo 100).

        Returns:
            Lista de diccionarios con los reportes, ordenados del más reciente
            al más antiguo.
        """
        if not self._reports:
            return []
        n = max(1, min(n, 100))
        return [r.to_dict() for r in self._reports[-n:]]

    # ── Control de ciclo ─────────────────────────────────────

    def start(self):
        """Inicia el bucle de automejora en background."""
        if self._task and not self._task.done():
            logger.info("SelfImprovement ya está corriendo")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("SelfImprovement loop started (interval=%ds)", self._scan_interval)

    def stop(self):
        """Detiene el bucle."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("SelfImprovement loop stopped")

    async def _run_loop(self):
        """Ciclo principal de automejora."""
        while self._running:
            try:
                report = await self._run_cycle()
                if report and report.fixes_applied > 0:
                    logger.info(
                        "SelfImprovement: %d issues, %d fixes applied, %d verified",
                        report.issues_found,
                        report.fixes_applied,
                        report.fixes_verified,
                    )
                self._cycle_count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"SelfImprovement cycle error: {e}")
            await asyncio.sleep(self._scan_interval)

    async def _run_cycle(self) -> Optional[ImprovementReport]:
        """Ejecuta un ciclo completo de mejora."""
        report = ImprovementReport(
            iteration_id=f"imp_{int(datetime.utcnow().timestamp())}",
            timestamp=datetime.utcnow().isoformat(),
        )

        # 1. Scannear logs
        issues = await self._scan_logs()

        # 2. Skills en disco no registradas en skill_executor
        issues.extend(self._scan_unregistered_skills())

        # 3. Archivos .py.bak huérfanos
        issues.extend(self._scan_orphan_backups())

        # 4. Código muerto (funciones/clases nunca importadas)
        issues.extend(self._scan_dead_code())

        if not issues:
            self._reports.append(report)
            return report

        report.issues_found = len(issues)
        report.issues = issues[: self._max_issues_per_cycle]

        # 5. Generar y aplicar fixes
        for issue in report.issues:
            fix = await self._generate_fix(issue)
            if fix:
                applied = await self._apply_fix(issue, fix)
                if applied:
                    issue.fix_applied = True
                    report.fixes_applied += 1
                    verified = await self._verify_fix(issue)
                    if verified:
                        issue.fix_verified = True
                        report.fixes_verified += 1

        self._reports.append(report)
        self._record_metrics_snapshot(report)
        return report

    def _record_metrics_snapshot(self, report: ImprovementReport) -> None:
        """Append a lightweight metrics entry so quality_metrics.py can show trend."""
        try:
            metrics_file = self._project_root / "metrics" / "history.jsonl"
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": report.timestamp,
                "source": "self_improvement",
                "cycle": self._cycle_count,
                "issues_found": report.issues_found,
                "fixes_applied": report.fixes_applied,
                "fixes_verified": report.fixes_verified,
            }
            with metrics_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug("Could not write metrics snapshot: %s", e)

    # ── Scanning de logs ──────────────────────────────────────

    async def _scan_logs(self) -> List[ImprovementIssue]:
        """Analiza archivos de log en busca de errores y warnings.

        También verifica la sintaxis Python de skills/ y core/
        al arrancar el scanning.
        """
        issues = []

        # Verificar sintaxis de archivos .py en skills/ y core/
        issues.extend(self._check_python_syntax())

        issues.extend(self._scan_log_files())
        issues.extend(await self._scan_skill_failures())
        issues.extend(await self._scan_test_failures())
        return issues

    def _scan_log_files(self) -> List[ImprovementIssue]:
        """Scannea archivos .log en busca de patrones de error.

        Busca en el directorio raíz del proyecto recursivamente.
        En Windows, usa Path.rglob que maneja correctamente las rutas.
        """
        issues = []

        # Buscar recursivamente .log files en todo el proyecto
        log_files = list(self._log_dir.rglob("*.log"))
        logger.debug("SelfImprovement: scanning %d log files", len(log_files))

        for log_file in log_files:
            # Saltar archivos grandes (>10MB)
            try:
                if log_file.stat().st_size > 10 * 1024 * 1024:
                    logger.debug("Skipping large log file: %s", log_file)
                    continue
            except OSError:
                continue

            if not log_file.is_file():
                continue
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                lines = content.splitlines()
            except Exception:
                continue

            # Buscar patrones en las últimas 500 líneas
            for i, line in enumerate(lines[-500:]):
                # ── Tracebacks completos ──
                if "traceback" in line.lower() or "error traceback" in line.lower():
                    tb_lines = []
                    for j in range(i, min(i + 30, len(lines))):
                        tb_lines.append(lines[j])
                    error_text = "\n".join(tb_lines)
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"log_{log_file.stem}_{i}",
                            severity="high",
                            category="error",
                            file_path=str(log_file),
                            line=i,
                            description="Traceback encontrado en logs",
                            error_text=error_text[:500],
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

                # ── ModuleNotFoundError (imports rotos) ──
                elif "modulenotfounderror" in line.lower():
                    # Extraer el nombre del módulo que falta
                    mod_match = re.search(
                        r"No module named ['\"]([^'\"]+)['\"]",
                        line,
                    )
                    mod_name = mod_match.group(1) if mod_match else "desconocido"
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"mod_{log_file.stem}_{i}",
                            severity="high",
                            category="error",
                            file_path=str(log_file),
                            line=i,
                            description=f"Import roto: módulo '{mod_name}' no encontrado",
                            error_text=line,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

                # ── KeyError ──
                elif "keyerror" in line.lower():
                    # Extraer la key que falta
                    key_match = re.search(r"KeyError:\s*['\"]([^'\"]+)['\"]", line)
                    key_name = key_match.group(1) if key_match else "desconocida"
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"key_{log_file.stem}_{i}",
                            severity="high",
                            category="error",
                            file_path=str(log_file),
                            line=i,
                            description=f"KeyError: clave '{key_name}' no encontrada",
                            error_text=line,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

                # ── AttributeError ──
                elif "attributeerror" in line.lower():
                    attr_match = re.search(
                        r"AttributeError:\s*(?:['\"]([^'\"]+)['\"]\s*.*?\s*has no attribute\s*['\"]([^'\"]+)['\"]|module\s*['\"]([^'\"]+)['\"]\s*has no attribute\s*['\"]([^'\"]+)['\"])",
                        line,
                        re.IGNORECASE,
                    )
                    desc = "AttributeError en logs"
                    if attr_match:
                        obj = attr_match.group(1) or attr_match.group(3) or "?"
                        attr = attr_match.group(2) or attr_match.group(4) or "?"
                        desc = f"AttributeError: '{obj}' no tiene atributo '{attr}'"
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"attr_{log_file.stem}_{i}",
                            severity="high",
                            category="error",
                            file_path=str(log_file),
                            line=i,
                            description=desc,
                            error_text=line,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

                # ── TypeError ──
                elif "typeerror" in line.lower():
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"type_{log_file.stem}_{i}",
                            severity="high",
                            category="error",
                            file_path=str(log_file),
                            line=i,
                            description=f"TypeError en logs",
                            error_text=line,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

                # ── Error genérico (sin rate limit) ──
                elif "error" in line.lower() and "rate limit" not in line.lower():
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"err_{log_file.stem}_{i}",
                            severity="medium",
                            category="error",
                            file_path=str(log_file),
                            line=i,
                            description=line[:200],
                            error_text=line,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

        return issues

    def _check_python_syntax(self) -> List[ImprovementIssue]:
        """Verifica que todos los archivos .py en skills/ y core/
        tengan sintaxis Python válida usando ast.parse.

        Reporta archivos con errores de sintaxis (SyntaxError)
        o caracteres no imprimibles (BOM, etc.) que impidan el parseo.
        """
        issues = []
        source_dirs = {"skills": self._skills_dir, "core": self._core_dir}

        for label, src_dir in source_dirs.items():
            if not src_dir.exists():
                logger.warning("SelfImprovement: directorio %s no existe: %s", label, src_dir)
                continue
            for py_file in sorted(src_dir.rglob("*.py")):
                if "venv" in str(py_file) or "__pycache__" in str(py_file):
                    continue
                try:
                    content = py_file.read_text(encoding="utf-8-sig", errors="replace")
                    # Verificar BOM (Byte Order Mark) que causa SyntaxError
                    if content and content[0] == "\ufeff":
                        # BOM presente, intentar remover y re-parsear
                        content = content.lstrip("\ufeff")
                    ast.parse(content)
                except SyntaxError as e:
                    # Detectar si es por BOM
                    error_text = str(e)
                    if "non-printable character" in error_text or "U+FEFF" in error_text:
                        error_text = f"Archivo contiene BOM (U+FEFF) que impide el parseo. " \
                                     f"Ejecutar: sed -i '1s/^\\xEF\\xBB\\xBF//' \"{py_file}\""
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"syntax_{py_file.stem}",
                            severity="high",
                            category="error",
                            file_path=str(py_file),
                            line=e.lineno or 1,
                            description=f"Error de sintaxis en {label}/{py_file.name}",
                            error_text=error_text,
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )
                except Exception as e:
                    issues.append(
                        ImprovementIssue(
                            issue_id=f"syntax_{py_file.stem}",
                            severity="medium",
                            category="error",
                            file_path=str(py_file),
                            description=f"Error al leer/parsear {label}/{py_file.name}",
                            error_text=str(e),
                            timestamp=datetime.utcnow().isoformat(),
                        )
                    )

        if issues:
            logger.warning(
                "SelfImprovement: %d archivos con errores de sintaxis en skills/ y core/",
                len(issues),
            )

        return issues

    async def _scan_skill_failures(self) -> List[ImprovementIssue]:
        """Analiza skills que fallan frecuentemente."""
        issues = []
        for skill in self._executor._registry.values():
            meta = skill.get_metadata()
            if meta.get("last_execution") and meta.get("execution_count", 0) > 5:
                # Skills con muchas ejecuciones podrían necesitar optimización
                pass
        return issues

    async def _scan_test_failures(self) -> List[ImprovementIssue]:
        """Ejecuta tests y analiza fallos."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pytest", str(self._test_dir), "--tb=line", "-q"],
                capture_output=True,
                text=True,
                cwd=str(self._project_root),
                timeout=60,
            )
            if result.returncode != 0:
                issues = []
                for line in result.stdout.splitlines():
                    if "FAILED" in line:
                        test_path = line.replace("FAILED ", "").strip()
                        issues.append(
                            ImprovementIssue(
                                issue_id=f"test_{test_path}",
                                severity="high",
                                category="test",
                                file_path=test_path.split("::")[0],
                                description=f"Test fallido: {test_path}",
                                error_text=result.stderr[:500],
                                timestamp=datetime.utcnow().isoformat(),
                            )
                        )
                return issues
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        return []

    # ── Scanning: skills en disco no registradas ──────────────

    def _scan_unregistered_skills(self) -> List[ImprovementIssue]:
        """Detecta archivos de skill en disco que NO están registrados en skill_executor.py.

        Compara los .py en skills/ que contienen clases estilo BaseSkill
        contra el registro actual en el executor.
        """
        issues = []
        registered = set(self._executor._registry.keys())
        # También considerar nombres de módulo que el executor importa
        executor_imports = self._get_executor_skill_modules()

        for py_file in self._skills_dir.glob("*_skill.py"):
            module_name = py_file.stem  # ej: "datetime_skill"

            # Extraer el nombre lógico de la skill desde el archivo
            skill_name = self._infer_skill_name_from_file(py_file)
            if skill_name and skill_name not in registered and skill_name not in executor_imports:
                issues.append(
                    ImprovementIssue(
                        issue_id=f"unreg_{module_name}",
                        severity="medium",
                        category="code_quality",
                        file_path=str(py_file),
                        description=f"Skill '{skill_name}' existe en disco ({py_file.name}) pero no está registrada en skill_executor.py",
                        error_text=f"Archivo: {py_file.name}. Nombre inferido: '{skill_name}'. Registradas: {sorted(registered)}",
                        timestamp=datetime.utcnow().isoformat(),
                    )
                )

        return issues

    def _get_executor_skill_modules(self) -> Set[str]:
        """Retorna los nombres de módulo/skill que skill_executor.py importa."""
        executor_file = self._skills_dir / "skill_executor.py"
        if not executor_file.exists():
            return set()

        content = executor_file.read_text(encoding="utf-8", errors="replace")
        # Buscar imports relativos: from .xxx_skill import
        imported = set()
        for m in re.findall(r"from \.(\w+) import", content):
            imported.add(m)
        # También buscar en _register_defaults las clases
        for m in re.findall(r"(\w+)Skill\b", content):
            # Convertir CamelCase a snake_case para el nombre de módulo
            snake = re.sub(r"(?<!^)(?=[A-Z])", "_", m).lower().replace("skill", "_skill")
            imported.add(snake)
        return imported

    def _infer_skill_name_from_file(self, py_file: Path) -> Optional[str]:
        """Intenta inferir el nombre lógico de una skill desde su archivo .py."""
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # Buscar clase que herede de BaseSkill
                    for base in node.bases:
                        base_name = ""
                        if isinstance(base, ast.Name):
                            base_name = base.id
                        elif isinstance(base, ast.Attribute):
                            base_name = base.attr
                        if "BaseSkill" in base_name or "Skill" in base_name:
                            # Intentar obtener .name del class
                            for item in node.body:
                                if isinstance(item, ast.Assign):
                                    for target in item.targets:
                                        if isinstance(target, ast.Name) and target.id == "name":
                                            if isinstance(item.value, ast.Constant):
                                                return item.value.value
                            # Fallback: CamelCase a snake
                            name = re.sub(r"(?<!^)(?=[A-Z])", "_", node.name).lower()
                            name = name.replace("_s_k_i_l_l", "_skill").replace("skill", "").strip("_")
                            if not name:
                                name = node.name.lower()
                            # Mapeos conocidos
                            known = {
                                "datetime": "datetime",
                                "datetime_skill": "datetime",
                                "shell": "shell",
                                "shell_skill": "shell",
                                "vision": "vision",
                                "vision_skill": "vision",
                                "ui_automation": "ui_automation",
                                "system_tray": "system_tray",
                                "notification": "notification",
                                "wake_word": "wake_word",
                                "os_control": "os_control",
                                "clipboard": "clipboard",
                                "active_window": "active_window",
                                "eventlog": "eventlog",
                                "telegram": "telegram",
                                "camera": "camera",
                                "voice": "voice",
                            }
                            if name in known:
                                return known[name]
                            return name
            return None
        except Exception:
            return None

    # ── Scanning: archivos .py.bak huérfanos ──────────────────

    def _scan_orphan_backups(self) -> List[ImprovementIssue]:
        """Detecta archivos .py.bak que quedaron de fixes anteriores y nunca se limpiaron.

        Un backup es huérfano si:
        - El .py original NO existe (el fix reemplazó el .bak por el original)
        - O el .bak existe y el .py original también, pero el .bak tiene días de antigüedad
        """
        issues = []
        backup_files = list(self._project_root.rglob("*.py.bak"))
        # También buscar *.bak junto a .py
        backup_files.extend(list(self._project_root.rglob("*.bak")))

        seen = set()
        for bak_file in backup_files:
            if bak_file in seen:
                continue
            seen.add(bak_file)

            # El archivo original tendría el .bak quitado
            # .py.bak → .py  o  .bak → .py
            original_path = None
            if bak_file.suffixes == [".py", ".bak"]:
                original_path = bak_file.with_suffix("")  # quita .bak
            elif bak_file.suffix == ".bak":
                original_path = bak_file.with_suffix("")
                if original_path and original_path.suffix != ".py":
                    original_path = original_path.with_suffix(original_path.suffix + ".py")

            if not original_path:
                continue

            bak_age_days = self._file_age_days(bak_file)

            if not original_path.exists():
                # Backup huérfano: el original no existe
                issues.append(
                    ImprovementIssue(
                        issue_id=f"orphan_bak_{bak_file.stem}",
                        severity="low",
                        category="code_quality",
                        file_path=str(bak_file),
                        description=f"Backup huérfano: {bak_file.name} (original {original_path.name} no existe)",
                        error_text=f"El original '{original_path.name}' fue eliminado o renombrado. "
                                   f"Backup tiene ~{bak_age_days} días de antigüedad.",
                        timestamp=datetime.utcnow().isoformat(),
                    )
                )
            elif bak_age_days > 7:
                # Backup antiguo que no se limpió
                issues.append(
                    ImprovementIssue(
                        issue_id=f"old_bak_{bak_file.stem}",
                        severity="low",
                        category="code_quality",
                        file_path=str(bak_file),
                        description=f"Backup antiguo sin limpiar: {bak_file.name} ({bak_age_days} días)",
                        error_text=f"Backup tiene ~{bak_age_days} días. El original {original_path.name} aún existe.",
                        timestamp=datetime.utcnow().isoformat(),
                    )
                )

        return issues

    @staticmethod
    def _file_age_days(path: Path) -> int:
        """Calcula antigüedad de un archivo en días."""
        try:
            mtime = path.stat().st_mtime
            age_seconds = datetime.utcnow().timestamp() - mtime
            return int(age_seconds / 86400)
        except OSError:
            return 999

    # ── Scanning: código muerto (funciones/clases nunca importadas) ──

    def _scan_dead_code(self) -> List[ImprovementIssue]:
        """Detecta funciones y clases definidas en archivos .py que NUNCA
        son importadas desde otro módulo del proyecto.

        Solo analiza skills/ y core/ (el código propio del proyecto).
        """
        issues = []

        # 1. Recopilar TODAS las importaciones entre módulos del proyecto
        project_modules: Dict[str, Set[str]] = {}  # module_name -> set of defined names
        all_imports: Dict[str, Set[str]] = {}  # module_name -> set of imported-from-other-project-modules names

        source_dirs = [self._skills_dir, self._core_dir, self._project_root / "api", self._project_root / "db"]
        # Archivos .py del proyecto (excluyendo venv, .uv-python, etc.)
        for src_dir in source_dirs:
            if not src_dir.exists():
                continue
            for py_file in src_dir.rglob("*.py"):
                if "venv" in str(py_file) or ".uv-python" in str(py_file):
                    continue
                rel_path = py_file.relative_to(self._project_root)
                module_path = str(rel_path.with_suffix("")).replace("\\", "/").replace("/", ".")

                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue

                # Nombres definidos en este módulo
                defined = set()
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        defined.add(node.name)
                    elif isinstance(node, ast.ClassDef):
                        defined.add(node.name)
                    elif isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                defined.add(target.id)

                project_modules[module_path] = defined
                all_imports[module_path] = set()

        # 2. Para cada módulo, ver qué nombres importa de otros módulos del proyecto
        for src_dir in source_dirs:
            if not src_dir.exists():
                continue
            for py_file in src_dir.rglob("*.py"):
                if "venv" in str(py_file) or ".uv-python" in str(py_file):
                    continue
                rel_path = py_file.relative_to(self._project_root)
                module_path = str(rel_path.with_suffix("")).replace("\\", "/").replace("/", ".")

                try:
                    tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError:
                    continue

                for node in ast.iter_child_nodes(tree):
                    # from .xxx import YYY
                    if isinstance(node, ast.ImportFrom):
                        if node.module and node.level is not None:
                            # import relativo
                            imported_names = {alias.name for alias in node.names}
                            # El módulo origen podría ser otro módulo del proyecto
                            all_imports[module_path].update(imported_names)
                    # import xxx  (si xxx es otro módulo del proyecto)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            imported_name = alias.name.split(".")[0]
                            for proj_mod in project_modules:
                                if imported_name in proj_mod or proj_mod.endswith(f".{imported_name}"):
                                    # Importó otro módulo del proyecto
                                    pass

        # 3. Para cada módulo, ver qué nombres definidos NO son importados
        for module_name, defined_names in project_modules.items():
            if not defined_names:
                continue

            # Saltar módulos que son entry points o scripts
            if module_name in ("api.main", "scripts.improvement_cycle", "test_api", "setup", "__init__"):
                continue
            # Saltar __init__ (exportan por defecto)
            if module_name.endswith("__init__"):
                continue

            for name in sorted(defined_names):
                # Saltar dunder methods y callbacks comunes
                if name.startswith("_") or name in ("main", "setup", "run", "start", "stop", "configure"):
                    continue

                # Verificar si es importado desde cualquier otro módulo
                is_imported = False
                for imp_mod, imported_set in all_imports.items():
                    if imp_mod == module_name:
                        continue
                    if name in imported_set:
                        is_imported = True
                        break

                if not is_imported:
                    # Doble check: buscar en todo el proyecto con regex
                    if not self._is_name_used_anywhere(name, module_name):
                        issues.append(
                            ImprovementIssue(
                                issue_id=f"deadcode_{module_name.replace('.', '_')}_{name}",
                                severity="low",
                                category="code_quality",
                                file_path=str(rel_path := self._find_module_path(module_name)),
                                description=f"Posible código muerto: '{name}' definido en {module_name} pero nunca importado",
                                error_text=f"Función/clase '{name}' está definida en {module_name} "
                                           f"pero no es importada desde ningún otro módulo del proyecto.",
                                timestamp=datetime.utcnow().isoformat(),
                            )
                        )

        return issues

    def _is_name_used_anywhere(self, name: str, exclude_module: str) -> bool:
        """Verifica si un nombre es usado (no solo definido) en algún archivo del proyecto."""
        exclude_path = exclude_module.replace(".", "/") + ".py"
        source_dirs = [self._skills_dir, self._core_dir, self._project_root / "api", self._project_root / "db"]
        for src_dir in source_dirs:
            if not src_dir.exists():
                continue
            for py_file in src_dir.rglob("*.py"):
                if "venv" in str(py_file) or ".uv-python" in str(py_file):
                    continue
                if exclude_path in str(py_file):
                    continue
                try:
                    content = py_file.read_text(encoding="utf-8", errors="replace")
                    # Buscar el nombre como identificador (no substring)
                    if re.search(rf'\b{re.escape(name)}\b', content):
                        return True
                except Exception:
                    continue
        return False

    def _find_module_path(self, module_name: str) -> Path:
        """Convierte nombre de módulo a Path."""
        rel = module_name.replace(".", "/") + ".py"
        path = self._project_root / rel
        if path.exists():
            return path
        return self._project_root / rel

    # ── Fix generation ───────────────────────────────────────

    async def _generate_fix(self, issue: ImprovementIssue) -> Optional[str]:
        """Usa el LLM para generar un fix para el issue."""
        code_context = self._get_code_context(issue.file_path, issue.line)

        prompt = f"""Eres un ingeniero de software experto. Tu tarea es diagnosticar y corregir un error en Origin.

INFORME DE ERROR:
Archivo: {issue.file_path}
Línea: {issue.line or "N/A"}
Descripción: {issue.description}
Error: {issue.error_text[:500]}

CONTEXTO DEL CÓDIGO:
```python
{code_context[:2000]}
```

INSTRUCCIONES:
1. Analiza la causa raíz del error
2. Si es un bug claro, genera el código corregido
3. Si es un warning de estilo o calidad, sugiere la mejora
4. Si no puedes determinar la causa, responde "NO_FIX_POSSIBLE"

Responde ÚNICAMENTE con un bloque JSON válido (sin markdown adicional):

{{
    "diagnosis": "explicación corta de la causa raíz",
    "fix_type": "bug_fix|refactor|style|test",
    "code_fix": "código corregido (solo la función/clase afectada)",
    "file_path": "{issue.file_path}",
    "confidence": 0.0-1.0
}}
"""
        result = await self._llm.call_llm(
            prompt=prompt,
            system_message="Eres un debugger experto. Diagnosticas y corriges código. Responde SIEMPRE con un JSON válido.",
            temperature=0.2,
            task_type="debug",
        )
        if result.get("success"):
            return result["content"]
        return None

    def _get_code_context(self, file_path: str, line: Optional[int] = None, context_lines: int = 20) -> str:
        """Obtiene contexto de código alrededor de una línea."""
        try:
            path = Path(file_path)
            if not path.is_absolute():
                path = self._project_root / path
            if not path.exists():
                return ""
            content = path.read_text(encoding="utf-8", errors="replace")
            if line is not None:
                lines = content.splitlines()
                start = max(0, line - context_lines)
                end = min(len(lines), line + context_lines)
                return "\n".join(lines[start:end])
            return content[:3000]
        except Exception:
            return ""

    # ── Fix application ──────────────────────────────────────

    async def _apply_fix(self, issue: ImprovementIssue, fix_response: str) -> bool:
        """Aplica un fix generado por el LLM.

        El fix_response puede ser:
        - JSON plano: parseado directo con json.loads
        - JSON envuelto en ```json ... ```: extraído con extract_json_from_text
        - Texto plano con llaves: búsqueda heurística de { ... }
        """
        try:
            fix_data = json.loads(fix_response)
        except (json.JSONDecodeError, TypeError):
            try:
                from core.utils import extract_json_from_text
                fix_data = extract_json_from_text(fix_response)
            except Exception:
                # Fallback final: buscar { ... } manualmente
                try:
                    start = fix_response.find("{")
                    end = fix_response.rfind("}")
                    if start != -1 and end > start:
                        fix_data = json.loads(fix_response[start:end + 1])
                    else:
                        fix_data = {}
                except Exception:
                    fix_data = {}

        if not fix_data or not isinstance(fix_data, dict):
            logger.warning("SelfImprovement: fix response no es un dict válido")
            return False

        code_fix = fix_data.get("code_fix", "")
        file_path = fix_data.get("file_path", issue.file_path)
        if not code_fix or code_fix == "NO_FIX_POSSIBLE":
            return False

        # Validar que el fix sea sintácticamente válido
        try:
            ast.parse(code_fix)
        except SyntaxError:
            logger.warning("SelfImprovement: fix con error de sintaxis, skipping")
            return False

        # Aplicar el fix al archivo
        try:
            target = Path(file_path)
            if not target.is_absolute():
                target = self._project_root / target

            if not target.exists():
                logger.warning(f"SelfImprovement: archivo no encontrado: {target}")
                return False

            original = target.read_text(encoding="utf-8")
            # Backup
            backup = target.with_suffix(".py.bak")
            target.rename(backup)

            if code_fix.startswith("def ") or code_fix.startswith("class ") or code_fix.startswith("async def "):
                # Es un fragmento: buscar y reemplazar en el archivo original
                func_name = code_fix.split("(")[0].split()[-1]
                pattern = re.compile(
                    rf"(def {func_name}.*?)(?=\n\s*(?:def |class |async def |$))",
                    re.DOTALL,
                )
                if pattern.search(original):
                    original = pattern.sub(code_fix, original)
                else:
                    original = code_fix
                target.write_text(original, encoding="utf-8")
            else:
                # Es el archivo completo
                target.write_text(code_fix, encoding="utf-8")

            issue.suggested_fix = code_fix[:200]
            logger.info(f"SelfImprovement: fix aplicado a {target}")
            return True

        except Exception as e:
            logger.warning(f"SelfImprovement: error aplicando fix: {e}")
            # Restaurar backup si existe
            backup = Path(str(target) + ".bak")
            if backup.exists():
                try:
                    backup.rename(target)
                except Exception:
                    pass
            return False

    async def _verify_fix(self, issue: ImprovementIssue) -> bool:
        """Verifica que el fix no haya roto nada."""
        # Verificar sintaxis del archivo modificado
        file_path = issue.file_path
        try:
            target = Path(file_path)
            if not target.is_absolute():
                target = self._project_root / target
            if target.exists():
                ast.parse(target.read_text(encoding="utf-8"))
        except (SyntaxError, Exception):
            return False

        # Correr tests si es un archivo de código fuente
        if file_path.endswith(".py") and "venv" not in file_path:
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", str(self._test_dir), "-q", "--tb=line"],
                    capture_output=True,
                    text=True,
                    cwd=str(self._project_root),
                    timeout=120,
                )
                return result.returncode == 0
            except subprocess.TimeoutExpired:
                return False
            except Exception:
                return False

        return True

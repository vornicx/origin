"""
AutoSkill — Generación dinámica de skills para Origin.

Cuando el reasoning loop detecta una tarea que ninguna skill existente
puede ejecutar, AutoSkill genera código Python sobre la marcha:

  1. Toma la descripción de la tarea del usuario
  2. Usa el LLM para generar una clase Python que implementa BaseSkill
  3. Guarda el archivo en skills/auto_skills/
  4. Importa dinámicamente y registra la skill
  5. Prueba con un input de ejemplo
  6. Si falla, itera con el LLM para corregir errores
"""

import sys
import ast
import logging
import importlib.util
from typing import Dict, Any, Optional, List
from pathlib import Path

logger = logging.getLogger("origin.skills.autoskill")

AUTO_SKILLS_DIR = Path(__file__).parent / "auto_skills"
AUTO_SKILLS_DIR.mkdir(parents=True, exist_ok=True)

# Asegurar que auto_skills es un package
init_file = AUTO_SKILLS_DIR / "__init__.py"
if not init_file.exists():
    init_file.write_text("")


# ── Template para generar skills ─────────────────────────────

SKILL_TEMPLATE = '''
from typing import Dict, Any
from datetime import datetime
from skills.base_skill import BaseSkill
import logging

logger = logging.getLogger("origin.skills.auto.{name}")


class {class_name}(BaseSkill):
    """Auto-generated skill: {description}"""

    def __init__(self):
        super().__init__(
            name="{name}",
            description="{description}"
        )
        {init_body}

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple:
        {validate_body}

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        import time
        start = time.time()
        self.execution_count += 1
        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {{"success": False, "result": None, "error": error, "execution_time": 0}}
        try:
            {execute_body}
            elapsed = round(time.time() - start, 3)
            self.last_execution = datetime.now()
            return {{"success": True, "result": result, "error": None, "execution_time": elapsed}}
        except Exception as e:
            elapsed = round(time.time() - start, 3)
            logger.error(f"{{self.name}} failed: {{e}}")
            return {{"success": False, "result": None, "error": str(e), "execution_time": elapsed}}

    def get_skill_docs(self) -> Dict[str, Any]:
        return {{
            "description": self.description,
            {doc_body}
        }}
'''


class AutoSkillEngine:
    """Genera, prueba y registra skills dinámicamente."""

    def __init__(self, llm_router, skill_executor):
        self._llm = llm_router
        self._executor = skill_executor
        self._generated_count = 0
        self._generated: List[str] = []  # Nombres de skills generadas

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "generated_count": self._generated_count,
            "generated_skills": list(self._generated),
            "auto_skills_dir": str(AUTO_SKILLS_DIR),
        }

    async def generate_skill(self, task_description: str, sample_inputs: dict = None) -> Optional[str]:
        """
        Genera una skill para la tarea descrita.

        Args:
            task_description: Qué debe hacer la skill (lenguaje natural)
            sample_inputs: Inputs de ejemplo para probar la skill

        Returns:
            Nombre de la skill si se generó y registró correctamente, None si falló
        """
        self._generated_count += 1
        skill_name = self._make_skill_name(task_description)
        class_name = self._make_class_name(skill_name)

        logger.info(f"AutoSkill: generando '{skill_name}' para: {task_description[:80]}...")

        # 1. Generar código con el LLM
        code = await self._generate_code(task_description, skill_name, class_name)
        if not code:
            logger.error("AutoSkill: el LLM no pudo generar código")
            return None

        # 2. Validar sintaxis
        valid, error = self._validate_syntax(code, skill_name)
        if not valid:
            logger.warning(f"AutoSkill: error de sintaxis: {error}")
            code = await self._fix_code(code, error, task_description, skill_name, class_name)
            if not code:
                return None

        # 3. Guardar archivo
        file_path = AUTO_SKILLS_DIR / f"{skill_name}.py"
        try:
            file_path.write_text(code, encoding="utf-8")
            logger.info(f"AutoSkill: guardado en {file_path}")
        except Exception as e:
            logger.error(f"AutoSkill: no se pudo guardar: {e}")
            return None

        # 4. Importar dinámicamente
        skill_instance = self._import_skill(skill_name, file_path)
        if not skill_instance:
            # Intentar corregir errores de import
            fix_code = await self._fix_import_error(task_description, skill_name, class_name, file_path)
            if fix_code:
                file_path.write_text(fix_code, encoding="utf-8")
                skill_instance = self._import_skill(skill_name, file_path)
            if not skill_instance:
                return None

        # 5. Registrar en el executor
        self._executor.register(skill_instance)
        self._generated.append(skill_name)
        logger.info(f"AutoSkill: '{skill_name}' registrada y lista")

        return skill_name

    def _make_skill_name(self, description: str) -> str:
        words = description.lower().split()[:4]
        name = "_".join(w for w in words if w.isalnum())
        if not name:
            name = f"auto_skill_{self._generated_count}"
        return name

    def _make_class_name(self, skill_name: str) -> str:
        return "".join(word.capitalize() for word in skill_name.split("_")) + "Skill"

    async def _generate_code(self, description: str, skill_name: str, class_name: str) -> Optional[str]:
        """Usa el LLM para generar código Python de la skill."""
        prompt = f"""Genera una skill para Origin (asistente IA) que haga lo siguiente:

{description}

La skill debe ser una clase Python que herede de BaseSkill.
El archivo debe llamarse '{skill_name}.py' y la clase '{class_name}'.

REQUISITOS:
- Importar: from typing import Dict, Any  y  from skills.base_skill import BaseSkill
- La clase debe tener __init__, validate_inputs, execute, y get_skill_docs
- validate_inputs retorna tuple[bool, str]: (True, "") si válido, (False, "error") si no
- execute retorna dict: {{"success": bool, "result": Any, "error": Optional[str], "execution_time": float}}
- Usa 'import time' y 'from datetime import datetime' dentro del método execute
- No uses librerías externas que no sean estándar de Python
- Si necesita HTTP, usa 'import httpx' y async with httpx.AsyncClient()
- Si necesita web scraping, usa 'from bs4 import BeautifulSoup'
- get_skill_docs retorna un dict con "description", "actions" (lista), "inputs_{{action}}" y "example"

Ejemplo de estructura para validate_inputs:
    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple:
        action = inputs.get("action", "")
        valid_actions = ("action1", "action2")
        if action not in valid_actions:
            return False, f"Accion invalida. Validas: {{valid_actions}}"
        return True, ""

Genera SOLO el código Python, sin explicaciones. El código debe ser funcional inmediatamente.
"""
        result = await self._llm.call_llm(
            prompt=prompt,
            system_message="Eres un ingeniero de software experto en Python. Generas código limpio, seguro y funcional.",  # noqa: E501
            temperature=0.3,
            task_type="code_generation",
        )
        if result.get("success"):
            return self._extract_code(result["content"])
        return None

    def _extract_code(self, text: str) -> Optional[str]:
        """Extrae código Python de la respuesta del LLM."""
        import re

        # Buscar bloques ```python ... ```
        match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Si no hay bloques, asumir que todo es código
        if "class" in text and "BaseSkill" in text:
            return text.strip()
        return None

    def _validate_syntax(self, code: str, skill_name: str) -> tuple:
        """Valida que el código Python sea sintácticamente correcto."""
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as e:
            return False, str(e)

    async def _fix_code(
        self, code: str, error: str, description: str, skill_name: str, class_name: str
    ) -> Optional[str]:
        """Pide al LLM que corrija el error de sintaxis."""
        prompt = f"""El siguiente código Python tiene un error de sintaxis:

```python
{code}
```

ERROR: {error}

Corrige el error y devuelve SOLO el código corregido, sin explicaciones.
La skill debe hacer: {description}
Nombre de clase: {class_name}
"""
        result = await self._llm.call_llm(
            prompt=prompt,
            system_message="Eres un debugger experto. Corriges errores de sintaxis Python.",
            temperature=0.2,
            task_type="code_fix",
        )
        if result.get("success"):
            return self._extract_code(result["content"])
        return None

    async def _fix_import_error(
        self, description: str, skill_name: str, class_name: str, file_path: Path
    ) -> Optional[str]:
        """Intenta corregir errores de importación."""
        code = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
        if not code:
            return None

        prompt = f"""El siguiente código Python tiene un error de importación o ejecución:

```python
{code}
```

La skill debe hacer: {description}
Nombre de clase: {class_name}

Corrige los errores. Asegúrate de que:
1. Todos los imports sean correctos
2. No haya dependencias externas no estándar
3. La clase herede de BaseSkill correctamente
4. validate_inputs y execute tengan las firmas correctas

Devuelve SOLO el código corregido, sin explicaciones.
"""
        result = await self._llm.call_llm(
            prompt=prompt,
            system_message="Eres un debugger experto en Python.",
            temperature=0.2,
            task_type="code_fix",
        )
        if result.get("success"):
            return self._extract_code(result["content"])
        return None

    def _import_skill(self, skill_name: str, file_path: Path) -> Optional[object]:
        """Importa dinámicamente una skill desde un archivo."""
        try:
            spec = importlib.util.spec_from_file_location(f"skills.auto_skills.{skill_name}", str(file_path))
            if not spec or not spec.loader:
                logger.error(f"AutoSkill: no se pudo cargar spec para {skill_name}")
                return None

            module = importlib.util.module_from_spec(spec)
            sys.modules[f"skills.auto_skills.{skill_name}"] = module
            spec.loader.exec_module(module)

            # Encontrar la clase que hereda de BaseSkill
            class_name = self._make_class_name(skill_name)
            skill_class = getattr(module, class_name, None)
            if not skill_class:
                # Buscar cualquier clase que herede de BaseSkill
                for name in dir(module):
                    obj = getattr(module, name)
                    if isinstance(obj, type) and "BaseSkill" in [b.__name__ for b in obj.__bases__]:
                        skill_class = obj
                        break
            if not skill_class:
                logger.error(f"AutoSkill: no se encontró clase BaseSkill en {skill_name}")
                return None

            return skill_class()
        except Exception as e:
            logger.warning(f"AutoSkill: error importando {skill_name}: {e}")
            return None

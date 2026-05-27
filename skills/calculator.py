import time
import math
import ast
import operator
from datetime import datetime
from typing import Dict, Any

from .base_skill import BaseSkill


# Operadores seguros permitidos
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Funciones matemáticas permitidas
_SAFE_FUNCTIONS = {
    "sqrt": math.sqrt,
    "abs": abs,
    "round": round,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "pi": math.pi,
    "e": math.e,
    "inf": math.inf,
}


def safe_eval(expr: str) -> float:
    """
    Evalúa expresiones matemáticas de forma segura usando AST.
    No permite acceso a módulos, atributos, imports ni código arbitrario.
    """
    # Normalizar: convertir ^ a ** (users expect ^ = power, not XOR)
    # Cuidado: solo fuera de strings o contextos especiales
    import re

    expr = re.sub(r"(?<!\*)(\^)(?!\*)", "**", expr)

    tree = ast.parse(expr, mode="eval")

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"Tipo no permitido: {type(node.value)}")
        elif isinstance(node, ast.BinOp):
            op = _SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Operador no permitido: {type(node.op).__name__}")
            left = _eval(node.left)
            right = _eval(node.right)
            # Limitar exponenciación para evitar DoS
            if isinstance(node.op, ast.Pow):
                if isinstance(right, (int, float)) and abs(right) > 1000:
                    raise ValueError("Exponente demasiado grande (máx 1000)")
                if isinstance(left, (int, float)) and abs(left) > 1e15:
                    raise ValueError("Base demasiado grande para exponenciación")
            result = op(left, right)
            # Guard against results that consume too much memory
            if isinstance(result, int) and result.bit_length() > 4096:
                raise ValueError("Resultado demasiado grande (overflow de precisión)")
            if isinstance(result, float) and (result == float("inf") or result != result):
                raise ValueError("Resultado infinito o NaN")
            return result
        elif isinstance(node, ast.UnaryOp):
            op = _SAFE_OPERATORS.get(type(node.op))
            if op is None:
                raise ValueError(f"Operador unario no permitido: {type(node.op).__name__}")
            return op(_eval(node.operand))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in _SAFE_FUNCTIONS:
                func = _SAFE_FUNCTIONS[node.func.id]
                args = [_eval(arg) for arg in node.args]
                if callable(func):
                    return func(*args)
                return func  # constante (pi, e)
            raise ValueError(f"Función no permitida: {ast.dump(node.func)}")
        elif isinstance(node, ast.Name):
            if node.id in _SAFE_FUNCTIONS:
                val = _SAFE_FUNCTIONS[node.id]
                if not callable(val):
                    return val  # constante
            raise ValueError(f"Variable no permitida: {node.id}")
        else:
            raise ValueError(f"Nodo AST no permitido: {type(node).__name__}")

    return _eval(tree)


class CalculatorSkill(BaseSkill):
    """
    Skill: Evaluación matemática segura.
    Soporta: +, -, *, /, //, %, **, sqrt, log, sin, cos, tan, pi, e, abs, round.
    No permite ejecución de código arbitrario.
    """

    def __init__(self):
        super().__init__(name="calculator", description="Evalúa expresiones matemáticas de forma segura")

    def validate_inputs(self, inputs: Dict[str, Any]) -> tuple[bool, str]:
        expr = inputs.get("expression", "").strip()
        if not expr:
            return False, "Se requiere 'expression' no vacío"
        if len(expr) > 500:
            return False, "Expresión demasiado larga (máx 500 chars)"
        # Bloquea patrones peligrosos
        for forbidden in ["import", "__", "eval", "exec", "open", "os.", "sys."]:
            if forbidden in expr:
                return False, f"Expresión contiene patrón prohibido: '{forbidden}'"
        return True, ""

    async def execute(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        self.execution_count += 1

        is_valid, error = self.validate_inputs(inputs)
        if not is_valid:
            return {"success": False, "result": None, "error": error, "execution_time": 0}

        try:
            expression = inputs["expression"].strip()
            value = safe_eval(expression)

            elapsed = round(time.time() - start, 4)
            self.last_execution = datetime.now()

            return {
                "success": True,
                "result": {
                    "expression": expression,
                    "value": value,
                    "type": type(value).__name__,
                },
                "error": None,
                "execution_time": elapsed,
            }

        except Exception as e:
            elapsed = round(time.time() - start, 4)
            return {
                "success": False,
                "result": None,
                "error": f"Error evaluando '{inputs.get('expression', '')}': {str(e)}",
                "execution_time": elapsed,
            }

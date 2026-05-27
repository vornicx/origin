"""Scheduler para ejecución paralela de steps de skills.

Modelo: **future-based push**.

No hay loop central que escanee la lista de pending; cada step se lanza como
una corrutina que `await`s las `Future`s de sus dependencias y, al terminar,
publica su resultado en su propia `Future`. El event loop hace el dispatch.

Ventajas vs. el modelo anterior (poll + asyncio.wait):
  - Sin polling: el step se reanuda al instante en que su última dep resuelve,
    no en el próximo wakeup del orquestador.
  - Sin escaneo O(pending) por iteración → overhead constante por step.
  - Concurrencia limitada por `Semaphore`, no por batches artificiales.
  - Cancelación natural: si una dep falla, la `Future` se resuelve con un
    sentinel `_FAIL` y los descendientes se marcan `skipped` sin ejecutar.

Topología:
  - Detecta ciclos por DFS antes de ejecutar (cero coste si no hay).
  - Soporta `${step_N.field}` para acceder a sub-campos del resultado.
"""

from typing import Dict, Any, List, Optional
import asyncio
import logging
import time

logger = logging.getLogger("origin.skills.scheduler")


# Sentinel marker: propagated through Futures to indicate a failed upstream.
class _DepFailed:
    __slots__ = ("ref", "reason")

    def __init__(self, ref: str, reason: str):
        self.ref = ref
        self.reason = reason


class PlanStep:
    __slots__ = ("idx", "step_num", "action", "skill", "inputs", "output_ref", "depends_on")

    def __init__(self, step: Dict[str, Any], idx: int):
        self.idx = idx
        self.step_num = step.get("step", idx + 1)
        self.action = step.get("action", "")
        self.skill = step.get("skill", "")
        self.inputs = step.get("inputs", {}) or {}
        self.output_ref = step.get("output_ref", f"step_{self.step_num}")
        self.depends_on: List[str] = step.get("depends_on", []) or []

    @property
    def is_leaf(self) -> bool:
        return not self.skill or self.skill in ("direct_response", "direct_llm_call", "none")


class SkillScheduler:
    """Future-based DAG scheduler.

    Cada `output_ref` tiene una `Future` precreada. Cada step corre como
    corrutina independiente que `await`s las futures de sus deps. Al terminar
    publica su resultado. El event loop se encarga del dispatch.
    """

    def __init__(self, skill_executor, max_concurrent: int = 16):
        self._executor = skill_executor
        self._default_max = max_concurrent

    # ── Ciclo / DAG ─────────────────────────────────────────────

    def _detect_cycles(self, steps: List[PlanStep]) -> Optional[List[str]]:
        graph: Dict[str, List[str]] = {s.output_ref: list(s.depends_on) for s in steps}
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {ref: WHITE for ref in graph}

        def dfs(node: str, stack: List[str]) -> Optional[List[str]]:
            color[node] = GRAY
            for dep in graph.get(node, []):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    if dep in stack:
                        return stack[stack.index(dep) :] + [dep]
                    return stack + [dep]
                if color[dep] == WHITE:
                    cycle = dfs(dep, stack + [dep])
                    if cycle:
                        return cycle
            color[node] = BLACK
            return None

        for ref in graph:
            if color[ref] == WHITE:
                cycle = dfs(ref, [ref])
                if cycle:
                    return cycle
        return None

    # ── Resolución de inputs ────────────────────────────────────

    @staticmethod
    def _resolve_value(v: Any, results: Dict[str, Any]) -> Any:
        if not isinstance(v, str) or not v.startswith("$"):
            return v
        ref = v[1:]
        if ref.startswith("{") and ref.endswith("}"):
            inner = ref[1:-1]
            if "." in inner:
                base, field = inner.split(".", 1)
                val = results.get(base)
                if isinstance(val, dict):
                    return val.get(field, v)
                return v
            return results.get(inner, v)
        if ref == "prev" and results:
            return results[next(reversed(results))]
        return results.get(ref, v)

    def _resolve_inputs(self, step: PlanStep, results: Dict[str, Any]) -> Dict[str, Any]:
        return {k: self._resolve_value(v, results) for k, v in step.inputs.items()}

    # ── Runner por step ─────────────────────────────────────────

    async def _step_runner(
        self,
        step: PlanStep,
        futures: Dict[str, asyncio.Future],
        outputs: List[Dict[str, Any]],
        skills_executed: List[str],
        sem: asyncio.Semaphore,
    ) -> None:
        # 1) Esperar deps. Si alguna es _DepFailed → marcar skip y propagar.
        dep_results: Dict[str, Any] = {}
        failed_dep: Optional[str] = None
        if step.depends_on:
            try:
                resolved = await asyncio.gather(
                    *(futures[d] for d in step.depends_on if d in futures),
                    return_exceptions=False,
                )
            except Exception as e:
                # Una future no debería raise — pero por defensa
                fut = futures[step.output_ref]
                if not fut.done():
                    fut.set_result(_DepFailed(step.output_ref, f"dep gather raised: {e}"))
                outputs.append(
                    {
                        "step": step.step_num,
                        "action": step.action,
                        "skill": step.skill,
                        "success": False,
                        "result": None,
                        "error": f"Dep error: {e}",
                    }
                )
                return
            for dep, val in zip([d for d in step.depends_on if d in futures], resolved):
                if isinstance(val, _DepFailed):
                    failed_dep = dep
                    break
                dep_results[dep] = val

        if failed_dep is not None:
            outputs.append(
                {
                    "step": step.step_num,
                    "action": step.action,
                    "skill": step.skill,
                    "success": False,
                    "result": None,
                    "error": f"Skipped: dependency '{failed_dep}' failed",
                }
            )
            futures[step.output_ref].set_result(_DepFailed(step.output_ref, f"upstream {failed_dep}"))
            return

        # 2) Leaf: respuesta directa, no llama al executor.
        if step.is_leaf:
            res = {"text": "direct_response"}
            outputs.append(
                {
                    "step": step.step_num,
                    "action": step.action,
                    "skill": step.skill,
                    "success": True,
                    "result": res,
                    "error": None,
                    "execution_time": 0,
                }
            )
            futures[step.output_ref].set_result(res)
            return

        # 3) Ejecutar la skill respetando el semáforo del run actual.
        resolved_inputs = self._resolve_inputs(step, dep_results)
        async with sem:
            try:
                result = await self._executor.execute(step.skill, resolved_inputs)
            except Exception as e:
                outputs.append(
                    {
                        "step": step.step_num,
                        "action": step.action,
                        "skill": step.skill,
                        "success": False,
                        "result": None,
                        "error": f"Exception: {e}",
                    }
                )
                futures[step.output_ref].set_result(_DepFailed(step.output_ref, str(e)))
                return

        success = bool(result.get("success"))
        outputs.append(
            {
                "step": step.step_num,
                "action": step.action,
                "skill": step.skill,
                **result,
            }
        )
        if success:
            if step.skill:
                skills_executed.append(step.skill)
            futures[step.output_ref].set_result(result.get("result"))
        else:
            futures[step.output_ref].set_result(_DepFailed(step.output_ref, result.get("error", "unknown failure")))

    # ── API pública ─────────────────────────────────────────────

    async def execute(self, plan: List[Dict[str, Any]], max_concurrent: Optional[int] = None) -> Dict[str, Any]:
        total = len(plan)
        if total == 0:
            return {
                "success": True,
                "results": [],
                "skills_executed": [],
                "plan_executed": 0,
                "total_steps": 0,
                "elapsed": 0.0,
            }

        steps = [PlanStep(s, i) for i, s in enumerate(plan)]

        # Fast path 1: plan trivial (1 step) — sin futures, sin gather, sin DFS.
        if total == 1:
            s = steps[0]
            start = time.perf_counter()
            if s.is_leaf:
                elapsed = round(time.perf_counter() - start, 4)
                return {
                    "success": True,
                    "results": [
                        {
                            "step": s.step_num,
                            "action": s.action,
                            "skill": s.skill,
                            "success": True,
                            "result": {"text": "direct_response"},
                            "error": None,
                            "execution_time": 0,
                        }
                    ],
                    "skills_executed": [],
                    "plan_executed": 1,
                    "total_steps": 1,
                    "elapsed": elapsed,
                    "failed": [],
                }
            try:
                result = await self._executor.execute(s.skill, s.inputs)
            except Exception as e:
                result = {"success": False, "result": None, "error": f"Exception: {e}"}
            elapsed = round(time.perf_counter() - start, 4)
            ok = bool(result.get("success"))
            return {
                "success": ok,
                "results": [{"step": s.step_num, "action": s.action, "skill": s.skill, **result}],
                "skills_executed": [s.skill] if ok and s.skill else [],
                "plan_executed": 1 if ok else 0,
                "total_steps": 1,
                "elapsed": elapsed,
                "failed": [] if ok else [s.output_ref],
            }

        # Fast path 2: si NINGÚN step tiene depends_on, podemos saltar cycle detection.
        any_deps = any(s.depends_on for s in steps)
        if any_deps:
            cycle = self._detect_cycles(steps)
            if cycle:
                return {
                    "success": False,
                    "results": [],
                    "skills_executed": [],
                    "plan_executed": 0,
                    "total_steps": total,
                    "elapsed": 0.0,
                    "error": f"Cycle detected: {' -> '.join(cycle)}",
                }

        # Semáforo dimensionado al run: por defecto min(default, total) para no
        # crear capacidad ociosa, con override por argumento.
        limit = max_concurrent if max_concurrent is not None else min(self._default_max, total)
        sem = asyncio.Semaphore(max(1, limit))

        loop = asyncio.get_event_loop()
        futures: Dict[str, asyncio.Future] = {s.output_ref: loop.create_future() for s in steps}
        outputs: List[Dict[str, Any]] = []
        skills_executed: List[str] = []

        start = time.perf_counter()
        await asyncio.gather(*(self._step_runner(s, futures, outputs, skills_executed, sem) for s in steps))
        elapsed = round(time.perf_counter() - start, 4)

        failed = [s.output_ref for s in steps if isinstance(futures[s.output_ref].result(), _DepFailed)]
        success = len(failed) == 0
        completed = total - len(failed)

        return {
            "success": success,
            "results": sorted(outputs, key=lambda r: r.get("step", 0)),
            "skills_executed": list(dict.fromkeys(skills_executed)),
            "plan_executed": completed,
            "total_steps": total,
            "elapsed": elapsed,
            "failed": failed,
        }

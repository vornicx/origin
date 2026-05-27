"""Benchmark scheduler nuevo (as_completed) vs implementación previa (batch-and-wait).

Replica la lógica del scheduler antiguo (`OldScheduler`) y la nueva
(`skills.scheduler.SkillScheduler`) y las corre sobre varios DAGs con
latencias controladas. Mide tiempo total y throughput.

DAGs probados:
  1. lineal          A -> B -> C -> D -> E (5 nodos)
  2. diamond         A -> {B, C} -> D
  3. cadena_amplia   A -> {B, C}; B -> D; C -> E; D -> F   (A->B->D bloqueaba el batch)
  4. mixto           dos cadenas independientes solapando
  5. fanout_4        A -> {B, C, D, E} (paralelismo puro)

Cada step tiene una duración determinística para que la diferencia sea
atribuible al scheduling, no al ejecutor.
"""
import asyncio
import os
import statistics
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# === Fake executor con latencia configurable =====================
class FakeExecutor:
    def __init__(self, default_delay: float = 0.1):
        self.default_delay = default_delay
        self.exec_count = 0

    async def execute(self, skill: str, inputs: Dict[str, Any]) -> Dict[str, Any]:
        self.exec_count += 1
        delay = inputs.get("__delay__", self.default_delay)
        await asyncio.sleep(delay)
        return {"success": True, "result": {"skill": skill, "delay": delay}, "skills_executed": [skill]}


# === Reimplementación FIEL del scheduler antiguo (batch-and-wait) ==
# Copia de la lógica previa para comparar apples-to-apples.
class OldScheduler:
    def __init__(self, executor, max_concurrent: int = 5):
        self._executor = executor
        self._max_concurrent = max_concurrent

    def _resolve_inputs(self, step, results):
        resolved = {}
        for k, v in step.get("inputs", {}).items():
            if isinstance(v, str) and v.startswith("$"):
                ref_key = v[1:]
                if ref_key in results:
                    resolved[k] = results[ref_key]
                else:
                    resolved[k] = v
            else:
                resolved[k] = v
        return resolved

    async def execute(self, plan: List[Dict[str, Any]]) -> Dict[str, Any]:
        steps = list(plan)
        for i, s in enumerate(steps):
            s.setdefault("output_ref", f"step_{s.get('step', i+1)}")
            s.setdefault("depends_on", [])
        results: Dict[str, Any] = {}
        completed = set()
        remaining = set(range(len(steps)))
        start = time.perf_counter()
        step_results = []
        while remaining:
            ready = []
            for i in remaining:
                if all(d in results for d in steps[i]["depends_on"]):
                    ready.append(i)
            if not ready:
                break
            batch = ready[: self._max_concurrent]

            async def _run(i):
                s = steps[i]
                resolved = self._resolve_inputs(s, results)
                r = await self._executor.execute(s.get("skill", ""), resolved)
                return i, r

            batch_results = await asyncio.gather(*[_run(i) for i in batch])
            for i, r in batch_results:
                results[steps[i]["output_ref"]] = r.get("result")
                step_results.append({"step": steps[i].get("step", i + 1), **r})
                remaining.remove(i)
                completed.add(i)
        return {
            "success": len(completed) == len(steps),
            "elapsed": time.perf_counter() - start,
            "results": step_results,
        }


# === DAGs de prueba ==============================================
def dag_linear(delay=0.1):
    return [
        {"step": 1, "skill": "a", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": delay}},
        {"step": 2, "skill": "b", "output_ref": "r2", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
        {"step": 3, "skill": "c", "output_ref": "r3", "depends_on": ["r2"], "inputs": {"__delay__": delay}},
        {"step": 4, "skill": "d", "output_ref": "r4", "depends_on": ["r3"], "inputs": {"__delay__": delay}},
        {"step": 5, "skill": "e", "output_ref": "r5", "depends_on": ["r4"], "inputs": {"__delay__": delay}},
    ]


def dag_diamond(delay=0.1):
    return [
        {"step": 1, "skill": "a", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": delay}},
        {"step": 2, "skill": "b", "output_ref": "r2", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
        {"step": 3, "skill": "c", "output_ref": "r3", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
        {"step": 4, "skill": "d", "output_ref": "r4", "depends_on": ["r2", "r3"], "inputs": {"__delay__": delay}},
    ]


def dag_chain_wide(delay_short=0.05, delay_long=0.30):
    # A rápido, B largo, C rápido, D depende de B (largo), E depende de C (corto), F depende de D
    # En batch-and-wait, [B, C] van juntos en un batch -> debes esperar B antes de lanzar D y E.
    # En as_completed, en cuanto C termina puedes lanzar E inmediatamente.
    return [
        {"step": 1, "skill": "a", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": delay_short}},
        {"step": 2, "skill": "b", "output_ref": "r2", "depends_on": ["r1"], "inputs": {"__delay__": delay_long}},
        {"step": 3, "skill": "c", "output_ref": "r3", "depends_on": ["r1"], "inputs": {"__delay__": delay_short}},
        {"step": 4, "skill": "d", "output_ref": "r4", "depends_on": ["r2"], "inputs": {"__delay__": delay_short}},
        {"step": 5, "skill": "e", "output_ref": "r5", "depends_on": ["r3"], "inputs": {"__delay__": delay_short}},
        {"step": 6, "skill": "f", "output_ref": "r6", "depends_on": ["r4"], "inputs": {"__delay__": delay_short}},
    ]


def dag_mixed():
    # Dos cadenas independientes con duraciones distintas:
    # A(0.05) -> B(0.20) -> C(0.05)
    # D(0.20) -> E(0.05) -> F(0.20)
    return [
        {"step": 1, "skill": "a", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": 0.05}},
        {"step": 2, "skill": "b", "output_ref": "r2", "depends_on": ["r1"], "inputs": {"__delay__": 0.20}},
        {"step": 3, "skill": "c", "output_ref": "r3", "depends_on": ["r2"], "inputs": {"__delay__": 0.05}},
        {"step": 4, "skill": "d", "output_ref": "r4", "depends_on": [], "inputs": {"__delay__": 0.20}},
        {"step": 5, "skill": "e", "output_ref": "r5", "depends_on": ["r4"], "inputs": {"__delay__": 0.05}},
        {"step": 6, "skill": "f", "output_ref": "r6", "depends_on": ["r5"], "inputs": {"__delay__": 0.20}},
    ]


def dag_fanout(delay=0.1):
    return [
        {"step": 1, "skill": "a", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": delay}},
        {"step": 2, "skill": "b", "output_ref": "r2", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
        {"step": 3, "skill": "c", "output_ref": "r3", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
        {"step": 4, "skill": "d", "output_ref": "r4", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
        {"step": 5, "skill": "e", "output_ref": "r5", "depends_on": ["r1"], "inputs": {"__delay__": delay}},
    ]


# === Lower-bound teórico (camino crítico) ========================
def critical_path(plan):
    """Calcula la suma de delays a lo largo del camino más largo."""
    by_ref = {s.get("output_ref", f"step_{s.get('step')}"): s for s in plan}
    memo = {}

    def cost(ref):
        if ref in memo:
            return memo[ref]
        s = by_ref[ref]
        delay = s.get("inputs", {}).get("__delay__", 0.1)
        deps = s.get("depends_on", [])
        c = delay + (max((cost(d) for d in deps), default=0))
        memo[ref] = c
        return c

    return max(cost(ref) for ref in by_ref)


# === Runner ======================================================
from skills.scheduler import SkillScheduler  # noqa: E402


async def bench_one(name: str, dag_fn, runs: int = 5):
    plan = dag_fn()
    lower_bound = critical_path(plan)

    old_times = []
    new_times = []
    for _ in range(runs):
        plan_old = dag_fn()
        plan_new = dag_fn()

        old_sched = OldScheduler(FakeExecutor())
        t0 = time.perf_counter()
        await old_sched.execute(plan_old)
        old_times.append(time.perf_counter() - t0)

        new_sched = SkillScheduler(FakeExecutor())
        t0 = time.perf_counter()
        await new_sched.execute(plan_new)
        new_times.append(time.perf_counter() - t0)

    old_med = statistics.median(old_times)
    new_med = statistics.median(new_times)
    speedup = old_med / new_med if new_med > 0 else 0
    old_overhead = (old_med - lower_bound) * 1000
    new_overhead = (new_med - lower_bound) * 1000

    print(f"\n{name}")
    print(f"  critical path (lower bound): {lower_bound * 1000:7.1f} ms")
    print(f"  OLD batch-and-wait (median): {old_med * 1000:7.1f} ms  (+{old_overhead:5.1f}ms overhead)")
    print(f"  NEW as_completed   (median): {new_med * 1000:7.1f} ms  (+{new_overhead:5.1f}ms overhead)")
    print(f"  speedup:                     {speedup:5.2f}x")
    return speedup


async def main():
    print("=" * 70)
    print("Scheduler benchmark — OLD batch-and-wait vs NEW future-push")
    print(f"({10} runs per case, median reported)")
    print("=" * 70)

    speedups = []
    # Standard delays (100ms) — dominado por timer del OS
    speedups.append(await bench_one("1. lineal A->B->C->D->E (delay 0.1s)", dag_linear, runs=10))
    speedups.append(await bench_one("2. diamond A->{B,C}->D (delay 0.1s)", dag_diamond, runs=10))
    speedups.append(await bench_one("3. chain_wide (mixed delays)     ", dag_chain_wide, runs=10))
    speedups.append(await bench_one("4. mixed (two parallel chains)   ", dag_mixed, runs=10))
    speedups.append(await bench_one("5. fanout A->{B,C,D,E} (delay 0.1)", dag_fanout, runs=10))

    # Delays cortos (1ms) — aísla el overhead del scheduler
    print("\n--- Short delays (1ms): isolates scheduler overhead ---")

    def short_lin():
        return dag_linear(delay=0.001)

    def short_dia():
        return dag_diamond(delay=0.001)

    def short_fan():
        return dag_fanout(delay=0.001)
    speedups.append(await bench_one("6. lineal (delay 1ms)            ", short_lin, runs=20))
    speedups.append(await bench_one("7. diamond (delay 1ms)           ", short_dia, runs=20))
    speedups.append(await bench_one("8. fanout (delay 1ms)            ", short_fan, runs=20))

    # Plan grande para amplificar paralelismo
    print("\n--- Wide DAGs (more nodes) ---")

    def dag_wide_fanout():
        out = [{"step": 1, "skill": "root", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": 0.05}}]
        for i in range(2, 21):
            out.append(
                {
                    "step": i,
                    "skill": f"leaf{i}",
                    "output_ref": f"r{i}",
                    "depends_on": ["r1"],
                    "inputs": {"__delay__": 0.05},
                }
            )
        return out

    speedups.append(await bench_one("9. wide_fanout (1 -> 20 leaves)  ", dag_wide_fanout, runs=10))

    def dag_two_chains_long():
        out = []
        # Chain A: 8 nodes mixed delays
        for i in range(1, 9):
            delay = 0.20 if i % 3 == 0 else 0.05
            deps = [f"r{i-1}"] if i > 1 else []
            out.append(
                {"step": i, "skill": f"a{i}", "output_ref": f"r{i}", "depends_on": deps, "inputs": {"__delay__": delay}}
            )
        # Chain B independent: 8 nodes
        for j in range(1, 9):
            delay = 0.05 if j % 3 == 0 else 0.20
            deps = [f"s{j-1}"] if j > 1 else []
            out.append(
                {
                    "step": 100 + j,
                    "skill": f"b{j}",
                    "output_ref": f"s{j}",
                    "depends_on": deps,
                    "inputs": {"__delay__": delay},
                }
            )
        return out

    speedups.append(await bench_one("10. two_long_chains (16 nodes)   ", dag_two_chains_long, runs=10))

    print("\n" + "=" * 70)
    print(f"Speedup geomean: {statistics.geometric_mean(speedups):.2f}x")
    print(f"Speedup max:     {max(speedups):.2f}x")
    print(f"Speedup min:     {min(speedups):.2f}x")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())

"""¿Cuánto del overhead es el scheduler vs el OS?

Mide el costo mínimo de N sleeps secuenciales y N en paralelo sin scheduler:
solo `asyncio.gather` directo. Si el scheduler nuevo está cerca de esto,
significa que ya no hay margen para mejorar más en esos DAGs.
"""
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def sleep_then(d):
    await asyncio.sleep(d)
    return d


async def measure_sequential(n, d, runs=10):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        for _ in range(n):
            await asyncio.sleep(d)
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


async def measure_parallel(n, d, runs=10):
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        await asyncio.gather(*(sleep_then(d) for _ in range(n)))
        times.append(time.perf_counter() - t0)
    return statistics.median(times) * 1000


async def main():
    from skills.scheduler import SkillScheduler

    class FakeExec:
        async def execute(self, skill, inputs):
            await asyncio.sleep(inputs.get("__delay__", 0.1))
            return {"success": True, "result": {}, "skills_executed": [skill]}

    print(f"{'case':<40} {'theoretical':<13} {'raw asyncio':<13} {'new sched':<13} {'sched ov':<10}")
    print("-" * 95)

    # Lineal: 5 × 0.1s
    theo = 500.0
    raw_seq = await measure_sequential(5, 0.1)
    sch = SkillScheduler(FakeExec())
    plan = [
        {
            "step": i + 1,
            "skill": "x",
            "output_ref": f"r{i+1}",
            "depends_on": [f"r{i}"] if i > 0 else [],
            "inputs": {"__delay__": 0.1},
        }
        for i in range(5)
    ]
    t = []
    for _ in range(10):
        t0 = time.perf_counter()
        await sch.execute(plan)
        t.append((time.perf_counter() - t0) * 1000)
    sched_lin = statistics.median(t)
    print(
        f"{'linear 5x100ms':<40} {theo:>8.1f}ms   {raw_seq:>8.1f}ms   {sched_lin:>8.1f}ms   {sched_lin - raw_seq:>+5.1f}ms"  # noqa: E501
    )

    # Diamond: 100 + 100 + 100 = 300
    theo = 300.0

    async def diamond_raw():
        await sleep_then(0.1)
        await asyncio.gather(sleep_then(0.1), sleep_then(0.1))
        await sleep_then(0.1)

    t_raw = []
    for _ in range(10):
        t0 = time.perf_counter()
        await diamond_raw()
        t_raw.append((time.perf_counter() - t0) * 1000)
    raw_dia = statistics.median(t_raw)
    plan = [
        {"step": 1, "skill": "a", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": 0.1}},
        {"step": 2, "skill": "b", "output_ref": "r2", "depends_on": ["r1"], "inputs": {"__delay__": 0.1}},
        {"step": 3, "skill": "c", "output_ref": "r3", "depends_on": ["r1"], "inputs": {"__delay__": 0.1}},
        {"step": 4, "skill": "d", "output_ref": "r4", "depends_on": ["r2", "r3"], "inputs": {"__delay__": 0.1}},
    ]
    t = []
    for _ in range(10):
        t0 = time.perf_counter()
        await sch.execute(plan)
        t.append((time.perf_counter() - t0) * 1000)
    sched_dia = statistics.median(t)
    print(
        f"{'diamond A->{B,C}->D':<40} {theo:>8.1f}ms   {raw_dia:>8.1f}ms   {sched_dia:>8.1f}ms   {sched_dia - raw_dia:>+5.1f}ms"  # noqa: E501
    )

    # Fanout: 100 + max(100*5) = 200
    theo = 200.0

    async def fanout_raw():
        await sleep_then(0.1)
        await asyncio.gather(*(sleep_then(0.1) for _ in range(5)))

    t_raw = []
    for _ in range(10):
        t0 = time.perf_counter()
        await fanout_raw()
        t_raw.append((time.perf_counter() - t0) * 1000)
    raw_fan = statistics.median(t_raw)
    plan = [{"step": 1, "skill": "r", "output_ref": "r1", "depends_on": [], "inputs": {"__delay__": 0.1}}]
    for i in range(2, 7):
        plan.append(
            {"step": i, "skill": f"l{i}", "output_ref": f"r{i}", "depends_on": ["r1"], "inputs": {"__delay__": 0.1}}
        )
    t = []
    for _ in range(10):
        t0 = time.perf_counter()
        await sch.execute(plan)
        t.append((time.perf_counter() - t0) * 1000)
    sched_fan = statistics.median(t)
    print(
        f"{'fanout A->{B,C,D,E,F}':<40} {theo:>8.1f}ms   {raw_fan:>8.1f}ms   {sched_fan:>8.1f}ms   {sched_fan - raw_fan:>+5.1f}ms"  # noqa: E501
    )


if __name__ == "__main__":
    asyncio.run(main())

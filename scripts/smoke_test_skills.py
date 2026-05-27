"""
Per-skill smoke test via the running Origin HTTP API.

We import skill_executor in-process via the api.main module (which already wired the
correct init order) and exercise each skill with a safe action.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Suppress backend bootstrap (we are NOT starting uvicorn, just importing module).
os.environ.setdefault("ORIGIN_TEST_MODE", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Safe default actions per skill — chosen to avoid destructive ops.
SKILL_TESTS: list[tuple[str, dict[str, Any]]] = [
    ("web_search", {"query": "Origin Tauri Rust"}),
    ("datetime", {"action": "now"}),
    ("system_info", {"action": "overview"}),
    ("calculator", {"expression": "2 * (3 + 4)"}),
    ("web_scraper", {"url": "https://httpbin.org/html", "action": "text"}),
    ("shell", {"action": "run", "command": "echo hello-from-origin"}),
    ("external_apis", {"action": "weather", "city": "Madrid"}),
    ("memory_compaction", {"action": "stats"}),
    ("file_manager", {"action": "list", "path": "."}),
    ("code_doctor", {"action": "scan", "path": str(ROOT / "frontend")}),
    ("vision", {"action": "screenshot"}),
    ("ui_automation", {"action": "window", "sub_action": "list"}),
    ("notification", {"action": "toast", "title": "Origin Test", "message": "smoke test"}),
    ("monitor", {"action": "status"}),
    ("clipboard", {"action": "read"}),
    ("camera", {"action": "status"}),
    ("active_window", {"action": "current"}),
    ("os_control", {"action": "battery"}),
    ("voice", {"action": "status"}),
    ("wake_word", {"action": "status"}),
    ("system_tray", {"action": "status"}),
    ("telegram", {"action": "status"}),
    ("cron", {"action": "list"}),
    ("eventlog", {"action": "read", "limit": 5}),
    ("browser", {"action": "navigate", "url": "about:blank", "headless": True}),
    ("custom_vision", {"action": "status"}),
    ("app_integrations", {"action": "status"}),
    ("scheduler", {"action": "status"}),
    ("self_improvement", {"action": "status"}),
    ("plugin_hooks", {"action": "list"}),
    ("agent_workspace", {"action": "list"}),
]


async def main():
    # Use the api.main module since it already resolves the import order correctly.
    # Importing api.main creates the FastAPI app + Mind instance but does NOT
    # start uvicorn (uvicorn is only started when api.main is the entry point).
    import api.main as api_main
    mind = api_main.mind
    executor = mind.skill_executor

    registered = set(executor._registry.keys())
    print(f"Registered skills ({len(registered)}): {sorted(registered)}\n")

    results: list[tuple[str, str, str, float]] = []
    for name, inputs in SKILL_TESTS:
        if name not in registered:
            results.append(("SKIP", name, f"not registered", 0))
            continue
        t0 = time.time()
        try:
            r = await asyncio.wait_for(executor.execute(name, inputs), timeout=30)
            elapsed = time.time() - t0
            if r.get("success"):
                preview = json.dumps(r.get("result"), default=str)[:160]
                results.append(("PASS", name, preview, elapsed))
            else:
                err = (r.get("error") or "no error msg")[:200]
                results.append(("FAIL", name, err, elapsed))
        except asyncio.TimeoutError:
            results.append(("FAIL", name, "timeout (>30s)", 30.0))
        except Exception as e:
            elapsed = time.time() - t0
            results.append(("FAIL", name, f"{type(e).__name__}: {e}"[:200], elapsed))

    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for label, *_ in results:
        counts[label] += 1

    print("\n=== SKILL SMOKE TEST RESULTS ===")
    print(f"PASS={counts['PASS']}  FAIL={counts['FAIL']}  SKIP={counts['SKIP']}\n")

    for cat in ("FAIL", "SKIP", "PASS"):
        items = [r for r in results if r[0] == cat]
        if not items:
            continue
        print(f"--- {cat} ({len(items)}) ---")
        for label, name, msg, elapsed in items:
            print(f"  {name:22} ({elapsed:5.2f}s)  {msg}")
        print()


if __name__ == "__main__":
    asyncio.run(main())

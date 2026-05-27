"""
Origin smoke test runner.
Iterates over all endpoints in openapi_snapshot.json, calls each with
reasonable defaults, classifies results into PASS / SKIP / FAIL.
"""
from __future__ import annotations
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any
import urllib.request
import urllib.error

# Force UTF-8 stdout so we don't crash on non-cp1252 bytes from API output
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:9001"
SNAPSHOT = Path(__file__).parent / "openapi_snapshot.json"

# Endpoints we deliberately skip (destructive, require auth, slow, etc.)
SKIP_PATHS = {
    "/auth/login", "/auth/register", "/auth/logout",  # auth flow
    "/os/power/{action}",  # power off / sleep
    "/cron/jobs/{job_id}/pause", "/cron/jobs/{job_id}/resume",
    "/services/{name}/disconnect", "/services/{name}/connect",
    "/services/{name}/permissions",
    "/workflows/{workflow_id}/disable", "/workflows/{workflow_id}/enable",
    "/workflows/{workflow_id}/run",
    "/context/auto/sources/{name}/disable", "/context/auto/sources/{name}/enable",
    "/proactive/dismiss/{event_id}", "/proactive/execute/{event_id}",
    "/proactive/start", "/proactive/stop",
    "/system/tray/start", "/system/tray/stop",
    "/telegram/start", "/telegram/stop",
    "/voice/wake-word/start", "/voice/wake-word/stop",
    "/dashboard/monitor/start", "/dashboard/monitor/stop",
    "/dashboard/improvement/cycle",
    "/dashboard/subconscious/reflect",
    "/core/refresh",
    "/{full_path}",  # catch-all SPA route
    "/os/wifi/connect",  # would change network
    "/clipboard/write",  # mutates clipboard
    "/telegram/send",
    "/context/{filename}",  # POST mutates; GET/DELETE handled by render
    "/workflows",  # POST creates workflow — needs deep payload, skip in smoke
    "/cron/jobs",  # POST creates real cron job — destructive
    "/mcp/tools/call",  # depends on registered MCP tools
    "/voice/stt",  # multipart upload, skip in smoke
}

# Default request bodies for POST endpoints (matched to real schemas in api/main.py)
DEFAULT_BODIES: dict[str, dict[str, Any]] = {
    "/mind/think": {"input": "¿Qué hora es?"},
    "/mind/memory/search": {"query": "test", "limit": 3},
    "/subagent/delegate": {"instruction": "echo test"},
    "/subagent/gather": {},
    "/auth/check": {},
    "/voice/speak": {"text": "test silencioso", "play": False},
    "/camera/analyze": {"prompt": "describe la imagen"},
    "/camera/capture": {},
    "/context/auto/bootstrap": {"inject_into_memory": False},
    "/services/ping_all": {},
}

# Path params filled in for parameterised routes (skip if no match)
PATH_PARAM_VALUES = {
    "filename": "default",
    "name": "datetime",
    "action": "status",
    "job_id": "nonexistent",
    "task_id": "nonexistent",
    "workflow_id": "nonexistent",
    "event_id": "nonexistent",
    "full_path": "",
}


# Per-endpoint timeouts (seconds). LLM calls and Windows COM cold-starts need more.
TIMEOUTS = {
    "/mind/think": 90.0,
    "/voice/speak": 45.0,
    "/voice/stt": 45.0,
    "/camera/analyze": 30.0,
    "/camera/capture": 15.0,
    "/services/ping_all": 30.0,
    "/dashboard/screenshot": 20.0,
    "/dashboard/skills": 30.0,
    "/dashboard/improvement": 15.0,
    "/context/auto/bootstrap": 30.0,
    "/subagent/gather": 90.0,
}

def call(method: str, path: str, body: Any = None, timeout: float = 15.0):
    url = BASE + path
    # OS/camera/proactive endpoints often need extra time on cold start
    if path.startswith(("/os/", "/camera/", "/window/", "/clipboard/")):
        timeout = max(timeout, 25.0)
    timeout = TIMEOUTS.get(path, timeout)
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, raw[:600].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:600].decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def render_path(path: str) -> str | None:
    out = path
    for k, v in PATH_PARAM_VALUES.items():
        out = out.replace("{" + k + "}", v)
    if "{" in out:
        return None
    return out


def main():
    spec = json.loads(SNAPSHOT.read_text())
    results = []
    paths = sorted(spec["paths"].items())
    for path, methods in paths:
        for method, info in methods.items():
            method = method.upper()
            if method not in ("GET", "POST", "DELETE", "PUT", "PATCH"):
                continue
            if path in SKIP_PATHS:
                results.append(("SKIP", method, path, "deliberately skipped"))
                continue
            rendered = render_path(path)
            if rendered is None:
                results.append(("SKIP", method, path, "unresolved path param"))
                continue
            body = None
            if method != "GET" and method != "DELETE":
                body = DEFAULT_BODIES.get(path, {})
            try:
                code, snippet = call(method, rendered, body)
            except Exception as e:
                code, snippet = -1, f"call raised: {e}"
            if 200 <= code < 300:
                label = "PASS"
            elif code in (404, 405, 422):
                label = "WARN"
            else:
                label = "FAIL"
            results.append((label, method, path, f"{code} :: {snippet[:140]}"))

    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for label, _, _, _ in results:
        counts[label] += 1

    print(f"\n=== SMOKE TEST RESULTS ===")
    print(f"Total: {len(results)}  PASS={counts['PASS']}  WARN={counts['WARN']}  FAIL={counts['FAIL']}  SKIP={counts['SKIP']}\n")

    for cat in ("FAIL", "WARN", "PASS", "SKIP"):
        items = [r for r in results if r[0] == cat]
        if not items:
            continue
        print(f"\n--- {cat} ({len(items)}) ---")
        for label, method, path, msg in items:
            print(f"  {method:6} {path:55} {msg}")

if __name__ == "__main__":
    main()

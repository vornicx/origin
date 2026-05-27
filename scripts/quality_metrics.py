#!/usr/bin/env python3
"""
Quality metrics tracker — records and trends key health indicators over time.

Usage:
    python scripts/quality_metrics.py record [--vuln-count N] [--bandit-issues N] [--coverage F]
    python scripts/quality_metrics.py trend [--last N]
    python scripts/quality_metrics.py show
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

METRICS_DIR = Path(__file__).parent.parent / "metrics"
HISTORY_FILE = METRICS_DIR / "history.jsonl"


def _count_tests() -> int:
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/", "--collect-only", "-q", "--no-header"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    for line in result.stdout.splitlines():
        if "selected" in line or "test" in line:
            parts = line.split()
            for i, p in enumerate(parts):
                if p.isdigit():
                    return int(p)
    return 0


def _count_source_lines() -> int:
    total = 0
    root = Path(__file__).parent.parent
    for p in root.rglob("*.py"):
        if any(part in p.parts for part in ("venv", ".venv", "__pycache__", "migrations")):
            continue
        try:
            total += len(p.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            pass
    return total


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


def cmd_record(args: argparse.Namespace) -> None:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)

    test_count = _count_tests()
    loc = _count_source_lines()
    commit = _git_commit()

    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "commit": commit,
        "test_count": test_count,
        "loc": loc,
        "coverage_pct": float(args.coverage) if args.coverage is not None else None,
        "vuln_count": int(args.vuln_count) if args.vuln_count is not None else None,
        "bandit_issues": int(args.bandit_issues) if args.bandit_issues is not None else None,
    }

    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    print(f"Recorded metrics snapshot ({commit}):")
    for k, v in entry.items():
        if k not in ("ts", "commit"):
            print(f"  {k}: {v}")


def _load_history(last: int | None = None) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    entries = [json.loads(l) for l in lines if l.strip()]
    if last:
        entries = entries[-last:]
    return entries


def cmd_trend(args: argparse.Namespace) -> None:
    history = _load_history(last=args.last)
    if not history:
        print("No metrics history found. Run 'record' first.")
        return

    header = f"{'Date':<12} {'Commit':<8} {'Tests':>6} {'LOC':>7} {'Cov%':>6} {'CVEs':>5} {'Bandit':>7}"
    print(header)
    print("-" * len(header))
    for e in history:
        date = e["ts"][:10]
        commit = e.get("commit", "?")[:7]
        tests = e.get("test_count", "?")
        loc = e.get("loc", "?")
        cov = f"{e['coverage_pct']:.1f}" if e.get("coverage_pct") is not None else "?"
        vulns = e.get("vuln_count", "?")
        bandit = e.get("bandit_issues", "?")
        print(f"{date:<12} {commit:<8} {str(tests):>6} {str(loc):>7} {cov:>6} {str(vulns):>5} {str(bandit):>7}")

    if len(history) >= 2:
        first, last_e = history[0], history[-1]
        print()
        print("Trend (first → latest):")
        for key, label in [("test_count", "Tests"), ("coverage_pct", "Coverage %"), ("vuln_count", "CVEs")]:
            a, b = first.get(key), last_e.get(key)
            if a is not None and b is not None:
                delta = b - a
                sign = "+" if delta >= 0 else ""
                arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "─")
                print(f"  {arrow} {label}: {a} → {b} ({sign}{delta})")


def cmd_show(args: argparse.Namespace) -> None:
    history = _load_history()
    if not history:
        print("No metrics history.")
        return
    latest = history[-1]
    print("Latest snapshot:")
    print(json.dumps(latest, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Origin quality metrics tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="Record a new metrics snapshot")
    rec.add_argument("--vuln-count", default=None)
    rec.add_argument("--bandit-issues", default=None)
    rec.add_argument("--coverage", default=None)

    trend = sub.add_parser("trend", help="Show trend table")
    trend.add_argument("--last", type=int, default=12, metavar="N")

    sub.add_parser("show", help="Show latest snapshot as JSON")

    args = parser.parse_args()
    dispatch = {"record": cmd_record, "trend": cmd_trend, "show": cmd_show}
    dispatch[args.command](args)


if __name__ == "__main__":
    main()

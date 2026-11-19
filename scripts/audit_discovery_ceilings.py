#!/usr/bin/env python3
"""Audit discovery bounds so a per-run budget cannot become a lifetime ceiling.

Item 15 asks for fixed ranges, pagination limits, parser assumptions and
traversal exclusions to be audited, and for per-run budgets to resume fairly.
Two properties make that true or false, and both are checkable statically:

1. **Every scheduler call persists its rotation.** `retry.select` reserves part
   of a batch for retries and alternates lanes when only one slot fits. That
   alternation lives in `state['retry_rotation'][queue]`. A call that omits
   `state=` cannot rotate at all, so a small `--limit` becomes a permanent
   ceiling on everything after the first batch. A call that omits `queue=`
   shares the `default` lane with any other caller on the same document, so two
   queues can starve each other.

2. **Bounded spaces expand instead of stopping.** The metadata model space and
   the numeric version window are explored in passes. A namespace that reached
   its current bound must raise it and restart, rather than declaring the space
   complete.

This reads source only, so it runs in CI and in the test suite.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# `selectors.DefaultSelector().select()` is the stdlib, not our scheduler.
STDLIB_OWNERS = frozenset({"selector", "selectors", "select"})


def scheduler_calls(root: Path) -> list[dict]:
    """Every `select(...)` scheduler call and whether it persists its rotation."""
    calls = []
    for path in sorted([*(root / "src/sonos_firmware").glob("*.py"),
                        *(root / "scripts").glob("*.py")]):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = getattr(function, "attr", None) or getattr(function, "id", None)
            if name != "select":
                continue
            if isinstance(function, ast.Attribute) and getattr(function.value, "id", None) in STDLIB_OWNERS:
                continue
            keywords = {keyword.arg for keyword in node.keywords if keyword.arg}
            calls.append({
                "file": str(path.relative_to(root)),
                "line": node.lineno,
                "persists_state": "state" in keywords,
                "names_queue": "queue" in keywords,
            })
    return calls


def unrotated_calls(calls: list[dict]) -> list[dict]:
    """Calls that would turn a small batch into a permanent coverage ceiling."""
    return [call for call in calls if not call["persists_state"]]


def unnamed_queue_calls(calls: list[dict]) -> list[dict]:
    """Calls that would share the default rotation lane with another consumer."""
    return [call for call in calls if call["persists_state"] and not call["names_queue"]]


def expanding_bounds(root: Path) -> dict:
    """The bounded spaces must widen when they reach their current limit."""
    frontier = (root / "src/sonos_firmware/frontier.py").read_text()
    compact = "".join(frontier.split())
    return {
        # A namespace that exhausted its range raises the bound and restarts.
        "model_space_expands": "2*row['model_max']+1" in compact,
        "model_policy_named": "'expanding-passes'" in frontier,
    }


def configurable_budgets(root: Path) -> dict:
    """Per-run budgets exposed by the target providers."""
    text = (root / "src/sonos_firmware/platform/providers.py").read_text()
    tree = ast.parse(text)
    budgets: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "options"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            budgets.setdefault(node.args[0].value, []).append(f"line {node.lineno}")
    return budgets


def audit(root: Path = ROOT) -> dict:
    calls = scheduler_calls(root)
    return {
        "schema_version": 1,
        "scheduler_calls": len(calls),
        "unrotated_calls": unrotated_calls(calls),
        "unnamed_queue_calls": unnamed_queue_calls(calls),
        "expanding_bounds": expanding_bounds(root),
        "configurable_budget_count": len(configurable_budgets(root)),
        "configurable_budgets": sorted(configurable_budgets(root)),
    }


def failures(report: dict) -> list[str]:
    problems = []
    if report["unrotated_calls"]:
        problems.append(f"unrotated scheduler calls (limit becomes a lifetime ceiling): "
                        f"{report['unrotated_calls']}")
    if report["unnamed_queue_calls"]:
        problems.append(f"scheduler calls sharing the default rotation lane: "
                        f"{report['unnamed_queue_calls']}")
    if not report["expanding_bounds"]["model_space_expands"]:
        problems.append("model exploration no longer widens its bound")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="write the ceiling audit as JSON")
    args = parser.parse_args()
    root = args.root.resolve()
    report = audit(root)
    if args.output:
        sys.path.insert(0, str(root / "src"))
        from sonos_firmware.storage import atomic_json
        atomic_json(args.output, report)
    print(f"scheduler_calls={report['scheduler_calls']} "
          f"unrotated={len(report['unrotated_calls'])} "
          f"unnamed_queue={len(report['unnamed_queue_calls'])} "
          f"model_expands={report['expanding_bounds']['model_space_expands']} "
          f"budgets={report['configurable_budget_count']}")
    for problem in failures(report):
        print(f"  {problem}")
    return 1 if failures(report) else 0


if __name__ == "__main__":
    raise SystemExit(main())

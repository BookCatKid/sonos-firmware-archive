#!/usr/bin/env python3
"""Check the repository's own consistency invariants.

Item 20 asks that obsolete entrypoints and duplicated logic be reconciled, stale
documentation corrected, and reporting made actionable. Those are easy to
violate silently, so this audit states them as checks:

* every configured target provider has an implementation, and every
  implementation is configured -- no orphan adapter, no dangling reference;
* every target still plans from a checkout with no archive state;
* every script is either wired into a provider/workflow/task, imported by other
  code, or documented as a manual maintenance tool -- nothing is orphaned;
* documentation never points at a repo path that does not exist, except when the
  link is pinned to a historical commit;
* no documentation advertises a current workflow file that was retired.

It reads files only, so it runs in the test suite and on a worker.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A documentation link pinned to a commit describes history, not the current tree.
PINNED = re.compile(r"/blob/[0-9a-f]{7,40}/")
REPO_PATH = re.compile(r"`((?:src|scripts|config|tests|tools|docs|schemas|\.github)/[A-Za-z0-9_./-]+)`")
SCRIPT_NAME = re.compile(r"\b([a-z0-9_]+\.py)\b")


def provider_names(root: Path) -> tuple[set[str], set[str]]:
    """Return (configured, implemented) provider names."""
    from sonos_firmware.platform.providers import BUILTINS
    data = json.loads((root / "config/targets.json").read_text())
    configured = {target["provider"] for target in data["targets"]}
    return configured, set(BUILTINS)


def script_references(root: Path) -> set[str]:
    """Every script name mentioned by code, tasks, workflows or tests."""
    referenced: set[str] = set()
    haystacks = [
        *root.glob("src/**/*.py"),
        *root.glob(".github/workflows/*.yml"),
        *root.glob("tests/*.py"),
        *root.glob("scripts/*.py"),
    ]
    for path in haystacks:
        text = path.read_text(errors="replace")
        for match in SCRIPT_NAME.finditer(text):
            name = match.group(1)
            # A script naming itself is not a reference.
            if path.parent.name == "scripts" and path.name == name:
                continue
            referenced.add(name)
    return referenced


def documented_scripts(root: Path) -> set[str]:
    documented: set[str] = set()
    for path in [*(root / "docs").glob("*.md"), root / "README.md"]:
        if not path.exists():
            continue
        for match in SCRIPT_NAME.finditer(path.read_text(errors="replace")):
            documented.add(match.group(1))
    return documented


def documentation_paths(root: Path) -> list[dict]:
    """Relative repo paths named in documentation that do not exist."""
    problems = []
    for path in [*(root / "docs").glob("*.md"), root / "README.md"]:
        if not path.exists():
            continue
        text = path.read_text(errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            if PINNED.search(line):
                continue
            for match in REPO_PATH.finditer(line):
                reference = match.group(1)
                if not (root / reference).exists():
                    problems.append({"document": path.name, "line": line_number, "path": reference})
    return problems


def retired_workflows(root: Path) -> list[dict]:
    """Current (unpinned) workflow references that name a file that is gone."""
    present = {path.name for path in (root / ".github/workflows").glob("*.yml")}
    problems = []
    for path in [*(root / "docs").glob("*.md"), root / "README.md"]:
        if not path.exists():
            continue
        for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
            if PINNED.search(line):
                continue
            for name in re.findall(r"\.github/workflows/([A-Za-z0-9_.-]+\.ya?ml)", line):
                if name not in present:
                    problems.append({"document": path.name, "line": line_number, "workflow": name})
    return problems


def audit(root: Path = ROOT) -> dict:
    from sonos_firmware.platform.registry import targets
    from sonos_firmware.platform.runner import plan

    configured, implemented = provider_names(root)
    registry = targets(root)
    unreachable = []
    for target in registry.values():
        try:
            report = plan(target, root, "owner/repo")
        except Exception as error:  # noqa: BLE001 - the message is the finding
            unreachable.append({"target": target.name, "error": str(error)})
            continue
        if report["status"] != "planned" or not report["steps"]:
            unreachable.append({"target": target.name, "error": f"plan status {report['status']}"})

    scripts = {path.name for path in (root / "scripts").glob("*.py")}
    referenced = script_references(root) | documented_scripts(root)
    orphan_scripts = sorted(scripts - referenced)

    return {
        "schema_version": 1,
        "targets": len(registry),
        "providers_configured": sorted(configured),
        "providers_implemented": sorted(implemented),
        "providers_missing_implementation": sorted(configured - implemented),
        "providers_never_configured": sorted(implemented - configured),
        "unreachable_targets": unreachable,
        "scripts": len(scripts),
        "orphan_scripts": orphan_scripts,
        "documentation_paths": documentation_paths(root),
        "retired_workflows": retired_workflows(root),
    }


def failures(report: dict) -> list[str]:
    problems = []
    for key in ("providers_missing_implementation", "providers_never_configured",
                "unreachable_targets", "orphan_scripts", "documentation_paths", "retired_workflows"):
        if report[key]:
            problems.append(f"{key}: {report[key]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="write the consistency report as JSON")
    args = parser.parse_args()
    root = args.root.resolve()
    sys.path.insert(0, str(root / "src"))
    report = audit(root)
    if args.output:
        from sonos_firmware.storage import atomic_json
        atomic_json(args.output, report)
    print(f"targets={report['targets']} scripts={report['scripts']} "
          f"orphan_scripts={len(report['orphan_scripts'])} "
          f"doc_path_problems={len(report['documentation_paths'])} "
          f"retired_workflows={len(report['retired_workflows'])} "
          f"unreachable_targets={len(report['unreachable_targets'])}")
    for problem in failures(report):
        print(f"  {problem}")
    return 1 if failures(report) else 0


if __name__ == "__main__":
    raise SystemExit(main())

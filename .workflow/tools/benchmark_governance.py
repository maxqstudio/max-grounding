#!/usr/bin/env python3
"""Measure Governance Engine V2 against an accepted SW2 baseline.

`--root` selects the governed codebase. `--tool-root` selects the Skill Workflow
toolchain under test, so a pinned consumer can be measured with the candidate
engine rather than its older vendored tools.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, TypeVar

from extract_project_facts import extract_project_facts
from project_snapshot import ProjectSnapshot, active_project_snapshot
from sequence_contract import compute_source_digest, source_files

T = TypeVar("T")


def git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def timed(repeats: int, fn: Callable[[], T]) -> tuple[dict[str, float], T]:
    samples: list[float] = []
    value: T | None = None
    for _ in range(repeats):
        start = time.perf_counter()
        value = fn()
        samples.append(time.perf_counter() - start)
    assert value is not None
    return (
        {
            "repeats": repeats,
            "min_seconds": min(samples),
            "median_seconds": statistics.median(samples),
            "max_seconds": max(samples),
            "mean_seconds": statistics.fmean(samples),
        },
        value,
    )


def run_command(root: Path, command: list[str]) -> dict[str, object]:
    start = time.perf_counter()
    proc = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return {
        "command": command,
        "seconds": time.perf_counter() - start,
        "returncode": proc.returncode,
        "output_tail": proc.stdout.splitlines()[-20:],
    }


def governance_tool(tool_root: Path, name: str) -> Path | None:
    for candidate in (
        tool_root / "scripts" / name,
        tool_root / ".workflow" / "tools" / name,
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def copy_for_sync(root: Path, destination: Path) -> None:
    ignored = shutil.ignore_patterns(
        ".git",
        "docs",
        "artifacts",
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
    )
    shutil.copytree(root, destination, ignore=ignored)
    generated = destination / ".workflow" / "generated"
    if generated.exists():
        shutil.rmtree(generated)


def baseline_comparison(path: Path | None, commands: dict[str, dict[str, object]], timings: dict[str, dict[str, float]]) -> dict[str, object]:
    if path is None:
        return {"status": "NOT_DECLARED"}
    if not path.is_file():
        return {"status": "MISSING", "path": str(path)}
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "INVALID", "path": str(path), "error": type(exc).__name__}

    result: dict[str, object] = {"status": "AVAILABLE", "path": str(path)}
    old_sync = baseline.get("commands", {}).get("sync_project_truth", {}).get("seconds")
    new_sync = commands.get("sync_project_truth", {}).get("seconds")
    if isinstance(old_sync, (int, float)) and isinstance(new_sync, (int, float)) and old_sync > 0:
        result["sync_project_truth"] = {
            "baseline_seconds": old_sync,
            "candidate_seconds": new_sync,
            "ratio": new_sync / old_sync,
            "improvement_percent": (1.0 - (new_sync / old_sync)) * 100.0,
            "improved": new_sync < old_sync,
        }
    old_facts = baseline.get("timings", {}).get("extract_project_facts", {}).get("median_seconds")
    new_facts = timings.get("extract_project_facts", {}).get("median_seconds")
    if isinstance(old_facts, (int, float)) and isinstance(new_facts, (int, float)) and old_facts > 0:
        result["extract_project_facts"] = {
            "baseline_median_seconds": old_facts,
            "candidate_median_seconds": new_facts,
            "ratio": new_facts / old_facts,
            "improvement_percent": (1.0 - (new_facts / old_facts)) * 100.0,
            "improved": new_facts < old_facts,
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--tool-root", default=".")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--output", default="artifacts/performance/sw2-01-engine.json")
    ap.add_argument("--include-selftests", action="store_true")
    ap.add_argument("--expected-head", default="")
    ap.add_argument("--label", default="SW2-01 Governance Engine V2")
    ap.add_argument("--baseline", default="")
    args = ap.parse_args()

    if args.repeats < 1:
        print("FAIL repeats must be >= 1")
        return 2

    root = Path(args.root).resolve()
    tool_root = Path(args.tool_root).resolve()
    sync_tool = governance_tool(tool_root, "sync_project_truth.py")
    docs_validator = governance_tool(tool_root, "validate_project_docs.py")
    if sync_tool is None or docs_validator is None:
        print("FAIL governance sync/docs tools missing")
        return 2

    observed_head = git(root, "rev-parse", "HEAD") or "NOT_AVAILABLE"
    expected_head = args.expected_head.strip()

    inventory_timing, inventory = timed(args.repeats, lambda: source_files(root))
    source_bytes = sum(path.stat().st_size for path in inventory)
    python_files = [path for path in inventory if path.suffix.lower() == ".py"]
    digest_timing, source_digest = timed(args.repeats, lambda: compute_source_digest(root))
    facts_timing, facts = timed(args.repeats, lambda: extract_project_facts(root))
    snapshot_timing, snapshot = timed(args.repeats, lambda: ProjectSnapshot.capture(root))

    fresh_snapshot = ProjectSnapshot.capture(root)
    start = time.perf_counter()
    with active_project_snapshot(fresh_snapshot):
        first_facts = extract_project_facts(root)
    first_fact_seconds = time.perf_counter() - start
    cached_timing, cached_facts = timed(
        args.repeats,
        lambda: _cached_facts(root, fresh_snapshot),
    )

    commands: dict[str, dict[str, object]] = {}
    with tempfile.TemporaryDirectory(prefix="skill-workflow-sw2-engine-") as td:
        fixture = Path(td) / "repo"
        copy_for_sync(root, fixture)
        commands["sync_project_truth"] = run_command(
            fixture,
            [sys.executable, str(sync_tool), "--root", str(fixture)],
        )
        commands["validate_project_docs_after_sync"] = run_command(
            fixture,
            [sys.executable, str(docs_validator), "--root", str(fixture)],
        )

    if args.include_selftests:
        engine_selftest = governance_tool(tool_root, "selftest_governance_engine.py")
        compiler_selftest = governance_tool(tool_root, "selftest_project_truth_compiler.py")
        strict_selftest = governance_tool(tool_root, "selftest_strict_project_workflow.py")
        if engine_selftest is None or compiler_selftest is None or strict_selftest is None:
            print("FAIL requested selftests are missing")
            return 2
        commands["governance_engine_selftest"] = run_command(
            tool_root, [sys.executable, str(engine_selftest)]
        )
        commands["project_truth_compiler_selftest"] = run_command(
            tool_root, [sys.executable, str(compiler_selftest)]
        )
        commands["strict_workflow_selftest"] = run_command(
            tool_root, [sys.executable, str(strict_selftest)]
        )

    failures = [
        name
        for name, result in commands.items()
        if int(result.get("returncode", 1)) != 0
    ]
    if expected_head and observed_head != expected_head:
        failures.append(
            "PROVENANCE_MISMATCH:observed=" + observed_head + ":expected=" + expected_head
        )
    if snapshot.source_digest != source_digest:
        failures.append("SNAPSHOT_DIGEST_PARITY_FAILED")
    if first_facts.get("source_digest") != source_digest or cached_facts.get("source_digest") != source_digest:
        failures.append("SNAPSHOT_FACT_PARITY_FAILED")

    timings = {
        "source_files": inventory_timing,
        "compute_source_digest": digest_timing,
        "extract_project_facts": facts_timing,
        "project_snapshot_capture": snapshot_timing,
        "first_facts_from_snapshot_seconds": {"seconds": first_fact_seconds},
        "cached_facts_from_snapshot": cached_timing,
    }
    baseline_path = Path(args.baseline).resolve() if args.baseline.strip() else None
    comparison = baseline_comparison(baseline_path, commands, timings)

    report = {
        "schema_version": 2,
        "benchmark": args.label,
        "observed_head": observed_head,
        "expected_head": expected_head or "NOT_DECLARED",
        "exact_head_match": bool(expected_head) and observed_head == expected_head,
        "tool_root": str(tool_root),
        "governed_root": str(root),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "source": {
            "digest": source_digest,
            "files": len(inventory),
            "python_files": len(python_files),
            "bytes": source_bytes,
            "lines": facts.get("source_summary", {}).get("lines"),
        },
        "timings": timings,
        "snapshot_metrics_after_fact_reuse": fresh_snapshot.metrics(),
        "current_io_model": {
            "governance_engine_source_enumerations": 1,
            "source_file_reads_per_snapshot": 1,
            "derived_project_facts": "memoized per active immutable snapshot",
            "authority_boundary": (
                "Snapshot/memoized facts are process-local acceleration. A fresh exact-head process "
                "must recapture source before final acceptance."
            ),
        },
        "baseline_comparison": comparison,
        "commands": commands,
        "failures": failures,
        "result": "FAIL" if failures else "PASS",
        "boundary": (
            "Hosted-runner timings are comparative evidence, not hardware-absolute performance claims. "
            "Governance parity still requires exact-head source/test/validator acceptance."
        ),
    }

    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failures else 0


def _cached_facts(root: Path, snapshot: ProjectSnapshot) -> dict:
    with active_project_snapshot(snapshot):
        return extract_project_facts(root)


if __name__ == "__main__":
    raise SystemExit(main())

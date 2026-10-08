#!/usr/bin/env python3
"""Validate the SW2-19 evidence-backed governance mode performance budget."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

MODES = ("develop", "verify", "finalize")


def close_enough(left: float, right: float, *, tolerance: float = 1e-9) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def derived_budget(observed_max: float, multiplier: float, quantum: float) -> float:
    return math.ceil((float(observed_max) * float(multiplier)) / float(quantum)) * float(quantum)


def validate_contract(data: dict) -> list[str]:
    failures: list[str] = []
    if data.get("schema_version") != 1:
        failures.append("SCHEMA_VERSION")
    if data.get("phase") != "SW2-19":
        failures.append("PHASE")
    for field in ("candidate_sha", "base_sha", "measurement_method", "evidence_boundary"):
        if not str(data.get(field, "")).strip():
            failures.append(f"MISSING_{field.upper()}")

    runners = int(data.get("independent_runners", 0) or 0)
    repeats = int(data.get("repeats_per_runner", 0) or 0)
    expected_count = runners * repeats
    if runners < 3:
        failures.append("INDEPENDENT_RUNNERS_LT_3")
    if repeats < 5:
        failures.append("REPEATS_PER_RUNNER_LT_5")
    if int(data.get("benchmark_run_id", 0) or 0) <= 0:
        failures.append("BENCHMARK_RUN_ID")

    artifacts = data.get("artifacts") or []
    if len(artifacts) != runners:
        failures.append("ARTIFACT_COUNT")
    seen_samples: set[int] = set()
    seen_ids: set[int] = set()
    for artifact in artifacts:
        sample = int(artifact.get("sample", 0) or 0)
        artifact_id = int(artifact.get("artifact_id", 0) or 0)
        digest = str(artifact.get("sha256", "")).strip().lower()
        if sample <= 0 or sample in seen_samples:
            failures.append("ARTIFACT_SAMPLE_IDENTITY")
        if artifact_id <= 0 or artifact_id in seen_ids:
            failures.append("ARTIFACT_IDENTITY")
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            failures.append("ARTIFACT_SHA256")
        seen_samples.add(sample)
        seen_ids.add(artifact_id)

    derivation = data.get("derivation") or {}
    multiplier = float(derivation.get("safety_multiplier", 0) or 0)
    quantum = float(derivation.get("round_up_seconds", 0) or 0)
    if multiplier <= 1.0:
        failures.append("SAFETY_MULTIPLIER")
    if quantum <= 0.0:
        failures.append("ROUND_UP_SECONDS")
    if derivation.get("enforcement_statistic") != "median_seconds":
        failures.append("ENFORCEMENT_STATISTIC")

    modes = data.get("modes") or {}
    if set(modes) != set(MODES):
        failures.append("MODE_SET")
        return failures

    for mode in MODES:
        contract = modes.get(mode) or {}
        try:
            values = [float(item) for item in contract.get("observed_seconds", [])]
        except (TypeError, ValueError):
            failures.append(f"{mode.upper()}_OBSERVATION_TYPE")
            continue
        if len(values) != expected_count:
            failures.append(f"{mode.upper()}_OBSERVATION_COUNT")
            continue
        if not values or any(value <= 0.0 for value in values):
            failures.append(f"{mode.upper()}_OBSERVATION_VALUE")
            continue

        median = statistics.median(values)
        maximum = max(values)
        recorded_median = float(contract.get("observed_median_seconds", -1))
        recorded_max = float(contract.get("observed_max_seconds", -1))
        budget = float(contract.get("budget_seconds", -1))
        if not close_enough(median, recorded_median):
            failures.append(f"{mode.upper()}_MEDIAN_MISMATCH")
        if not close_enough(maximum, recorded_max):
            failures.append(f"{mode.upper()}_MAX_MISMATCH")
        if multiplier > 0.0 and quantum > 0.0:
            expected_budget = derived_budget(maximum, multiplier, quantum)
            if not close_enough(budget, expected_budget):
                failures.append(f"{mode.upper()}_BUDGET_DERIVATION")
        if median > budget:
            failures.append(f"{mode.upper()}_MEDIAN_OVER_BUDGET")

    if data.get("result") != "PASS":
        failures.append("RESULT")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--budget",
        default="benchmarks/baselines/sw2-19-v2.1-mode-budget.json",
    )
    args = parser.parse_args()
    root = Path(args.root).resolve()
    path = (root / args.budget).resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"result": "FAIL", "failures": [f"BUDGET_LOAD:{exc}"]}, indent=2))
        return 1

    failures = validate_contract(data)
    payload = {
        "schema_version": 1,
        "budget": path.relative_to(root).as_posix(),
        "candidate_sha": data.get("candidate_sha"),
        "benchmark_run_id": data.get("benchmark_run_id"),
        "budgets_seconds": {
            mode: (data.get("modes") or {}).get(mode, {}).get("budget_seconds")
            for mode in MODES
        },
        "failures": failures,
        "result": "PASS" if not failures else "FAIL",
        "evidence_boundary": "This validator proves internal integrity of the accepted multi-runner SW2-19 timing evidence and its deterministic budget derivation. It does not replace exact-head governance acceptance or remeasure GitHub runner performance.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

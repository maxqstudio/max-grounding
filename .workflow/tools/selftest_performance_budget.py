#!/usr/bin/env python3
"""Regression tests for the SW2-19 performance-budget contract."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "scripts" / "validate_performance_budget.py"
BUDGET = ROOT / "benchmarks" / "baselines" / "sw2-19-v2.1-mode-budget.json"


def load_module():
    spec = importlib.util.spec_from_file_location("validate_performance_budget", VALIDATOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("PERFORMANCE_BUDGET_VALIDATOR_IMPORT")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    module = load_module()
    baseline = json.loads(BUDGET.read_text(encoding="utf-8"))

    failures = module.validate_contract(copy.deepcopy(baseline))
    require(not failures, f"BASELINE_SHOULD_PASS:{failures}")

    over_budget = copy.deepcopy(baseline)
    over_budget["modes"]["verify"]["budget_seconds"] = 1.0
    failures = module.validate_contract(over_budget)
    require("VERIFY_BUDGET_DERIVATION" in failures, "OVER_BUDGET_DERIVATION_NOT_CAUGHT")
    require("VERIFY_MEDIAN_OVER_BUDGET" in failures, "OVER_BUDGET_MEDIAN_NOT_CAUGHT")

    missing_mode = copy.deepcopy(baseline)
    del missing_mode["modes"]["finalize"]
    failures = module.validate_contract(missing_mode)
    require("MODE_SET" in failures, "MISSING_MODE_NOT_CAUGHT")

    weak_sample = copy.deepcopy(baseline)
    weak_sample["independent_runners"] = 1
    failures = module.validate_contract(weak_sample)
    require("INDEPENDENT_RUNNERS_LT_3" in failures, "RUNNER_COUNT_NOT_CAUGHT")

    tampered_median = copy.deepcopy(baseline)
    tampered_median["modes"]["develop"]["observed_median_seconds"] += 0.25
    failures = module.validate_contract(tampered_median)
    require("DEVELOP_MEDIAN_MISMATCH" in failures, "MEDIAN_TAMPER_NOT_CAUGHT")

    bad_digest = copy.deepcopy(baseline)
    bad_digest["artifacts"][0]["sha256"] = "bad"
    failures = module.validate_contract(bad_digest)
    require("ARTIFACT_SHA256" in failures, "ARTIFACT_DIGEST_NOT_CAUGHT")

    print("PERFORMANCE_BUDGET_BASELINE=PASS")
    print("PERFORMANCE_BUDGET_OVER_LIMIT_DETECTION=PASS")
    print("PERFORMANCE_BUDGET_MODE_SET_DETECTION=PASS")
    print("PERFORMANCE_BUDGET_SAMPLE_COUNT_DETECTION=PASS")
    print("PERFORMANCE_BUDGET_TAMPER_DETECTION=PASS")
    print("PERFORMANCE_BUDGET_SELFTEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

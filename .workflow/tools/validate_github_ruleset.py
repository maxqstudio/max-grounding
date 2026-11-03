#!/usr/bin/env python3
"""Validate a GitHub branch-ruleset export against Skill Workflow policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

REQUIRED_CHECKS = (
    "Self Governance (ubuntu-latest)",
    "Governance Selftest (ubuntu-latest)",
    "Governance Selftest (windows-latest)",
    "SW2 Sequence Evidence (ubuntu-latest)",
    "Governance Engine Performance (ubuntu-latest)",
    "Consumer Engine Performance (max-grounding)",
)


def validate(payload: object) -> dict[str, object]:
    failures: list[str] = []
    if not isinstance(payload, dict):
        return {
            "schema_version": 1,
            "result": "FAIL",
            "failures": ["RULESET_PAYLOAD_NOT_OBJECT"],
            "required_checks": list(REQUIRED_CHECKS),
        }

    if payload.get("target") != "branch":
        failures.append("RULESET_TARGET_NOT_BRANCH")
    if payload.get("enforcement") != "active":
        failures.append("RULESET_NOT_ACTIVE")

    conditions = payload.get("conditions", {})
    refs = conditions.get("ref_name", {}) if isinstance(conditions, dict) else {}
    includes = refs.get("include", []) if isinstance(refs, dict) else []
    if "~DEFAULT_BRANCH" not in includes:
        failures.append("DEFAULT_BRANCH_NOT_TARGETED")

    rules = payload.get("rules", [])
    rules = rules if isinstance(rules, list) else []
    by_type = {item.get("type"): item for item in rules if isinstance(item, dict) and item.get("type")}
    for required in ("deletion", "non_fast_forward", "pull_request"):
        if required not in by_type:
            failures.append("RULE_MISSING:" + required)

    pull = by_type.get("pull_request", {})
    pull_params = pull.get("parameters", {}) if isinstance(pull, dict) else {}
    methods = pull_params.get("allowed_merge_methods", []) if isinstance(pull_params, dict) else []
    if set(methods) != {"squash"}:
        failures.append("MERGE_METHODS_NOT_SQUASH_ONLY")

    status_rule = by_type.get("required_status_checks")
    observed_checks: set[str] = set()
    strict_policy: bool | None = None
    if not isinstance(status_rule, dict):
        failures.append("RULE_MISSING:required_status_checks")
    else:
        params = status_rule.get("parameters", {})
        if isinstance(params, dict):
            strict_policy = params.get("strict_required_status_checks_policy")
            raw = params.get("required_status_checks", [])
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict) and isinstance(item.get("context"), str):
                        observed_checks.add(item["context"])
        missing = [name for name in REQUIRED_CHECKS if name not in observed_checks]
        failures.extend("REQUIRED_STATUS_CHECK_MISSING:" + name for name in missing)

    return {
        "schema_version": 1,
        "result": "FAIL" if failures else "PASS",
        "ruleset_id": payload.get("id", "UNKNOWN"),
        "ruleset_name": payload.get("name", "UNKNOWN"),
        "required_checks": list(REQUIRED_CHECKS),
        "observed_required_checks": sorted(observed_checks),
        "strict_required_status_checks_policy": strict_policy,
        "failures": failures,
        "evidence_boundary": (
            "This validator proves the supplied live ruleset payload requires PR+squash governance, blocks deletion/non-fast-forward updates, "
            "targets the default branch, and requires every permanent acceptance check. Workflow files alone are insufficient evidence."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ruleset", required=True)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    payload = json.loads(Path(args.ruleset).read_text(encoding="utf-8"))
    report = validate(payload)
    output = json.dumps(report, indent=2, sort_keys=True)
    print(output)
    if args.report:
        path = Path(args.report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(output + "\n", encoding="utf-8", newline="\n")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

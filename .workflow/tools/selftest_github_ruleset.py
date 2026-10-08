#!/usr/bin/env python3
"""Regression tests for GitHub ruleset policy validation."""

from __future__ import annotations

from copy import deepcopy

from validate_github_ruleset import REQUIRED_CHECKS, validate


def good_ruleset() -> dict[str, object]:
    return {
        "id": 1,
        "name": "protection",
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "pull_request", "parameters": {"allowed_merge_methods": ["squash"]}},
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": name, "integration_id": 15368} for name in REQUIRED_CHECKS
                    ],
                },
            },
        ],
    }


def main() -> int:
    baseline = good_ruleset()
    assert validate(baseline)["result"] == "PASS"

    missing_check = deepcopy(baseline)
    status_rule = next(item for item in missing_check["rules"] if item["type"] == "required_status_checks")
    status_rule["parameters"]["required_status_checks"] = status_rule["parameters"]["required_status_checks"][:-1]
    report = validate(missing_check)
    assert report["result"] == "FAIL"
    assert any(item.startswith("REQUIRED_STATUS_CHECK_MISSING:") for item in report["failures"])

    no_pr = deepcopy(baseline)
    no_pr["rules"] = [item for item in no_pr["rules"] if item["type"] != "pull_request"]
    assert validate(no_pr)["result"] == "FAIL"

    extra_merge_method = deepcopy(baseline)
    pull = next(item for item in extra_merge_method["rules"] if item["type"] == "pull_request")
    pull["parameters"]["allowed_merge_methods"] = ["squash", "merge"]
    assert validate(extra_merge_method)["result"] == "FAIL"

    print("RULESET_POLICY_BASELINE=PASS")
    print("MISSING_REQUIRED_CHECK_REJECTION=PASS")
    print("MISSING_PR_RULE_REJECTION=PASS")
    print("NON_SQUASH_MERGE_REJECTION=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

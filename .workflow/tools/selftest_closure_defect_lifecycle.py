#!/usr/bin/env python3
"""Regression coverage for closure-state and known-defect lifecycle authority."""

from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import validate_closure_defect_lifecycle as lifecycle

BASE = "a" * 40


def write_json(root: Path, path: str, value: object) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def fixture() -> dict[str, object]:
    return {
        "roadmap": {
            "schema_version": 1,
            "current_phase": "SW2-21",
            "phases": [
                {"id": "SW2-20", "title": "Done", "status": "COMPLETE"},
                {"id": "SW2-21", "title": "Current", "status": "CURRENT"},
            ],
        },
        "state": {
            "schema_version": 1,
            "phase": "SW2-21",
            "status": "IN_PROGRESS",
            "working_branch": "work/sw2-21",
            "last_accepted_branch": "main",
            "last_accepted_sha": BASE,
            "next_authorized_actions": ["Complete the current governed phase."],
        },
        "acceptance": {
            "schema_version": 1,
            "sequence_session": "SW2-21-GOVERNANCE",
        },
        "session": {
            "schema_version": 1,
            "session_id": "SW2-21-GOVERNANCE",
            "phase": "SW2-21",
            "implementation_base_sha": BASE,
        },
        "defects": {
            "schema_version": 1,
            "defects": [
                {
                    "id": "DEF-OPEN",
                    "summary": "Still open",
                    "status": "OPEN",
                    "evidence": "reproducer",
                },
                {
                    "id": "DEF-FIXED",
                    "summary": "Fixed",
                    "status": "FIXED/ACCEPTED",
                    "evidence": "original defect evidence",
                    "resolution_phase": "SW2-20",
                    "resolution_evidence": "accepted SW2-20 gate",
                },
                {
                    "id": "DEF-HIST",
                    "summary": "Historical accepted boundary",
                    "status": "HISTORICAL",
                    "evidence": "original observation",
                    "resolution_phase": "SW2-20",
                    "resolution_evidence": "accepted explicit boundary",
                },
                {
                    "id": "DEF-NP",
                    "summary": "Not proven",
                    "status": "NOT_PROVEN",
                    "evidence": "insufficient evidence",
                },
            ],
        },
    }


def materialize(root: Path, data: dict[str, object]) -> None:
    write_json(root, ".workflow/roadmap.json", data["roadmap"])
    write_json(root, ".workflow/state.json", data["state"])
    write_json(root, ".workflow/acceptance.json", data["acceptance"])
    write_json(root, ".workflow/known_defects.json", data["defects"])
    write_json(root, "docs/sequence/sessions/SW2-21-GOVERNANCE.json", data["session"])


def require_failure(data: dict[str, object], expected: str) -> None:
    with tempfile.TemporaryDirectory(prefix="sw2-closure-defect-") as td:
        root = Path(td)
        materialize(root, data)
        report = lifecycle.validate(root)
        failures = report["failures"]
        if report["result"] != "FAIL" or not any(expected in item for item in failures):
            raise RuntimeError(f"EXPECTED_FAILURE_MISSING:{expected}:{failures}")


def fixture_regressions() -> None:
    with tempfile.TemporaryDirectory(prefix="sw2-closure-defect-") as td:
        root = Path(td)
        data = fixture()
        materialize(root, data)
        report = lifecycle.validate(root, expected_base=BASE)
        if report["result"] != "PASS":
            raise RuntimeError("VALID_FIXTURE_REJECTED:" + repr(report["failures"]))

    data = fixture()
    data["state"]["last_accepted_sha"] = "b" * 40
    require_failure(data, "IMPLEMENTATION_BASE_MISMATCH")

    data = fixture()
    data["state"].update({
        "status": "SW2_21_ACCEPTED",
        "working_branch": "main",
        "next_authorized_actions": ["Mark PR #99 ready and squash-merge it."],
    })
    require_failure(data, "STALE_ACCEPTED_CLOSURE_ACTION")

    data = fixture()
    data["state"].update({
        "status": "SW2_21_ACCEPTED",
        "working_branch": "work/sw2-21",
        "next_authorized_actions": ["Open the next governed boundary after main acceptance."],
    })
    require_failure(data, "ACCEPTED_STATE_WORKING_BRANCH_NOT_MAIN")

    data = fixture()
    data["defects"]["defects"][0]["status"] = "CONFIRMED"
    require_failure(data, "DEFECT_STATUS_INVALID")

    data = fixture()
    data["defects"]["defects"][1].pop("resolution_evidence")
    require_failure(data, "DEFECT_RESOLUTION_EVIDENCE_MISSING")

    data = fixture()
    data["defects"]["defects"][2]["resolution_phase"] = "SW2-99"
    require_failure(data, "DEFECT_RESOLUTION_PHASE_UNKNOWN")

    data = fixture()
    duplicate = copy.deepcopy(data["defects"]["defects"][0])
    data["defects"]["defects"].append(duplicate)
    require_failure(data, "DEFECT_ID_DUPLICATE")

    data = fixture()
    data["state"].update({
        "status": "SW2_21_ACCEPTED",
        "working_branch": "main",
        "next_authorized_actions": [
            "After accepted main and post-merge evidence are settled, open the next governed boundary."
        ],
    })
    with tempfile.TemporaryDirectory(prefix="sw2-closure-defect-") as td:
        root = Path(td)
        materialize(root, data)
        report = lifecycle.validate(root)
        if report["result"] != "PASS":
            raise RuntimeError("BRANCH_AGNOSTIC_ACCEPTED_STATE_REJECTED:" + repr(report["failures"]))

    data = fixture()
    data["state"].update({
        "status": "SW2_21_ACCEPTED",
        "working_branch": "main",
        "last_accepted_sha": "b" * 40,
        "next_authorized_actions": [
            "Treat the accepted phase as terminal until the roadmap is explicitly extended."
        ],
    })
    with tempfile.TemporaryDirectory(prefix="sw2-closure-defect-") as td:
        root = Path(td)
        materialize(root, data)
        report = lifecycle.validate(root, expected_base=BASE)
        if report["result"] != "PASS":
            raise RuntimeError("ACCEPTED_BASE_LIFECYCLE_REJECTED:" + repr(report["failures"]))

    print("CLOSURE_BASE_REBIND_REJECTION=PASS")
    print("CLOSURE_STALE_ACTION_REJECTION=PASS")
    print("DEFECT_LIFECYCLE_NEGATIVE_PATHS=PASS")
    print("BRANCH_AGNOSTIC_ACCEPTED_CLOSURE=PASS")
    print("ACCEPTED_BASE_LIFECYCLE=PASS")


def main() -> int:
    current = lifecycle.validate(Path.cwd())
    if current["result"] != "PASS":
        raise RuntimeError("CURRENT_REPOSITORY_LIFECYCLE_INVALID:" + repr(current["failures"]))
    fixture_regressions()
    print("CURRENT_REPOSITORY_LIFECYCLE=PASS")
    print("CLOSURE_DEFECT_LIFECYCLE_SELFTEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Regression tests for exact-head release preflight."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from release_preflight import validate


def run(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def commit_all(root: Path, message: str) -> str:
    run(root, "add", ".")
    run(root, "commit", "-m", message)
    return run(root, "rev-parse", "HEAD")


def governance_report(
    path: Path,
    head: str,
    *,
    requested_mode: str,
    effective_mode: str | None = None,
    result: str = "PASS",
) -> None:
    assert requested_mode in {"verify", "finalize"}
    effective = effective_mode or requested_mode
    assert effective in {"verify", "finalize"}
    write_json(
        path,
        {
            "schema_version": 2,
            "result": result,
            "expected_head": head,
            "requested_mode": requested_mode,
            "effective_mode": effective,
            "final_acceptance_authority": bool(requested_mode == "finalize" and effective == "finalize" and result == "PASS"),
        },
    )


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "repo"
        root.mkdir()
        run(root, "init")
        run(root, "config", "user.email", "fixture@example.invalid")
        run(root, "config", "user.name", "Fixture")

        acceptance_path = root / ".workflow" / "acceptance.json"
        roadmap_path = root / ".workflow" / "roadmap.json"
        acceptance = {
            "schema_version": 1,
            "requirements": [
                {"id": "SW2-09-R1", "status": "PASS"},
                {"id": "SW2-09-R2", "status": "PASS"},
                {"id": "SW2-09-R3", "status": "PASS"},
                {"id": "SW2-09-R4", "status": "NOT_PROVEN"},
            ],
            "truth_gates": {
                "SOURCE_TESTS": "PASS",
                "RELEASE_EVIDENCE": "NOT_PROVEN",
                "RUNTIME_E2E": "NOT_APPLICABLE",
            },
        }
        write_json(acceptance_path, acceptance)
        write_json(
            roadmap_path,
            {
                "schema_version": 1,
                "current_phase": "SW2-07",
                "phases": [
                    {"id": "SW2-07", "status": "CURRENT"},
                    {"id": "SW2-08", "status": "COMPLETE"},
                    {"id": "SW2-09", "status": "PLANNED"},
                ],
            },
        )
        (root / "README.md").write_text("fixture\n", encoding="utf-8", newline="\n")
        head = commit_all(root, "fixture evidence candidate")
        report_path = Path(temp) / "governance.json"

        governance_report(
            report_path,
            head,
            requested_mode="verify",
            effective_mode="finalize",
        )
        evidence = validate(
            root,
            expected_head=head,
            version="v2.0.0-rc.1",
            governance_report=report_path,
            evidence_only=True,
        )
        assert evidence["result"] == "PASS"
        assert evidence["publication_authority"] is False
        assert evidence["evidence_only"] is True
        assert evidence["governance_requested_mode"] == "verify"
        assert evidence["governance_effective_mode"] == "finalize"

        strict_incomplete = validate(
            root,
            expected_head=head,
            version="v2.0.0-rc.1",
            governance_report=report_path,
        )
        assert strict_incomplete["result"] == "FAIL"
        assert "FINALIZE_REPORT_WRONG_REQUESTED_MODE" in strict_incomplete["failures"]
        assert "RELEASE_REQUIREMENT_NOT_PASS:SW2-09-R4" in strict_incomplete["failures"]
        assert "RELEASE_TRUTH_GATE_NOT_PROVEN:RELEASE_EVIDENCE:NOT_PROVEN" in strict_incomplete["failures"]
        assert strict_incomplete["publication_authority"] is False

        evidence_stable = validate(
            root,
            expected_head=head,
            version="v2.0.0",
            governance_report=report_path,
            evidence_only=True,
        )
        assert evidence_stable["result"] == "FAIL"
        assert "EVIDENCE_MODE_REQUIRES_PRERELEASE" in evidence_stable["failures"]
        assert evidence_stable["publication_authority"] is False

        governance_report(report_path, head, requested_mode="finalize")
        wrong_requested = validate(
            root,
            expected_head=head,
            version="v2.0.0-rc.2",
            governance_report=report_path,
            evidence_only=True,
        )
        assert wrong_requested["result"] == "FAIL"
        assert "EVIDENCE_REPORT_WRONG_REQUESTED_MODE" in wrong_requested["failures"]

        acceptance["requirements"][3]["status"] = "FAIL"
        write_json(acceptance_path, acceptance)
        failed_head = commit_all(root, "fixture explicit failure")
        governance_report(report_path, failed_head, requested_mode="verify", effective_mode="finalize")
        evidence_failure = validate(
            root,
            expected_head=failed_head,
            version="v2.0.0-rc.3",
            governance_report=report_path,
            evidence_only=True,
        )
        assert evidence_failure["result"] == "FAIL"
        assert "EVIDENCE_REQUIREMENT_FAIL:SW2-09-R4" in evidence_failure["failures"]
        assert evidence_failure["publication_authority"] is False

        acceptance["requirements"][3]["status"] = "PASS"
        acceptance["truth_gates"]["RELEASE_EVIDENCE"] = "PASS"
        write_json(acceptance_path, acceptance)
        strict_head = commit_all(root, "fixture strict prerelease")
        governance_report(report_path, strict_head, requested_mode="finalize")

        prerelease = validate(
            root,
            expected_head=strict_head,
            version="v2.0.0-rc.4",
            governance_report=report_path,
        )
        assert prerelease["result"] == "PASS"
        assert prerelease["publication_authority"] is True
        assert prerelease["pending_publication_requirements"] == []

        stable_too_early = validate(
            root,
            expected_head=strict_head,
            version="v2.0.0",
            governance_report=report_path,
        )
        assert stable_too_early["result"] == "FAIL"
        assert "STABLE_RELEASE_OUTSIDE_AUTHORIZED_PHASE:SW2-07" in stable_too_early["failures"]
        assert stable_too_early["publication_authority"] is False

        acceptance["requirements"][3]["status"] = "NOT_PROVEN"
        write_json(acceptance_path, acceptance)
        write_json(
            roadmap_path,
            {
                "schema_version": 1,
                "current_phase": "SW2-09",
                "phases": [
                    {"id": "SW2-07", "status": "COMPLETE"},
                    {"id": "SW2-08", "status": "COMPLETE"},
                    {"id": "SW2-09", "status": "CURRENT"},
                ],
            },
        )
        stable_head = commit_all(root, "fixture stable publication pending")
        governance_report(report_path, stable_head, requested_mode="finalize")
        stable = validate(
            root,
            expected_head=stable_head,
            version="v2.0.0",
            governance_report=report_path,
        )
        assert stable["result"] == "PASS"
        assert stable["publication_authority"] is True
        assert stable["pending_publication_requirements"] == ["SW2-09-R4"]

        acceptance["requirements"][2]["status"] = "NOT_PROVEN"
        write_json(acceptance_path, acceptance)
        non_publication_gap_head = commit_all(root, "fixture non-publication gap")
        governance_report(report_path, non_publication_gap_head, requested_mode="finalize")
        non_publication_gap = validate(
            root,
            expected_head=non_publication_gap_head,
            version="v2.0.0",
            governance_report=report_path,
        )
        assert non_publication_gap["result"] == "FAIL"
        assert "RELEASE_REQUIREMENT_NOT_PASS:SW2-09-R3" in non_publication_gap["failures"]
        assert non_publication_gap["publication_authority"] is False
        acceptance["requirements"][2]["status"] = "PASS"

        acceptance["requirements"] = acceptance["requirements"][:3]
        write_json(acceptance_path, acceptance)
        missing_r4_head = commit_all(root, "fixture missing publication requirement")
        governance_report(report_path, missing_r4_head, requested_mode="finalize")
        missing_r4 = validate(
            root,
            expected_head=missing_r4_head,
            version="v2.0.0",
            governance_report=report_path,
        )
        assert missing_r4["result"] == "FAIL"
        assert "STABLE_RELEASE_REQUIREMENT_MISSING:SW2-09-R4" in missing_r4["failures"]
        assert missing_r4["publication_authority"] is False

        acceptance["requirements"].append({"id": "SW2-09-R4", "status": "NOT_PROVEN"})
        write_json(acceptance_path, acceptance)
        restored_head = commit_all(root, "fixture restore publication pending")
        governance_report(report_path, restored_head, requested_mode="finalize")

        (root / "DIRTY.txt").write_text("dirty\n", encoding="utf-8", newline="\n")
        dirty = validate(
            root,
            expected_head=restored_head,
            version="v2.0.0",
            governance_report=report_path,
        )
        assert dirty["result"] == "FAIL"
        assert "RELEASE_WORKTREE_NOT_CLEAN" in dirty["failures"]
        assert dirty["publication_authority"] is False
        (root / "DIRTY.txt").unlink()

        governance_report(report_path, restored_head, requested_mode="finalize", result="FAIL")
        failed_finalize = validate(
            root,
            expected_head=restored_head,
            version="v2.0.0",
            governance_report=report_path,
        )
        assert failed_finalize["result"] == "FAIL"
        assert "FINALIZE_REPORT_NOT_PASS" in failed_finalize["failures"]
        assert failed_finalize["publication_authority"] is False

        acceptance = {
            "schema_version": 1,
            "requirements": [
                {"id": "SW2-19-R1", "status": "PASS"},
                {"id": "SW2-19-R2", "status": "PASS"},
                {"id": "SW2-19-R3", "status": "PASS"},
                {"id": "SW2-19-R4", "status": "NOT_PROVEN"},
            ],
            "truth_gates": {
                "SOURCE_TESTS": "PASS",
                "PROJECT_STATE_SYNC": "PASS",
                "RUNTIME_E2E": "NOT_APPLICABLE",
            },
        }
        write_json(acceptance_path, acceptance)
        write_json(
            roadmap_path,
            {
                "schema_version": 1,
                "current_phase": "SW2-19",
                "phases": [
                    {"id": "SW2-18", "status": "COMPLETE"},
                    {"id": "SW2-19", "status": "CURRENT"},
                ],
            },
        )
        v21_head = commit_all(root, "fixture V2.1 stable publication pending")
        governance_report(report_path, v21_head, requested_mode="finalize")
        v21 = validate(
            root,
            expected_head=v21_head,
            version="v2.1.0",
            governance_report=report_path,
        )
        assert v21["result"] == "PASS"
        assert v21["publication_authority"] is True
        assert v21["pending_publication_requirements"] == ["SW2-19-R4"]

        acceptance["requirements"][2]["status"] = "NOT_PROVEN"
        write_json(acceptance_path, acceptance)
        v21_gap_head = commit_all(root, "fixture V2.1 non-publication gap")
        governance_report(report_path, v21_gap_head, requested_mode="finalize")
        v21_gap = validate(
            root,
            expected_head=v21_gap_head,
            version="v2.1.0",
            governance_report=report_path,
        )
        assert v21_gap["result"] == "FAIL"
        assert "RELEASE_REQUIREMENT_NOT_PASS:SW2-19-R3" in v21_gap["failures"]
        assert v21_gap["publication_authority"] is False

        acceptance["requirements"][2]["status"] = "PASS"
        acceptance["requirements"] = acceptance["requirements"][:3]
        write_json(acceptance_path, acceptance)
        v21_missing_r4_head = commit_all(root, "fixture V2.1 missing publication requirement")
        governance_report(report_path, v21_missing_r4_head, requested_mode="finalize")
        v21_missing_r4 = validate(
            root,
            expected_head=v21_missing_r4_head,
            version="v2.1.0",
            governance_report=report_path,
        )
        assert v21_missing_r4["result"] == "FAIL"
        assert "STABLE_RELEASE_REQUIREMENT_MISSING:SW2-19-R4" in v21_missing_r4["failures"]
        assert v21_missing_r4["publication_authority"] is False

    print("EVIDENCE_ONLY_PRERELEASE=PASS")
    print("EVIDENCE_VERIFY_ESCALATION_NONAUTH=PASS")
    print("EVIDENCE_ONLY_NO_PUBLICATION_AUTHORITY=PASS")
    print("EVIDENCE_ONLY_STABLE_REJECTION=PASS")
    print("EVIDENCE_WRONG_REQUESTED_MODE_REJECTION=PASS")
    print("EVIDENCE_ONLY_EXPLICIT_FAILURE_REJECTION=PASS")
    print("STRICT_INCOMPLETE_REJECTION=PASS")
    print("STRICT_PRERELEASE_PREFLIGHT=PASS")
    print("STABLE_PHASE_BOUNDARY_REJECTION=PASS")
    print("STABLE_PUBLICATION_PENDING_PREFLIGHT=PASS")
    print("NON_PUBLICATION_REQUIREMENT_REJECTION=PASS")
    print("MISSING_PUBLICATION_REQUIREMENT_REJECTION=PASS")
    print("V21_STABLE_PUBLICATION_PENDING_PREFLIGHT=PASS")
    print("V21_NON_PUBLICATION_REQUIREMENT_REJECTION=PASS")
    print("V21_MISSING_PUBLICATION_REQUIREMENT_REJECTION=PASS")
    print("DIRTY_WORKTREE_REJECTION=PASS")
    print("FAILED_FINALIZE_REJECTION=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

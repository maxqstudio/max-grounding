#!/usr/bin/env python3
"""Read-only release preflight bound to an exact governed candidate."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

VERSION_RE = re.compile(r"^v(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")
STABLE_RELEASE_CONTRACTS = {
    "SW2-09": {
        "requirements": {
            "SW2-09-R1",
            "SW2-09-R2",
            "SW2-09-R3",
            "SW2-09-R4",
        },
        "publication_requirement": "SW2-09-R4",
    },
    "SW2-19": {
        "requirements": {
            "SW2-19-R1",
            "SW2-19-R2",
            "SW2-19-R3",
            "SW2-19-R4",
        },
        "publication_requirement": "SW2-19-R4",
    },
}


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT).strip()


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate(
    root: Path,
    *,
    expected_head: str,
    version: str,
    governance_report: Path,
    evidence_only: bool = False,
) -> dict[str, object]:
    failures: list[str] = []
    stable = "-" not in version

    if not VERSION_RE.fullmatch(version):
        failures.append("INVALID_RELEASE_VERSION")
    if evidence_only and stable:
        failures.append("EVIDENCE_MODE_REQUIRES_PRERELEASE")

    try:
        observed_head = git(root, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        observed_head = "UNAVAILABLE"
        failures.append("GIT_HEAD_UNAVAILABLE")
    if observed_head != expected_head:
        failures.append(f"RELEASE_HEAD_MISMATCH:observed={observed_head}:expected={expected_head}")

    try:
        dirty = git(root, "status", "--porcelain", "--untracked-files=all")
    except (OSError, subprocess.CalledProcessError):
        dirty = "UNKNOWN"
        failures.append("GIT_STATUS_UNAVAILABLE")
    if dirty:
        failures.append("RELEASE_WORKTREE_NOT_CLEAN")

    if version and observed_head != "UNAVAILABLE":
        try:
            if git(root, "tag", "--list", version):
                failures.append("RELEASE_TAG_ALREADY_EXISTS:" + version)
        except (OSError, subprocess.CalledProcessError):
            failures.append("GIT_TAG_QUERY_FAILED")

    report_label = "VERIFY" if evidence_only else "FINALIZE"
    if not governance_report.is_file():
        governance = {}
        failures.append(report_label + "_REPORT_MISSING")
    else:
        try:
            governance = _load(governance_report)
        except (OSError, ValueError, TypeError):
            governance = {}
            failures.append(report_label + "_REPORT_INVALID")

    requested_mode = governance.get("requested_mode") if governance else None
    effective_mode = governance.get("effective_mode") if governance else None
    if governance:
        if governance.get("result") != "PASS":
            failures.append(report_label + "_REPORT_NOT_PASS")
        report_head = governance.get("expected_head")
        if report_head != expected_head:
            failures.append(f"{report_label}_REPORT_HEAD_MISMATCH:{report_head}")
        if evidence_only:
            if requested_mode != "verify":
                failures.append("EVIDENCE_REPORT_WRONG_REQUESTED_MODE")
            if effective_mode not in {"verify", "finalize"}:
                failures.append("EVIDENCE_REPORT_INVALID_EFFECTIVE_MODE")
            if governance.get("final_acceptance_authority") is not False:
                failures.append("EVIDENCE_REPORT_MUST_NOT_BE_AUTHORITY")
        else:
            if requested_mode != "finalize":
                failures.append("FINALIZE_REPORT_WRONG_REQUESTED_MODE")
            if effective_mode != "finalize":
                failures.append("FINALIZE_REPORT_WRONG_EFFECTIVE_MODE")
            if governance.get("final_acceptance_authority") is not True:
                failures.append("FINALIZE_REPORT_NOT_AUTHORITY")

    acceptance_path = root / ".workflow" / "acceptance.json"
    roadmap_path = root / ".workflow" / "roadmap.json"
    if not acceptance_path.is_file():
        acceptance = {}
        failures.append("ACCEPTANCE_MISSING")
    else:
        acceptance = _load(acceptance_path)

    current_phase = "UNKNOWN"
    phases: list[object] = []
    if not roadmap_path.is_file():
        failures.append("ROADMAP_MISSING")
    else:
        roadmap = _load(roadmap_path)
        current_phase = str(roadmap.get("current_phase", "UNKNOWN"))
        raw_phases = roadmap.get("phases", [])
        if isinstance(raw_phases, list):
            phases = raw_phases

    stable_contract = STABLE_RELEASE_CONTRACTS.get(current_phase) if stable else None
    publication_requirement = (
        str(stable_contract["publication_requirement"])
        if stable_contract is not None
        else ""
    )

    requirements = acceptance.get("requirements", []) if isinstance(acceptance, dict) else []
    if not isinstance(requirements, list):
        failures.append("ACCEPTANCE_REQUIREMENTS_INVALID")
        requirements = []
    requirement_ids: set[str] = set()
    pending_publication_requirements: list[str] = []
    for item in requirements:
        if not isinstance(item, dict):
            failures.append("ACCEPTANCE_REQUIREMENT_INVALID")
            continue
        ident = str(item.get("id", "UNKNOWN"))
        requirement_ids.add(ident)
        status = item.get("status")
        if evidence_only:
            if status == "FAIL":
                failures.append("EVIDENCE_REQUIREMENT_FAIL:" + ident)
            elif status not in {"PASS", "NOT_PROVEN", "NOT_APPLICABLE"}:
                failures.append(f"EVIDENCE_REQUIREMENT_INVALID_STATUS:{ident}:{status}")
        elif status != "PASS":
            publication_pending = bool(
                stable
                and stable_contract is not None
                and ident == publication_requirement
                and status == "NOT_PROVEN"
            )
            if publication_pending:
                pending_publication_requirements.append(ident)
            else:
                failures.append("RELEASE_REQUIREMENT_NOT_PASS:" + ident)

    gates = acceptance.get("truth_gates", {}) if isinstance(acceptance, dict) else {}
    if not isinstance(gates, dict):
        failures.append("TRUTH_GATES_INVALID")
        gates = {}
    for name, status in sorted(gates.items()):
        if evidence_only:
            if status == "FAIL":
                failures.append(f"EVIDENCE_TRUTH_GATE_FAIL:{name}")
            elif status not in {"PASS", "NOT_PROVEN", "NOT_APPLICABLE"}:
                failures.append(f"EVIDENCE_TRUTH_GATE_INVALID_STATUS:{name}:{status}")
        elif status not in {"PASS", "NOT_APPLICABLE"}:
            failures.append(f"RELEASE_TRUTH_GATE_NOT_PROVEN:{name}:{status}")

    if stable:
        if stable_contract is None:
            failures.append("STABLE_RELEASE_OUTSIDE_AUTHORIZED_PHASE:" + current_phase)
        else:
            required_ids = stable_contract["requirements"]
            assert isinstance(required_ids, set)
            for ident in sorted(required_ids - requirement_ids):
                failures.append("STABLE_RELEASE_REQUIREMENT_MISSING:" + ident)
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            ident = str(phase.get("id", ""))
            if ident.startswith("SW2-") and ident != current_phase and phase.get("status") != "COMPLETE":
                failures.append("PRIOR_PHASE_NOT_COMPLETE:" + ident)

    passed = not failures
    publication_authority = bool(passed and not evidence_only)
    return {
        "schema_version": 1,
        "result": "PASS" if passed else "FAIL",
        "expected_head": expected_head,
        "observed_head": observed_head,
        "version": version,
        "stable": stable,
        "evidence_only": evidence_only,
        "publication_authority": publication_authority,
        "pending_publication_requirements": pending_publication_requirements,
        "governance_requested_mode": requested_mode,
        "governance_effective_mode": effective_mode,
        "current_phase": current_phase,
        "governance_report": str(governance_report),
        "failures": failures,
        "evidence_boundary": (
            "Evidence-only mode is a non-authoritative dry run: it requires a verify request, allows governance breadth to escalate fail-closed, proves exact-head, clean-state, version/tag, report, and status semantics while allowing explicit NOT_PROVEN items, can never authorize publication, and rejects stable versions. "
            "Strict mode requires a finalize request, effective finalize execution, complete PASS governance, and an explicitly authorized stable-release phase contract. During a stable preflight only the active phase publication requirement may remain NOT_PROVEN because tag/release publication is the action being authorized; every other release requirement and truth gate must already be PASS or NOT_APPLICABLE. Neither mode creates a Git tag or GitHub release."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--governance-report", required=True)
    parser.add_argument("--evidence-only", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    governance_report = Path(args.governance_report)
    if not governance_report.is_absolute():
        governance_report = root / governance_report
    report = validate(
        root,
        expected_head=args.expected_head,
        version=args.version,
        governance_report=governance_report,
        evidence_only=args.evidence_only,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.report:
        output = Path(args.report)
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

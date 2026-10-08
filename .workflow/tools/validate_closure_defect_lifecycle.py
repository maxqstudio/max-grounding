#!/usr/bin/env python3
"""Validate accepted-closure semantics and known-defect lifecycle authority."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ALLOWED_DEFECT_STATUSES = {"OPEN", "FIXED/ACCEPTED", "HISTORICAL", "NOT_PROVEN"}
RESOLVED_DEFECT_STATUSES = {"FIXED/ACCEPTED", "HISTORICAL"}
STALE_CLOSURE_PATTERNS = (
    re.compile(r"(?i)\bPR\s*#\d+\b"),
    re.compile(r"(?i)\bpull\s+request\s*#?\d+\b"),
    re.compile(r"(?i)\bmark\b.*\b(?:PR|pull\s+request)\b.*\bready\b"),
    re.compile(r"(?i)\bsquash[- ]merge\b"),
    re.compile(r"(?i)\bwork/[A-Za-z0-9._/-]+"),
)


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("TOP_LEVEL_OBJECT_REQUIRED:" + path.as_posix())
    return data


def git_root(start: Path) -> Path:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(value).resolve()
    except Exception:
        return start.resolve()


def text(value: object) -> str:
    return str(value or "").strip()


def accepted_state(status: str) -> bool:
    upper = status.upper()
    return "ACCEPTED" in upper or "RELEASED" in upper


def validate(root: Path, *, expected_base: str = "") -> dict:
    root = root.resolve()
    failures: list[str] = []

    required = {
        "state": root / ".workflow/state.json",
        "roadmap": root / ".workflow/roadmap.json",
        "acceptance": root / ".workflow/acceptance.json",
        "known_defects": root / ".workflow/known_defects.json",
    }
    loaded: dict[str, dict] = {}
    for key, path in required.items():
        if not path.is_file():
            failures.append("MISSING_AUTHORITY:" + path.relative_to(root).as_posix())
            continue
        try:
            loaded[key] = load_json(path)
        except Exception as exc:
            failures.append("INVALID_AUTHORITY:" + key + ":" + type(exc).__name__)

    if failures:
        return {"schema_version": 1, "result": "FAIL", "failures": failures}

    state = loaded["state"]
    roadmap = loaded["roadmap"]
    acceptance = loaded["acceptance"]
    known_defects = loaded["known_defects"]

    current_phase = text(roadmap.get("current_phase"))
    state_phase = text(state.get("phase"))
    if not current_phase:
        failures.append("ROADMAP_CURRENT_PHASE_MISSING")
    if current_phase != state_phase:
        failures.append(f"STATE_PHASE_MISMATCH:{state_phase}!={current_phase}")

    phases = roadmap.get("phases", [])
    phase_status: dict[str, str] = {}
    if not isinstance(phases, list):
        failures.append("ROADMAP_PHASES_INVALID")
        phases = []
    for item in phases:
        if not isinstance(item, dict):
            continue
        pid = text(item.get("id"))
        if pid:
            phase_status[pid] = text(item.get("status")).upper()

    last_accepted_branch = text(state.get("last_accepted_branch"))
    last_accepted_sha = text(state.get("last_accepted_sha"))
    if last_accepted_branch != "main":
        failures.append("LAST_ACCEPTED_BRANCH_NOT_MAIN:" + last_accepted_branch)
    if not last_accepted_sha:
        failures.append("LAST_ACCEPTED_SHA_MISSING")
    sequence_session = text(acceptance.get("sequence_session"))
    implementation_base = ""
    if not sequence_session:
        failures.append("SEQUENCE_SESSION_MISSING")
    else:
        session_path = root / "docs/sequence/sessions" / f"{sequence_session}.json"
        if not session_path.is_file():
            failures.append("SEQUENCE_SESSION_FILE_MISSING:" + sequence_session)
        else:
            try:
                session = load_json(session_path)
            except Exception as exc:
                failures.append("SEQUENCE_SESSION_INVALID:" + type(exc).__name__)
            else:
                implementation_base = text(session.get("implementation_base_sha"))
                if not implementation_base:
                    failures.append("IMPLEMENTATION_BASE_SHA_MISSING:" + sequence_session)
                elif (
                    not accepted_state(text(state.get("status")))
                    and last_accepted_sha
                    and implementation_base != last_accepted_sha
                ):
                    failures.append(
                        f"IMPLEMENTATION_BASE_MISMATCH:{implementation_base}!={last_accepted_sha}"
                    )

    if expected_base and implementation_base != expected_base:
        failures.append(
            f"IMPLEMENTATION_BASE_EXPECTED_MISMATCH:{implementation_base}!={expected_base}"
        )

    state_status = text(state.get("status"))
    next_actions = state.get("next_authorized_actions", [])
    if not isinstance(next_actions, list):
        failures.append("NEXT_AUTHORIZED_ACTIONS_INVALID")
        next_actions = []
    if accepted_state(state_status):
        if text(state.get("working_branch")) != "main":
            failures.append("ACCEPTED_STATE_WORKING_BRANCH_NOT_MAIN")
        for index, action in enumerate(next_actions):
            action_text = text(action)
            for pattern in STALE_CLOSURE_PATTERNS:
                if pattern.search(action_text):
                    failures.append(
                        f"STALE_ACCEPTED_CLOSURE_ACTION:{index + 1}:{action_text}"
                    )
                    break

    defects = known_defects.get("defects", [])
    if not isinstance(defects, list):
        failures.append("KNOWN_DEFECTS_INVALID")
        defects = []
    seen: set[str] = set()
    status_counts = {status: 0 for status in sorted(ALLOWED_DEFECT_STATUSES)}
    for index, item in enumerate(defects, start=1):
        if not isinstance(item, dict):
            failures.append(f"DEFECT_INVALID:{index}")
            continue
        defect_id = text(item.get("id"))
        status = text(item.get("status")).upper()
        evidence = text(item.get("evidence"))
        if not defect_id:
            failures.append(f"DEFECT_ID_MISSING:{index}")
            continue
        if defect_id in seen:
            failures.append("DEFECT_ID_DUPLICATE:" + defect_id)
        seen.add(defect_id)
        if status not in ALLOWED_DEFECT_STATUSES:
            failures.append(f"DEFECT_STATUS_INVALID:{defect_id}:{status}")
            continue
        status_counts[status] += 1
        if not evidence:
            failures.append("DEFECT_EVIDENCE_MISSING:" + defect_id)
        resolution_phase = text(item.get("resolution_phase"))
        resolution_evidence = text(item.get("resolution_evidence"))
        if status in RESOLVED_DEFECT_STATUSES:
            if not resolution_phase:
                failures.append("DEFECT_RESOLUTION_PHASE_MISSING:" + defect_id)
            elif resolution_phase not in phase_status:
                failures.append(
                    f"DEFECT_RESOLUTION_PHASE_UNKNOWN:{defect_id}:{resolution_phase}"
                )
            elif phase_status[resolution_phase] != "COMPLETE":
                failures.append(
                    f"DEFECT_RESOLUTION_PHASE_NOT_COMPLETE:{defect_id}:{resolution_phase}"
                )
            if not resolution_evidence:
                failures.append("DEFECT_RESOLUTION_EVIDENCE_MISSING:" + defect_id)
        else:
            if resolution_phase or resolution_evidence:
                failures.append("OPEN_DEFECT_HAS_RESOLUTION_METADATA:" + defect_id)

    report = {
        "schema_version": 1,
        "result": "FAIL" if failures else "PASS",
        "failures": failures,
        "current_phase": current_phase,
        "state_status": state_status,
        "last_accepted_branch": last_accepted_branch,
        "last_accepted_sha": last_accepted_sha,
        "implementation_base_sha": implementation_base,
        "defects_checked": len(defects),
        "defect_status_counts": status_counts,
        "evidence_boundary": (
            "This gate validates closure-state durability, accepted-main implementation-base binding, "
            "and known-defect lifecycle metadata. It does not infer that an unresolved defect is fixed, "
            "does not change historical acceptance evidence, and does not claim platform merge enforcement."
        ),
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--expected-base", default="")
    args = parser.parse_args()
    root = git_root(Path(args.root))
    report = validate(root, expected_base=args.expected_base.strip())
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

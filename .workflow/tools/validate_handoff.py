#!/usr/bin/env python3
"""Profile-aware structural handoff validator for Skill Workflow projects.

A PASS proves structural handoff discipline only. It does not prove semantic
correctness of project documentation or runtime behavior.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from project_profile import (
    CONTRACT_DOCS,
    PROFILE_FILE,
    contract_settings,
    documentation_settings,
    normalized_profile,
    parse_profile,
    required_docs,
    sequence_settings,
    validate_profile,
)

PLACEHOLDER_PATTERNS = (
    re.compile(r"<[^>]+>"),
    re.compile(r"\bTODO\b", re.IGNORECASE),
    re.compile(r"\bTBD\b", re.IGNORECASE),
)


def git_root(start: Path) -> Path:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(value)
    except Exception:
        return start.resolve()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def has_placeholder(text: str) -> bool:
    # Generated provenance markers and the narrow HTML presentation vocabulary
    # used by deterministic generated indexes are not template placeholders.
    visible = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    visible = re.sub(
        r"</?(?:details|summary|code)>",
        "",
        visible,
        flags=re.IGNORECASE,
    )
    return any(pattern.search(visible) for pattern in PLACEHOLDER_PATTERNS)


def validate_symbol_index_shape(text: str) -> list[str]:
    """Accept legacy or V2 presentation while failing malformed V2 structure."""
    if "| File | Symbol |" in text:
        return []

    v2_markers = (
        "## File summary",
        "| File | Symbols | Classes | Functions | Methods |",
        "## Detailed symbols",
        "| Symbol | Kind | Lines@SHA |",
    )
    present = [marker in text for marker in v2_markers]
    if not any(present):
        return ["SYMBOL_INDEX_TABLE_MISSING"]

    failures = [
        "SYMBOL_INDEX_V2_STRUCTURE_MISSING:" + marker
        for marker, found in zip(v2_markers, present)
        if not found
    ]
    opened = len(re.findall(r"<details>", text, flags=re.IGNORECASE))
    closed = len(re.findall(r"</details>", text, flags=re.IGNORECASE))
    summaries = len(re.findall(r"<summary>", text, flags=re.IGNORECASE))
    summary_closed = len(re.findall(r"</summary>", text, flags=re.IGNORECASE))
    if opened != closed or opened != summaries or summaries != summary_closed:
        failures.append(
            "SYMBOL_INDEX_V2_DETAILS_UNBALANCED:"
            + f"open={opened},close={closed},summary={summaries},summary_close={summary_closed}"
        )
    if opened == 0 and "_No Python symbols observed._" not in text:
        failures.append("SYMBOL_INDEX_V2_DETAILS_MISSING")
    return failures


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--allow-placeholders", action="store_true")
    args = ap.parse_args()

    root = git_root(Path(args.root).resolve())
    failures: list[str] = []
    warnings: list[str] = []

    profile_path = root / PROFILE_FILE
    if not profile_path.is_file():
        print(f"HANDOFF_ROOT={root}")
        print(f"FAIL MISSING_REQUIRED_DOC:{PROFILE_FILE}")
        print("RESULT=FAIL failures=1 warnings=0")
        return 1

    try:
        profile_data = parse_profile(profile_path)
        profile_name = normalized_profile(profile_data)
        failures.extend(validate_profile(profile_data))
        required = required_docs(profile_data)
        settings = contract_settings(profile_data)
        documentation_policy = documentation_settings(profile_data)
        sequence_policy = sequence_settings(profile_data)
    except Exception as exc:
        print(f"HANDOFF_ROOT={root}")
        print(f"FAIL PROJECT_PROFILE_INVALID:{exc}")
        print("RESULT=FAIL failures=1 warnings=0")
        return 1

    print(f"HANDOFF_ROOT={root}")
    print(f"GOVERNANCE_PROFILE={profile_name}")

    generated_mode = bool(documentation_policy.get("generated", False))
    docs_root = root / str(documentation_policy.get("docs_root", "docs"))
    spec_root = root / str(documentation_policy.get("spec_root", ".workflow"))
    if not spec_root.is_dir():
        failures.append("PROJECT_TRUTH_SPEC_ROOT_MISSING:" + str(spec_root))

    roadmap_spec = spec_root / "roadmap.json"
    state_spec = spec_root / "state.json"
    if not roadmap_spec.is_file():
        failures.append("ROADMAP_SPEC_MISSING:" + str(roadmap_spec))
    if not state_spec.is_file():
        failures.append("STATE_SPEC_MISSING:" + str(state_spec))

    if roadmap_spec.is_file() and state_spec.is_file():
        try:
            roadmap_data = json.loads(roadmap_spec.read_text(encoding="utf-8"))
            state_data = json.loads(state_spec.read_text(encoding="utf-8"))
            roadmap_phase = str(roadmap_data.get("current_phase", "")).strip()
            state_phase = str(state_data.get("phase", "")).strip()
            if not roadmap_phase or roadmap_phase == "replace-me":
                failures.append("ROADMAP_CURRENT_PHASE_MISSING")
            if not state_phase or state_phase == "replace-me":
                failures.append("STATE_CURRENT_PHASE_MISSING")
            if roadmap_phase and state_phase and roadmap_phase != state_phase:
                failures.append(
                    "ROADMAP_STATE_PHASE_MISMATCH:"
                    + roadmap_phase
                    + "!="
                    + state_phase
                )

            phases = roadmap_data.get("phases", [])
            current_ids = [
                str(item.get("id", "")).strip()
                for item in phases
                if isinstance(item, dict)
                and str(item.get("status", "")).strip().upper() == "CURRENT"
            ]
            if len(current_ids) != 1:
                failures.append(
                    "ROADMAP_CURRENT_MARKER_COUNT:" + str(len(current_ids))
                )
            elif roadmap_phase and current_ids[0] != roadmap_phase:
                failures.append(
                    "ROADMAP_CURRENT_MARKER_MISMATCH:"
                    + current_ids[0]
                    + "!="
                    + roadmap_phase
                )
        except Exception as exc:
            failures.append("ROADMAP_SPEC_INVALID:" + str(exc))

    for rel in sorted(required):
        path = root / rel if rel == PROFILE_FILE else docs_root / rel
        if not path.is_file():
            failures.append(f"MISSING_REQUIRED_DOC:{rel}")
            continue

        text = read(path)
        if not text.strip():
            failures.append(f"EMPTY_REQUIRED_DOC:{rel}")
            continue

        if rel in {"SYMBOL_INDEX.md", "FLOW_INDEX.md"}:
            if re.search(r"^Status\s*:\s*STALE\s*$", text, re.MULTILINE | re.IGNORECASE):
                failures.append(f"STALE_INDEX:{rel}")

        if rel.endswith(".md") and generated_mode:
            if "GENERATED BY PROJECT TRUTH COMPILER - DO NOT EDIT" not in text:
                failures.append(f"GENERATED_DOC_MARKER_MISSING:{rel}")

        if rel.endswith(".md") and not args.allow_placeholders and has_placeholder(text):
            warnings.append(f"PLACEHOLDER_TOKEN_PRESENT:{rel}")

    if generated_mode:
        for rel in sorted(required):
            if rel != PROFILE_FILE and (root / rel).is_file():
                failures.append("DUPLICATE_CANONICAL_DOC_AT_REPO_ROOT:" + rel)

    profile_text = read(profile_path)
    if "replace-me" in profile_text.lower():
        failures.append("PROJECT_PROFILE_REASON_NOT_SET")

    for key, value in settings.items():
        contract_doc = CONTRACT_DOCS[key]
        if value == "not_applicable" and (docs_root / contract_doc).is_file():
            failures.append(f"NOT_APPLICABLE_DOC_PRESENT:{contract_doc}")

    current = docs_root / "CURRENT_STATE.md"
    if current.is_file():
        text = read(current)
        required_current_fields = [
            "Authoritative SHA:",
            "Status:",
            "Roadmap phase:",
            "ROADMAP_SYNC:",
            "Next authorized action",
            "Governance profile:",
        ]
        if sequence_policy.get("required", False):
            required_current_fields.extend([
                "Sequence policy:",
                "Current sequence mode:",
                "SEQUENCE_SYNC:",
            ])
        for field in required_current_fields:
            if field not in text:
                failures.append(f"CURRENT_STATE_FIELD_MISSING:{field}")
        match = re.search(r"^Governance profile:\s*(.*?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        if not match or not match.group(1).strip():
            failures.append("CURRENT_STATE_GOVERNANCE_PROFILE_MISSING")
        elif match.group(1).strip().lower() != profile_name:
            failures.append(
                "CURRENT_STATE_PROFILE_CONFLICT:"
                + match.group(1).strip()
                + "!="
                + profile_name
            )

    roadmap = docs_root / "ROADMAP.md"
    if roadmap.is_file():
        text = read(roadmap)
        state_match = re.search(
            r"^Current project phase:\s*(.*?)\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        roadmap_match = re.search(
            r"^Current roadmap phase:\s*(.*?)\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        sync_match = re.search(
            r"^ROADMAP_SYNC:\s*(.*?)\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if not state_match or not state_match.group(1).strip():
            failures.append("ROADMAP_PROJECT_PHASE_MISSING")
        if not roadmap_match or not roadmap_match.group(1).strip():
            failures.append("ROADMAP_CURRENT_PHASE_MISSING")
        if (
            state_match
            and roadmap_match
            and state_match.group(1).strip() != roadmap_match.group(1).strip()
        ):
            failures.append(
                "ROADMAP_STATE_PHASE_CONFLICT:"
                + roadmap_match.group(1).strip()
                + "!="
                + state_match.group(1).strip()
            )
        if generated_mode and (
            not sync_match or sync_match.group(1).strip().upper() != "PASS"
        ):
            failures.append(
                "ROADMAP_SYNC_NOT_PASS:"
                + (sync_match.group(1).strip() if sync_match else "MISSING")
            )

    manifest = docs_root / "PROJECT_MANIFEST.md"
    if manifest.is_file():
        text = read(manifest)
        match = re.search(r"^Governance profile:\s*(.*?)\s*$", text, re.MULTILINE | re.IGNORECASE)
        if not match or not match.group(1).strip():
            failures.append("PROJECT_MANIFEST_GOVERNANCE_PROFILE_MISSING")
        elif match.group(1).strip().lower() != profile_name:
            failures.append(
                "PROJECT_MANIFEST_PROFILE_CONFLICT:"
                + match.group(1).strip()
                + "!="
                + profile_name
            )

    overview = docs_root / "SYSTEM_OVERVIEW.md"
    if overview.is_file():
        text = read(overview)
        match = re.search(
            r"^Human comprehension status:\s*(.*?)\s*$",
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if not match or not match.group(1).strip():
            failures.append("HUMAN_COMPREHENSION_STATUS_MISSING")
        elif match.group(1).strip().upper() != "PASS":
            failures.append(
                "HUMAN_COMPREHENSION_GATE_NOT_PASS:"
                + match.group(1).strip()
            )
        for heading in (
            "## One-minute summary",
            "## System at a glance",
            "## Major components",
            "## Main data flow",
            "## Main user workflows",
            "## Lifecycle and state",
            "## Authority model",
            "## Mutable vs immutable",
            "## Failure and recovery",
            "## Current project state",
            "## Proven vs not proven",
            "## Human comprehension gate",
        ):
            if heading not in text:
                failures.append("SYSTEM_OVERVIEW_SECTION_MISSING:" + heading)

    authority = docs_root / "SOURCE_AUTHORITY_MAP.md"
    if authority.is_file() and "Canonical authority" not in read(authority):
        failures.append("SOURCE_AUTHORITY_MAP_STRUCTURE_INVALID")

    symbol = docs_root / "SYMBOL_INDEX.md"
    if symbol.is_file():
        text = read(symbol)
        if "Authority SHA:" not in text:
            failures.append("SYMBOL_INDEX_AUTHORITY_SHA_MISSING")
        failures.extend(validate_symbol_index_shape(text))

    flow = docs_root / "FLOW_INDEX.md"
    if flow.is_file():
        text = read(flow)
        if "Authority SHA:" not in text:
            failures.append("FLOW_INDEX_AUTHORITY_SHA_MISSING")
        if "Flow inventory" not in text and "| Flow |" not in text:
            failures.append("FLOW_INDEX_INVENTORY_MISSING")

    sequence_doc = docs_root / "SEQUENCE_CONTRACTS.md"
    if sequence_policy.get("required", False):
        if not sequence_doc.is_file():
            failures.append("MISSING_REQUIRED_DOC:SEQUENCE_CONTRACTS.md")
        else:
            text = read(sequence_doc)
            for heading in ("## Modes", "## Flow inventory", "## Mismatch handling"):
                if heading not in text:
                    failures.append("SEQUENCE_CONTRACTS_STRUCTURE_INVALID:" + heading)

        sessions_root = root / "docs" / "sequence" / "sessions"
        sessions = list(sessions_root.rglob("*.json")) if sessions_root.is_dir() else []
        if not sessions:
            failures.append("SEQUENCE_SESSION_CONTRACT_MISSING")

    matrix = docs_root / "TEST_ACCEPTANCE_MATRIX.md"
    if matrix.is_file():
        text = read(matrix)
        if "Evidence boundary" not in text:
            failures.append("TEST_ACCEPTANCE_EVIDENCE_BOUNDARY_MISSING")
        if "Final tested source" not in text:
            failures.append("TEST_ACCEPTANCE_TESTED_SOURCE_MISSING")
        if sequence_policy.get("required", False):
            if "## Sequence contract evidence" not in text:
                failures.append("TEST_ACCEPTANCE_SEQUENCE_SECTION_MISSING")
            if "SEQUENCE_SYNC:" not in text:
                failures.append("TEST_ACCEPTANCE_SEQUENCE_GATE_MISSING")

    truth = docs_root / "PROJECT_TRUTH_SYNC.md"
    if truth.is_file():
        text = read(truth)
        if "## Truth gates" not in text:
            failures.append("PROJECT_TRUTH_GATES_MISSING")
        if "## Critical claim traceability" not in text:
            failures.append("PROJECT_TRUTH_TRACEABILITY_MISSING")
        if "PROJECT_STATE_SYNC" not in text:
            failures.append("PROJECT_TRUTH_FINAL_GATE_MISSING")
        if sequence_policy.get("required", False) and "SEQUENCE_SYNC" not in text:
            failures.append("PROJECT_TRUTH_SEQUENCE_GATE_MISSING")
        if generated_mode and "PROJECT_DOCS_SYNC" not in text:
            failures.append("PROJECT_TRUTH_DOC_COMPILER_GATE_MISSING")

    for warning in warnings:
        print(f"WARN {warning}")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        print(f"RESULT=FAIL failures={len(failures)} warnings={len(warnings)}")
        return 1

    print(
        f"RESULT=PASS profile={profile_name} required_docs={len(required)} "
        f"failures=0 warnings={len(warnings)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

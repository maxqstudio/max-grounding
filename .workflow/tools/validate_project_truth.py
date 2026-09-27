#!/usr/bin/env python3
"""Machine-verifiable Project Truth Synchronization checks.

This validator checks provenance, truth-ledger structure, referenced files,
and simple path::symbol resolution. It cannot prove semantic correctness.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from project_profile import (
    PROFILE_FILE,
    documentation_settings,
    normalized_profile,
    parse_profile,
    required_docs,
    runtime_settings,
    sequence_settings,
    validate_profile,
)

REQUIRED_GATES = [
    "SOURCE_TESTS",
    "RUNTIME_E2E",
    "PROVENANCE_SYNC",
    "REFERENCE_SYNC",
    "STRUCTURAL_SYNC",
    "SEMANTIC_SYNC",
    "BEHAVIORAL_SYNC",
    "CROSS_DOCUMENT_CONSISTENCY",
    "HUMAN_COMPREHENSION",
    "SEQUENCE_SYNC",
    "DOC_LAYOUT",
    "PROJECT_DOCS_NORMALIZED",
    "DOC_READABILITY",
    "PROJECT_DOCS_SYNC",
    "DOC_SOURCE_TRACEABILITY",
    "DOC_TEST_TRACEABILITY",
    "TEST_RUNTIME_TRACEABILITY",
    "PROJECT_STATE_SYNC",
]

ALLOWED = {"PASS", "FAIL", "NOT_APPLICABLE", "NOT_PROVEN"}
CLAIM_ID = re.compile(r"^TRUTH-[A-Z0-9_-]+$")
PREFERRED_SOURCE_REF = re.compile(r"^([^:;]+)::([^;]+)$")


def run_git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def git_root(start: Path) -> Path:
    return Path(run_git(start, "rev-parse", "--show-toplevel"))


def parse_table(text: str, heading: str) -> list[list[str]]:
    pos = text.find(heading)
    if pos < 0:
        return []
    chunk = text[pos:].split("\n## ", 1)[0]
    rows: list[list[str]] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if not cols or all(set(c) <= {"-", ":"} for c in cols):
            continue
        rows.append(cols)
    return rows


def split_refs(value: str) -> list[str]:
    return [x.strip().strip("`") for x in value.split(";") if x.strip()]


def file_exists(root: Path, ref: str, docs_root: Path | None = None) -> bool:
    p = Path(ref.replace("\\", "/"))
    if p.is_absolute():
        return p.is_file()
    if docs_root is not None and len(p.parts) == 1 and p.suffix.lower() == ".md":
        return (docs_root / p).is_file()
    return (root / p).is_file()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--ledger", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()

    start = Path(args.root).resolve()
    try:
        root = git_root(start)
        head = run_git(root, "rev-parse", "HEAD")
        status = run_git(root, "status", "--porcelain")
    except Exception as exc:
        print(f"FAIL GIT_ERROR {exc}")
        return 1

    failures: list[str] = []
    warnings: list[str] = []
    checked_refs = 0
    checked_symbols = 0
    claims_checked = 0

    if status and not args.allow_dirty:
        failures.append("WORKTREE_NOT_CLEAN")

    profile_path = root / PROFILE_FILE
    if not profile_path.is_file():
        failures.append("MISSING_PROJECT_PROFILE")
        profile_name = "standard"
        truth_required = False
        runtime_policy = {"e2e_required": True}
        sequence_policy = {"required": True, "runtime_trace_required": False}
        documentation_policy = {
            "generated": True,
            "spec_root": ".workflow",
            "docs_root": "docs",
        }
    else:
        try:
            profile_data = parse_profile(profile_path)
            failures.extend(validate_profile(profile_data))
            profile_name = normalized_profile(profile_data)
            truth_required = "PROJECT_TRUTH_SYNC.md" in required_docs(profile_data)
            runtime_policy = runtime_settings(profile_data)
            sequence_policy = sequence_settings(profile_data)
            documentation_policy = documentation_settings(profile_data)
        except Exception as exc:
            failures.append("PROJECT_PROFILE_INVALID:" + str(exc))
            profile_name = "standard"
            truth_required = False
            runtime_policy = {"e2e_required": True}
            sequence_policy = {"required": True, "runtime_trace_required": False}
            documentation_policy = {
                "generated": True,
                "spec_root": ".workflow",
                "docs_root": "docs",
            }

    if documentation_policy.get("generated", False):
        tool_dir = Path(__file__).resolve().parent
        project_docs_validator = tool_dir / "validate_project_docs.py"
        if not project_docs_validator.is_file():
            project_docs_validator = root / "scripts" / "validate_project_docs.py"
        if not project_docs_validator.is_file():
            failures.append("PROJECT_DOCS_VALIDATOR_MISSING")
        else:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(project_docs_validator),
                    "--root",
                    str(root),
                ],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if proc.returncode != 0:
                failures.append("PROJECT_DOCS_COMPILER_VALIDATION_FAILED")

    docs_root = root / str(documentation_policy.get("docs_root", "docs"))
    ledger_arg = args.ledger.strip()
    if ledger_arg:
        ledger_candidate = Path(ledger_arg)
        if ledger_candidate.is_absolute():
            ledger = ledger_candidate
        elif len(ledger_candidate.parts) == 1:
            ledger = docs_root / ledger_candidate
        else:
            ledger = root / ledger_candidate
    else:
        ledger = docs_root / "PROJECT_TRUTH_SYNC.md"

    if not truth_required and not ledger.is_file():
        report = {
            "repo_sha": head,
            "worktree_clean": not bool(status),
            "governance_profile": profile_name,
            "applicable": False,
            "reason": "PROJECT_TRUTH_SYNC is not required by this profile and no ledger exists.",
            "failures": failures,
            "warnings": warnings,
            "result": "FAIL" if failures else "PASS",
        }
        payload = json.dumps(report, indent=2, sort_keys=True)
        print(payload)
        if args.report:
            report_path = Path(args.report)
            if not report_path.is_absolute():
                report_path = root / report_path
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(payload + "\n", encoding="utf-8")
        return 1 if failures else 0
    if not ledger.is_file():
        failures.append(f"MISSING_LEDGER:{args.ledger}")
        text = ""
    else:
        text = ledger.read_text(encoding="utf-8")

    gate_rows = parse_table(text, "## Truth gates")
    gates: dict[str, str] = {}
    for row in gate_rows[1:]:
        if len(row) >= 2:
            gates[row[0]] = row[1]

    for gate in REQUIRED_GATES:
        value = gates.get(gate)
        if value is None:
            failures.append(f"MISSING_GATE:{gate}")
        elif value not in ALLOWED:
            failures.append(f"INVALID_GATE_VALUE:{gate}:{value}")
        elif value == "FAIL":
            failures.append(f"TRUTH_GATE_EXPLICIT_FAIL:{gate}")

    claim_rows = parse_table(text, "## Critical claim traceability")
    for row in claim_rows[1:]:
        if len(row) < 7:
            continue
        claim_id, claim, docs, sources, tests, runtime, claim_status = row[:7]
        if not claim_id or claim_id == "Claim ID":
            continue
        if not CLAIM_ID.match(claim_id):
            failures.append(f"INVALID_CLAIM_ID:{claim_id}")
            continue
        claims_checked += 1
        if claim_status not in ALLOWED:
            failures.append(f"INVALID_CLAIM_STATUS:{claim_id}:{claim_status}")

        for ref in split_refs(docs):
            checked_refs += 1
            if not file_exists(root, ref, docs_root):
                failures.append(f"BROKEN_DOC_REF:{claim_id}:{ref}")

        for ref in split_refs(tests):
            if ref in {"NOT_APPLICABLE", "NOT_PROVEN"}:
                continue
            checked_refs += 1
            if not file_exists(root, ref):
                failures.append(f"BROKEN_TEST_REF:{claim_id}:{ref}")

        for ref in split_refs(sources):
            match = PREFERRED_SOURCE_REF.match(ref)
            if match:
                rel, symbol = match.groups()
                checked_refs += 1
                path = root / rel
                if not path.is_file():
                    failures.append(f"BROKEN_SOURCE_REF:{claim_id}:{rel}")
                    continue
                checked_symbols += 1
                source_text = path.read_text(encoding="utf-8", errors="ignore")
                if symbol not in source_text:
                    failures.append(f"UNRESOLVED_SYMBOL:{claim_id}:{ref}")
            else:
                checked_refs += 1
                if not file_exists(root, ref):
                    failures.append(f"BROKEN_SOURCE_REF:{claim_id}:{ref}")

        if runtime not in {"", "NOT_APPLICABLE", "NOT_PROVEN"}:
            for ref in split_refs(runtime):
                if "/" in ref or "\\" in ref:
                    checked_refs += 1
                    if not file_exists(root, ref):
                        warnings.append(f"RUNTIME_EVIDENCE_PATH_UNRESOLVED:{claim_id}:{ref}")

    overview = docs_root / "SYSTEM_OVERVIEW.md"
    if not overview.is_file():
        failures.append("MISSING_SYSTEM_OVERVIEW")
    else:
        overview_text = overview.read_text(encoding="utf-8", errors="ignore")
        match = re.search(
            r"^Human comprehension status:\s*(.*?)\s*$",
            overview_text,
            re.MULTILINE | re.IGNORECASE,
        )
        human_status = match.group(1).strip().upper() if match else ""
        if not human_status:
            failures.append("HUMAN_COMPREHENSION_STATUS_MISSING")
        elif human_status != "PASS":
            failures.append(
                "HUMAN_COMPREHENSION_STATUS_NOT_PASS:" + human_status
            )
        if gates.get("HUMAN_COMPREHENSION") == "PASS" and human_status != "PASS":
            failures.append(
                "TRUTH_GATE_HUMAN_COMPREHENSION_CONFLICT:"
                + (human_status or "MISSING")
            )

    if documentation_policy.get("generated", False):
        docs_gate = gates.get("PROJECT_DOCS_SYNC")
        if docs_gate not in {"PASS", None}:
            failures.append(
                f"PROJECT_DOCS_SYNC_REQUIRED_BUT_NOT_PASS:{docs_gate}"
            )

    if sequence_policy.get("required", False):
        sequence_gate = gates.get("SEQUENCE_SYNC")
        if sequence_gate not in {"PASS", None}:
            failures.append(
                f"SEQUENCE_SYNC_REQUIRED_BUT_NOT_PASS:{sequence_gate}"
            )

    if runtime_policy.get("e2e_required", True):
        runtime_gate = gates.get("RUNTIME_E2E")
        if runtime_gate not in {"PASS", None}:
            failures.append(
                f"RUNTIME_E2E_REQUIRED_BUT_NOT_PASS:{runtime_gate}"
            )

    if gates.get("PROJECT_STATE_SYNC") == "PASS":
        for gate in REQUIRED_GATES:
            if gate == "PROJECT_STATE_SYNC":
                continue
            value = gates.get(gate)
            if value not in {"PASS", "NOT_APPLICABLE"}:
                failures.append(f"PROJECT_PASS_WITH_UNPROVEN_GATE:{gate}:{value}")

        for row in claim_rows[1:]:
            if len(row) >= 7 and CLAIM_ID.match(row[0]):
                if row[6] not in {"PASS", "NOT_APPLICABLE"}:
                    failures.append(f"PROJECT_PASS_WITH_UNPROVEN_CLAIM:{row[0]}:{row[6]}")

    report = {
        "repo_sha": head,
        "worktree_clean": not bool(status),
        "governance_profile": profile_name,
        "applicable": True,
        "ledger": str(ledger.relative_to(root)) if ledger.is_relative_to(root) else str(ledger),
        "gates": gates,
        "claims_checked": claims_checked,
        "references_checked": checked_refs,
        "symbols_checked": checked_symbols,
        "failures": failures,
        "warnings": warnings,
        "machine_scope": "Provenance/structure/reference checks only; semantic and behavioral truth require mapped source/test/runtime audit.",
        "result": "FAIL" if failures else "PASS",
    }

    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload + "\n", encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

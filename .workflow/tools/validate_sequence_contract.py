#!/usr/bin/env python3
"""Validate BEFORE / DURING / AFTER sequence-contract acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from project_profile import parse_profile, sequence_settings
from sequence_contract import (
    VALID_MODES,
    compare_plan_actual,
    compute_source_digest,
    git_head,
    is_ancestor,
    load_json,
    render_graph_mermaid,
    sha256_file,
    write_json,
)


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def git_show_bytes(root: Path, commit: str, relpath: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(root), "show", f"{commit}:{relpath}"],
        stderr=subprocess.STDOUT,
    )


def expected_plan_mermaid(plan_path: Path, plan: dict) -> str:
    return (
        "%% GENERATED FILE - DO NOT EDIT\n"
        f"%% PLAN_SHA256: {sha256_file(plan_path)}\n"
        f"%% FLOW_ID: {plan.get('flow_id', '')}\n"
        + render_graph_mermaid(plan)
    )


def expected_actual_mermaid(actual: dict) -> str:
    return (
        "%% GENERATED FILE - DO NOT EDIT\n"
        f"%% SOURCE_DIGEST: {actual.get('source_digest', '')}\n"
        f"%% OBSERVED_HEAD: {actual.get('observed_head', '')}\n"
        "%% GENERATED_BY: generate_sequence_actual.py\n"
        + render_graph_mermaid(actual)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--session", required=True)
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    start = Path(args.root).resolve()
    try:
        root = Path(subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
        ).strip())
        head = git_head(root)
        source_digest = compute_source_digest(root)
    except Exception as exc:
        print(f"FAIL GIT_ERROR:{exc}")
        return 1

    session_path = resolve(root, args.session)
    if not session_path.is_file():
        print(f"FAIL MISSING_SESSION:{args.session}")
        return 1

    session = load_json(session_path)
    failures: list[str] = []
    warnings: list[str] = []
    mode = str(session.get("mode", "")).upper()
    scope = str(session.get("scope", "CURRENT")).upper()
    critical = bool(session.get("critical", False))

    if mode not in VALID_MODES:
        failures.append("INVALID_SEQUENCE_MODE:" + mode)
    if scope not in {"CURRENT", "HISTORICAL"}:
        failures.append("INVALID_SEQUENCE_SCOPE:" + scope)

    try:
        policy = sequence_settings(parse_profile(root / "PROJECT_PROFILE.yaml"))
    except Exception as exc:
        failures.append("PROJECT_PROFILE_SEQUENCE_POLICY_ERROR:" + str(exc))
        policy = {"required": True, "runtime_trace_required": False}

    actual_cfg = session.get("actual", {})
    actual_graph_text = str(actual_cfg.get("graph", "")).strip()
    actual_diagram_text = str(actual_cfg.get("diagram", "")).strip()

    if not actual_graph_text:
        failures.append("ACTUAL_GRAPH_PATH_MISSING")
        actual_graph_path = root / "__missing_actual__"
    else:
        actual_graph_path = resolve(root, actual_graph_text)

    if not actual_diagram_text:
        failures.append("ACTUAL_DIAGRAM_PATH_MISSING")
        actual_diagram_path = root / "__missing_actual_diagram__"
    else:
        actual_diagram_path = resolve(root, actual_diagram_text)

    actual: dict = {}
    if not actual_graph_path.is_file():
        failures.append("MISSING_ACTUAL_GRAPH:" + actual_graph_text)
    else:
        try:
            actual = load_json(actual_graph_path)
        except Exception as exc:
            failures.append("INVALID_ACTUAL_GRAPH:" + str(exc))

    if actual:
        if actual.get("generated") is not True:
            failures.append("ACTUAL_GRAPH_NOT_GENERATED")
        if actual.get("generated_by") != "generate_sequence_actual.py":
            failures.append("ACTUAL_GRAPH_UNKNOWN_GENERATOR")
        observed_digest = str(actual.get("source_digest", ""))
        recorded_digest = str(actual_cfg.get("source_digest", "")).strip()

        if not observed_digest:
            failures.append("ACTUAL_SOURCE_DIGEST_MISSING")
        if recorded_digest and recorded_digest != observed_digest:
            failures.append("SESSION_ACTUAL_SOURCE_DIGEST_MISMATCH")

        if scope == "CURRENT" and observed_digest != source_digest:
            failures.append(
                "ACTUAL_SOURCE_DIGEST_MISMATCH:"
                + observed_digest
                + "!="
                + source_digest
            )

        declared_entries = [str(x) for x in actual_cfg.get("entries", [])]
        graph_entries = [str(x) for x in actual.get("entries", [])]
        if declared_entries and declared_entries != graph_entries:
            failures.append("SESSION_ACTUAL_ENTRYPOINT_MISMATCH")
        if critical and not graph_entries:
            failures.append("CRITICAL_SEQUENCE_ENTRYPOINT_MISSING")

        if not actual_diagram_path.is_file():
            failures.append("MISSING_ACTUAL_DIAGRAM:" + actual_diagram_text)
        else:
            expected = expected_actual_mermaid(actual)
            observed = actual_diagram_path.read_text(encoding="utf-8", errors="ignore")
            if observed != expected:
                failures.append("GENERATED_ACTUAL_DIAGRAM_TAMPERED")

    plan_cfg = session.get("plan", {})
    plan_required = bool(plan_cfg.get("required", False))
    source_compare = None
    runtime_compare = None
    plan = {}

    if mode == "BEFORE":
        if not plan_required:
            failures.append("BEFORE_REQUIRES_PLAN")
        if not bool(plan_cfg.get("frozen", False)):
            failures.append("BEFORE_PLAN_NOT_FROZEN")

        plan_rel = str(plan_cfg.get("contract", "")).strip()
        frozen_commit = str(plan_cfg.get("frozen_commit", "")).strip()
        implementation_base = str(session.get("implementation_base_sha", "")).strip()
        recorded_hash = str(plan_cfg.get("sha256", "")).strip()
        plan_diagram = str(plan_cfg.get("diagram", "")).strip()

        if not plan_rel:
            failures.append("BEFORE_PLAN_PATH_MISSING")
        if not frozen_commit:
            failures.append("BEFORE_FROZEN_COMMIT_MISSING")
        if not implementation_base:
            failures.append("BEFORE_IMPLEMENTATION_BASE_MISSING")

        plan_path = resolve(root, plan_rel) if plan_rel else root / "__missing_plan__"
        if plan_rel and not plan_path.is_file():
            failures.append("MISSING_PLAN_CONTRACT:" + plan_rel)
        elif plan_path.is_file():
            try:
                plan = load_json(plan_path)
            except Exception as exc:
                failures.append("INVALID_PLAN_CONTRACT:" + str(exc))

        if plan_path.is_file():
            current_hash = sha256_file(plan_path)
            if not recorded_hash:
                failures.append("PLAN_HASH_MISSING")
            elif recorded_hash != current_hash:
                failures.append("PLAN_HASH_MISMATCH")

            if frozen_commit and plan_rel:
                try:
                    frozen_bytes = git_show_bytes(root, frozen_commit, plan_rel)
                    frozen_hash = hashlib.sha256(frozen_bytes).hexdigest()
                    if recorded_hash and frozen_hash != recorded_hash:
                        failures.append("FROZEN_PLAN_HASH_MISMATCH")
                except Exception:
                    failures.append("FROZEN_PLAN_NOT_FOUND_IN_COMMIT")

        if frozen_commit and implementation_base:
            if not is_ancestor(root, frozen_commit, implementation_base):
                failures.append("PLAN_DOES_NOT_PRECEDE_IMPLEMENTATION")
            if not is_ancestor(root, implementation_base, head):
                failures.append("IMPLEMENTATION_BASE_NOT_ANCESTOR_OF_HEAD")

        if plan:
            if str(plan.get("status", "")).upper() != "FROZEN":
                failures.append("PLAN_CONTRACT_STATUS_NOT_FROZEN")

            if not plan_diagram:
                failures.append("PLAN_DIAGRAM_PATH_MISSING")
            else:
                pd = resolve(root, plan_diagram)
                if not pd.is_file():
                    failures.append("MISSING_PLAN_DIAGRAM:" + plan_diagram)
                elif pd.read_text(encoding="utf-8", errors="ignore") != expected_plan_mermaid(plan_path, plan):
                    failures.append("GENERATED_PLAN_DIAGRAM_TAMPERED")

            if actual:
                source_compare = compare_plan_actual(plan, actual, "SOURCE")
                if not source_compare["match"]:
                    if source_compare["missing_required_edges"]:
                        failures.append(
                            "MISSING_REQUIRED_EDGES:"
                            + str(len(source_compare["missing_required_edges"]))
                        )
                    if source_compare["forbidden_edges_present"]:
                        failures.append(
                            "FORBIDDEN_EDGES_PRESENT:"
                            + str(len(source_compare["forbidden_edges_present"]))
                        )
                    if source_compare["unresolved_bindings"]:
                        failures.append(
                            "UNRESOLVED_PLAN_BINDINGS:"
                            + str(len(source_compare["unresolved_bindings"]))
                        )

    elif mode in {"DURING", "AFTER"}:
        if plan_required:
            failures.append(mode + "_PLAN_MUST_BE_NOT_APPLICABLE")
        for key in ("contract", "frozen_commit", "sha256", "diagram"):
            if str(plan_cfg.get(key, "")).strip():
                failures.append("RETROSPECTIVE_PLAN_FORBIDDEN:" + key)
        if bool(plan_cfg.get("frozen", False)):
            failures.append("RETROSPECTIVE_PLAN_FORBIDDEN:frozen")

    tests = session.get("tests", [])
    if bool(session.get("test_traceability_required", True)) and critical and not tests:
        failures.append("CRITICAL_SEQUENCE_TEST_TRACEABILITY_MISSING")

    runtime_cfg = session.get("runtime_trace", {})
    runtime_required = bool(runtime_cfg.get("required", False))
    if critical and policy.get("runtime_trace_required", False):
        runtime_required = True

    runtime_graph_text = str(runtime_cfg.get("graph", "")).strip()
    if runtime_required and not runtime_graph_text:
        failures.append("RUNTIME_SEQUENCE_GRAPH_REQUIRED")
    elif runtime_graph_text:
        runtime_path = resolve(root, runtime_graph_text)
        if not runtime_path.is_file():
            failures.append("MISSING_RUNTIME_SEQUENCE_GRAPH:" + runtime_graph_text)
        else:
            try:
                runtime_graph = load_json(runtime_path)
                runtime_digest = str(runtime_graph.get("source_digest", ""))
                actual_digest = str(actual.get("source_digest", "")) if actual else ""
                if runtime_digest and actual_digest and runtime_digest != actual_digest:
                    failures.append("RUNTIME_SEQUENCE_SOURCE_DIGEST_MISMATCH")
                if mode == "BEFORE" and plan:
                    runtime_compare = compare_plan_actual(plan, runtime_graph, "RUNTIME")
                    if runtime_required and not runtime_compare["match"]:
                        failures.append("PLAN_RUNTIME_SEQUENCE_MISMATCH")
            except Exception as exc:
                failures.append("INVALID_RUNTIME_SEQUENCE_GRAPH:" + str(exc))

    if policy.get("required", False) and not str(session.get("session_id", "")).strip():
        failures.append("SEQUENCE_SESSION_ID_MISSING")

    report = {
        "schema_version": 1,
        "session": str(session_path.relative_to(root)) if session_path.is_relative_to(root) else str(session_path),
        "session_id": session.get("session_id"),
        "phase": session.get("phase"),
        "mode": mode,
        "scope": scope,
        "critical": critical,
        "final_head": head,
        "source_digest": source_digest,
        "plan_actual_source_comparison": source_compare,
        "plan_runtime_comparison": runtime_compare,
        "failures": failures,
        "warnings": warnings,
        "mismatch_resolution_required": bool(failures),
        "allowed_mismatch_classifications": [
            "CODE_DEFECT",
            "PLAN_CHANGE",
            "GENERATOR_DEFECT",
        ],
        "result": "FAIL" if failures else "PASS",
        "evidence_boundary": (
            "Static generated actual graph proves only extracted structural edges. "
            "Dynamic behavior requires runtime trace or semantic evidence when applicable."
        ),
    }

    report_path_text = args.report or str(session.get("acceptance_report", "")).strip()
    if report_path_text:
        report_path = resolve(root, report_path_text)
        write_json(report_path, report)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

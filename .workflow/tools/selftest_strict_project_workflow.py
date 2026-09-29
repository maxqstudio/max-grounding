#!/usr/bin/env python3
"""End-to-end STRICT Skill Workflow self-test.

Creates a temporary Git project, adopts the current project-local tool pack,
generates DURING sequence evidence and canonical docs/, commits the exact
snapshot, then runs the blocking validator chain against a clean worktree.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


QUESTIONS = [
    "What is the project and what problem does it solve?",
    "Who uses it and what are the primary outcomes?",
    "What are the major components and how do they relate?",
    "How does important data flow through the system?",
    "What are the main user/domain workflows?",
    "What are the important lifecycle states and transitions?",
    "Who/what is authoritative for important decisions?",
    "What is mutable and what is immutable?",
    "How does failure/recovery behave?",
    "What is the current project state?",
    "What is proven and what is not proven?",
    "What may happen next and what is blocked?",
]

PASS_GATES = [
    "SOURCE_TESTS",
    "PROVENANCE_SYNC",
    "REFERENCE_SYNC",
    "STRUCTURAL_SYNC",
    "SEMANTIC_SYNC",
    "CROSS_DOCUMENT_CONSISTENCY",
    "HUMAN_COMPREHENSION",
    "SEQUENCE_SYNC",
    "DOC_SOURCE_TRACEABILITY",
    "DOC_TEST_TRACEABILITY",
    "PROJECT_STATE_SYNC",
]


def run(root: Path, *args: str, expect: int = 0) -> str:
    proc = subprocess.run(
        list(args),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != expect:
        raise RuntimeError(
            "command failed\n"
            + " ".join(args)
            + "\nexpected="
            + str(expect)
            + " actual="
            + str(proc.returncode)
            + "\n"
            + proc.stdout
        )
    return proc.stdout


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def git(root: Path, *args: str) -> str:
    return run(root, "git", *args).strip()


def clone_fixture(source: Path, prefix: str) -> tuple[tempfile.TemporaryDirectory, Path]:
    holder = tempfile.TemporaryDirectory(prefix=prefix)
    clone = Path(holder.name) / "fixture"
    run(source, "git", "clone", "--quiet", "--no-hardlinks", str(source), str(clone))
    git(clone, "config", "user.email", "skill-workflow-selftest@example.invalid")
    git(clone, "config", "user.name", "Skill Workflow Selftest")
    return holder, clone


def sync_and_commit_case(case_root: Path, message: str) -> None:
    tool_root = case_root / ".workflow" / "tools"
    run(
        case_root,
        sys.executable,
        str(tool_root / "sync_project_truth.py"),
        "--root",
        str(case_root),
    )
    git(case_root, "add", ".")
    git(case_root, "commit", "-m", message)


def assert_cross_failure(case_root: Path, expected_code: str) -> None:
    tool_root = case_root / ".workflow" / "tools"
    base = git(case_root, "rev-parse", "HEAD^")
    output = run(
        case_root,
        sys.executable,
        str(tool_root / "validate_cross_document_consistency.py"),
        "--root",
        str(case_root),
        "--base",
        base,
        "--require-base",
        expect=1,
    )
    if expected_code not in output:
        raise RuntimeError(
            "expected cross-document failure missing: "
            + expected_code
            + "\\n"
            + output
        )


def run_relation_and_truth_regressions(valid_root: Path) -> None:
    # Unknown relation target must still be rejected.
    holder, case_root = clone_fixture(
        valid_root, "skill-workflow-relation-unknown-target-"
    )
    try:
        claims_path = case_root / ".workflow" / "claims.json"
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        claims["relations"][0]["other_claim_id"] = "TRUTH-DOES-NOT-EXIST"
        write_json(claims_path, claims)
        sync_and_commit_case(case_root, "test: unknown relation target")
        assert_cross_failure(case_root, "RELATION_UNKNOWN_RIGHT_CLAIM")
    finally:
        holder.cleanup()
    print("UNKNOWN_RELATION_TARGET_DETECTION=PASS")

    # Invalid relation type must still be rejected.
    holder, case_root = clone_fixture(
        valid_root, "skill-workflow-relation-invalid-type-"
    )
    try:
        claims_path = case_root / ".workflow" / "claims.json"
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        claims["relations"][0]["relation"] = "DEPENDS_SOMEHOW"
        write_json(claims_path, claims)
        sync_and_commit_case(case_root, "test: invalid relation type")
        assert_cross_failure(case_root, "INVALID_CLAIM_RELATION")
    finally:
        holder.cleanup()
    print("INVALID_RELATION_DETECTION=PASS")

    # A real independently projected conflicting claim text must remain detectable.
    holder, case_root = clone_fixture(
        valid_root, "skill-workflow-real-claim-conflict-"
    )
    try:
        conflict = case_root / "docs" / "CLAIM_CONFLICT.md"
        conflict.write_text(
            "# Independent claim projection\n\n"
            "| Claim ID | Claim | Status |\n"
            "|---|---|---|\n"
            "| TRUTH-HEALTH-001 | This deliberately contradicts the canonical health claim. | PASS |\n",
            encoding="utf-8",
        )
        git(case_root, "add", ".")
        git(case_root, "commit", "-m", "test: real claim text conflict")
        assert_cross_failure(case_root, "CLAIM_TEXT_CONFLICT:TRUTH-HEALTH-001")
    finally:
        holder.cleanup()
    print("REAL_CLAIM_TEXT_CONFLICT_DETECTION=PASS")

    # Explicit PROJECT_STATE_SYNC=FAIL must make Project Truth fail closed.
    holder, case_root = clone_fixture(
        valid_root, "skill-workflow-project-state-fail-"
    )
    try:
        acceptance_path = case_root / ".workflow" / "acceptance.json"
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        acceptance["truth_gates"]["PROJECT_STATE_SYNC"] = "FAIL"
        write_json(acceptance_path, acceptance)
        sync_and_commit_case(case_root, "test: explicit project state failure")
        tool_root = case_root / ".workflow" / "tools"
        output = run(
            case_root,
            sys.executable,
            str(tool_root / "validate_project_truth.py"),
            "--root",
            str(case_root),
            expect=1,
        )
        if "TRUTH_GATE_EXPLICIT_FAIL:PROJECT_STATE_SYNC" not in output:
            raise RuntimeError(
                "Project Truth did not fail closed for PROJECT_STATE_SYNC=FAIL\\n"
                + output
            )
        if '"result": "FAIL"' not in output:
            raise RuntimeError(
                "Project Truth failure did not report result=FAIL\\n" + output
            )
    finally:
        holder.cleanup()
    print("PROJECT_STATE_FAIL_CLOSED=PASS")


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent

    with tempfile.TemporaryDirectory(prefix="skill-workflow-strict-selftest-") as td:
        root = Path(td)

        run(
            skill_root,
            sys.executable,
            str(skill_root / "scripts" / "initialize_project_truth.py"),
            "--root",
            str(root),
        )

        tool_root = root / ".workflow" / "tools"
        if not (tool_root / "validate_project_truth.py").is_file():
            raise RuntimeError("vendored STRICT tool pack missing")

        git(root, "init")
        git(root, "config", "user.email", "skill-workflow-selftest@example.invalid")
        git(root, "config", "user.name", "Skill Workflow Selftest")

        profile = """profile: strict

contracts:
  api_contracts: required
  data_contracts: required
  ui_information_architecture: required
  runbook: required
  decisions: required
  known_defects: required
  glossary: required
  changelog: optional

runtime:
  e2e_required: false

documentation:
  generated: true
  spec_root: .workflow
  docs_root: docs

sequence:
  required: true
  runtime_trace_required: false

notes:
  profile_reason: strict_end_to_end_governance_selftest
"""
        (root / "PROJECT_PROFILE.yaml").write_text(profile, encoding="utf-8")

        (root / "app.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "@app.get('/health')\n"
            "def health():\n"
            "    return {'ok': True}\n",
            encoding="utf-8",
        )
        tests_root = root / "tests"
        tests_root.mkdir(parents=True, exist_ok=True)
        (tests_root / "test_health.py").write_text(
            "def test_health_contract():\n"
            "    assert True\n",
            encoding="utf-8",
        )

        write_json(
            root / ".workflow" / "project.json",
            {
                "schema_version": 1,
                "project": {
                    "name": "Strict Workflow Fixture",
                    "repository": "local/strict-workflow-fixture",
                    "purpose": "Verify the complete Skill Workflow STRICT governance path.",
                    "primary_users": ["governance tester"],
                    "expected_outcomes": [
                        "deterministic docs",
                        "validated DURING sequence evidence",
                    ],
                },
                "technology": {
                    "languages": ["Python"],
                    "frameworks": ["FastAPI"],
                    "persistence": [],
                    "external_systems": [],
                },
                "entry_points": [
                    {
                        "name": "health",
                        "path": "app.py::health",
                        "purpose": "Serve health status.",
                    }
                ],
                "constraints": [
                    "Generated governance Markdown is never manually edited."
                ],
            },
        )

        write_json(
            root / ".workflow" / "authority.json",
            {
                "schema_version": 1,
                "authorities": [
                    {
                        "concern": "source",
                        "authority": "Git committed fixture source",
                        "meaning": "Implementation truth is the committed source tree.",
                        "mutable": True,
                    },
                    {
                        "concern": "runtime",
                        "authority": "self-test process",
                        "meaning": "Runtime behavior is outside this static fixture and is not applicable.",
                        "mutable": True,
                    },
                    {
                        "concern": "acceptance",
                        "authority": "blocking Skill Workflow validators",
                        "meaning": "Acceptance requires the complete validator chain.",
                        "mutable": False,
                    },
                ],
                "invariants": [
                    "Canonical project documentation lives only under docs/.",
                    "DURING sequence mode must not contain a retrospective plan.",
                ],
            },
        )

        write_json(
            root / ".workflow" / "state.json",
            {
                "schema_version": 1,
                "phase": "STRICT_SELFTEST",
                "status": "READY_FOR_VALIDATION",
                "working_branch": "main",
                "last_accepted_sha": "",
                "last_accepted_branch": "",
                "proven": [
                    "Source fixture exists.",
                    "Governance semantics are explicitly declared.",
                ],
                "not_proven": [
                    "No external runtime behavior is claimed by this fixture."
                ],
                "blockers": [],
                "next_authorized_actions": [
                    "Run the blocking governance validator chain."
                ],
                "blocked_actions": [
                    "Do not reinterpret generated documentation as upstream authority."
                ],
            },
        )

        write_json(
            root / ".workflow" / "roadmap.json",
            {
                "schema_version": 1,
                "current_phase": "STRICT_SELFTEST",
                "phases": [
                    {
                        "id": "STRICT_SELFTEST",
                        "title": "STRICT governance self-test",
                        "status": "CURRENT",
                        "objective": "Verify the complete blocking governance path.",
                        "exit_criteria": [
                            "All blocking validators pass.",
                            "Roadmap synchronization is proven.",
                        ],
                    },
                    {
                        "id": "COMPLETE",
                        "title": "STRICT self-test complete",
                        "status": "PLANNED",
                        "objective": "Record a clean validated fixture.",
                        "exit_criteria": ["The final committed fixture validates read-only."],
                    },
                ],
            },
        )

        write_json(
            root / ".workflow" / "architecture.json",
            {
                "schema_version": 1,
                "components": [
                    {
                        "id": "fixture_api",
                        "name": "Fixture API",
                        "purpose": "Expose deterministic health status.",
                        "owns": ["GET /health"],
                        "depends_on": [],
                    }
                ],
                "data_flows": [
                    {
                        "from": "caller",
                        "to": "Fixture API",
                        "meaning": "Caller requests health and receives status.",
                    }
                ],
                "external_boundaries": [],
            },
        )

        write_json(
            root / ".workflow" / "contracts.json",
            {
                "schema_version": 1,
                "api_contracts": [
                    {
                        "method": "GET",
                        "path": "/health",
                        "purpose": "Return fixture health.",
                        "authority": "app.py::health",
                        "mutation": "NONE",
                        "error_behavior": "Fail closed on application error.",
                    }
                ],
                "data_contracts": [
                    {
                        "name": "health response",
                        "source_of_truth": "app.py::health",
                        "mutability": "immutable response snapshot",
                        "legal_writes": [],
                        "retention": "not persisted",
                        "invariants": ["ok is true for the fixture response"],
                    }
                ],
                "ui_surfaces": [
                    {
                        "name": "none",
                        "purpose": "No UI surface exists in this fixture.",
                        "backend_authority": "GET /health",
                        "visible_data": ["health status"],
                        "actions": ["read"],
                        "forbidden_actions": ["mutation"],
                    }
                ],
                "runbook_steps": [
                    {
                        "name": "governance validation",
                        "command": "python .workflow/tools/validate_project_truth.py",
                        "purpose": "Validate final STRICT project truth.",
                        "expected": "PASS",
                    }
                ],
            },
        )

        write_json(
            root / ".workflow" / "claims.json",
            {
                "schema_version": 1,
                "claims": [
                    {
                        "id": "TRUTH-HEALTH-001",
                        "claim": "The fixture health entrypoint is source and test traceable.",
                        "documents": ["PROJECT_TRUTH_SYNC.md"],
                        "source_owners": ["app.py::health"],
                        "tests": ["tests/test_health.py"],
                        "runtime_evidence": [],
                        "status": "PASS",
                    },
                    {
                        "id": "TRUTH-HEALTH-DEPENDENCY-001",
                        "claim": "The fixture health dependency is source and test traceable.",
                        "documents": ["PROJECT_TRUTH_SYNC.md"],
                        "source_owners": ["app.py::health"],
                        "tests": ["tests/test_health.py"],
                        "runtime_evidence": [],
                        "status": "PASS",
                    },
                ],
                "relations": [
                    {
                        "claim_id": "TRUTH-HEALTH-001",
                        "relation": "REQUIRES",
                        "other_claim_id": "TRUTH-HEALTH-DEPENDENCY-001",
                        "notes": "The public health truth requires its traced implementation dependency.",
                    }
                ],
            },
        )

        write_json(
            root / ".workflow" / "decisions.json",
            {
                "schema_version": 1,
                "decisions": [
                    {
                        "id": "D-001",
                        "date": "2026-09-26",
                        "title": "Use DURING sequence mode",
                        "context": "The fixture represents already-present implementation.",
                        "decision": "Use DURING and prohibit retrospective plans.",
                        "reason": "Preserve sequence provenance semantics.",
                        "alternatives": ["BEFORE", "AFTER"],
                        "impact": "Actual sequence only.",
                        "authority": "self-test contract",
                    }
                ],
            },
        )

        write_json(
            root / ".workflow" / "known_defects.json",
            {
                "schema_version": 1,
                "defects": [
                    {
                        "id": "KD-001",
                        "status": "CLOSED",
                        "summary": "No open product defect is required for the fixture.",
                        "evidence": "self-test scope",
                    }
                ],
            },
        )

        write_json(
            root / ".workflow" / "glossary.json",
            {
                "schema_version": 1,
                "terms": [
                    {
                        "term": "DURING",
                        "definition": "Sequence governance for implementation already in progress.",
                    }
                ],
            },
        )

        write_json(
            root / ".workflow" / "changelog.json",
            {
                "schema_version": 1,
                "entries": [
                    {
                        "date": "2026-09-26",
                        "title": "STRICT fixture initialized",
                        "type": "test",
                        "changes": ["Created deterministic governance fixture."],
                    }
                ],
            },
        )

        flow_path = root / ".workflow" / "workflows" / "FLOW-EXAMPLE.json"
        if flow_path.exists():
            flow_path.unlink()

        write_json(
            root / ".workflow" / "workflows" / "FLOW-HEALTH.json",
            {
                "schema_version": 1,
                "flow_id": "FLOW-HEALTH",
                "title": "Health request",
                "purpose": "Serve deterministic health status.",
                "critical": True,
                "entry_condition": "GET /health",
                "authority": "app.py::health",
                "states": ["REQUESTED", "RESPONDED"],
                "transitions": [
                    {
                        "from": "REQUESTED",
                        "to": "RESPONDED",
                        "action": "health returns status",
                    }
                ],
                "invariants": ["No state mutation occurs."],
                "failure_behavior": ["Application error fails the request."],
                "restart_behavior": ["Retry is safe because the flow is read-only."],
                "rollback_behavior": ["No rollback is required."],
                "source_owners": ["app.py::health"],
                "tests": ["tests/test_health.py"],
                "sequence_session": "docs/sequence/sessions/health.json",
            },
        )

        gates = {name: "PASS" for name in PASS_GATES}
        gates.update(
            {
                "RUNTIME_E2E": "NOT_APPLICABLE",
                "BEHAVIORAL_SYNC": "NOT_APPLICABLE",
                "TEST_RUNTIME_TRACEABILITY": "NOT_APPLICABLE",
                "ROADMAP_SYNC": "NOT_PROVEN",
                "DOC_LAYOUT": "NOT_PROVEN",
                "PROJECT_DOCS_NORMALIZED": "NOT_PROVEN",
                "DOC_READABILITY": "NOT_PROVEN",
                "PROJECT_DOCS_SYNC": "NOT_PROVEN",
            }
        )

        write_json(
            root / ".workflow" / "acceptance.json",
            {
                "schema_version": 1,
                "evidence_boundary": (
                    "STRICT governance, deterministic docs, static DURING sequence "
                    "and source/test traceability only; external runtime is not applicable."
                ),
                "runtime_status": "NOT_APPLICABLE",
                "human_comprehension_status": "PASS",
                "human_comprehension_questions": {
                    question: "PASS" for question in QUESTIONS
                },
                "sequence_mode": "DURING",
                "sequence_session": "docs/sequence/sessions/health.json",
                "sequence_sync_status": "PASS",
                "truth_gates": gates,
                "requirements": [
                    {
                        "id": "REQ-001",
                        "requirement": "Health flow is source/test traceable.",
                        "evidence": "app.py::health; tests/test_health.py",
                        "status": "PASS",
                    }
                ],
                "test_commands": ["python tests/test_health.py"],
                "runtime_checks": ["NOT_APPLICABLE"],
            },
        )

        git(root, "add", ".")
        git(root, "commit", "-m", "test: establish strict fixture base")
        base_sha = git(root, "rev-parse", "HEAD")

        generated_root = root / "docs" / "sequence" / "generated"
        generated_root.mkdir(parents=True, exist_ok=True)
        actual_json = generated_root / "health.actual.json"
        actual_mmd = generated_root / "health.actual.mmd"

        run(
            root,
            sys.executable,
            str(tool_root / "generate_sequence_actual.py"),
            "--root",
            str(root),
            "--output-json",
            str(actual_json.relative_to(root)),
            "--output-mermaid",
            str(actual_mmd.relative_to(root)),
            "--entry",
            "app.py::health",
        )

        actual_json_bytes = actual_json.read_bytes()
        actual_mmd_bytes = actual_mmd.read_bytes()
        if b"\r\n" in actual_json_bytes:
            raise RuntimeError("generate_sequence_actual wrote CRLF into actual JSON")
        if b"\r\n" in actual_mmd_bytes:
            raise RuntimeError("generate_sequence_actual wrote CRLF into actual Mermaid")
        if not actual_json_bytes.endswith(b"\n"):
            raise RuntimeError("generate_sequence_actual actual JSON missing final LF")
        if not actual_mmd_bytes.endswith(b"\n"):
            raise RuntimeError("generate_sequence_actual actual Mermaid missing final LF")
        print("DETERMINISTIC_SEQUENCE_LF=PASS")

        actual = json.loads(actual_json.read_text(encoding="utf-8"))
        write_json(
            root / "docs" / "sequence" / "sessions" / "health.json",
            {
                "schema_version": 1,
                "session_id": "STRICT-SELFTEST-HEALTH",
                "phase": "STRICT_SELFTEST",
                "mode": "DURING",
                "scope": "CURRENT",
                "critical": True,
                "status": "VERIFIED",
                "implementation_base_sha": "",
                "plan": {
                    "required": False,
                    "contract": "",
                    "frozen": False,
                    "frozen_commit": "",
                    "sha256": "",
                    "diagram": "",
                },
                "actual": {
                    "graph": "docs/sequence/generated/health.actual.json",
                    "diagram": "docs/sequence/generated/health.actual.mmd",
                    "source_digest": actual["source_digest"],
                    "entries": ["app.py::health"],
                },
                "test_traceability_required": True,
                "tests": ["tests/test_health.py"],
                "runtime_trace": {
                    "required": False,
                    "graph": "",
                },
                "acceptance_report": "",
            },
        )

        run(
            root,
            sys.executable,
            str(tool_root / "sync_project_truth.py"),
            "--root",
            str(root),
        )

        acceptance_bytes = (root / ".workflow" / "acceptance.json").read_bytes()
        if b"\r\n" in acceptance_bytes:
            raise RuntimeError("sync_project_truth wrote CRLF into acceptance.json")
        if not acceptance_bytes.endswith(b"\n"):
            raise RuntimeError("sync_project_truth acceptance.json missing final LF")
        synced_acceptance = json.loads(acceptance_bytes.decode("utf-8"))
        if synced_acceptance.get("truth_gates", {}).get("ROADMAP_SYNC") != "PASS":
            raise RuntimeError("STRICT sync did not record ROADMAP_SYNC=PASS")
        if not (root / "docs" / "ROADMAP.md").is_file():
            raise RuntimeError("STRICT sync did not generate docs/ROADMAP.md")
        print("DETERMINISTIC_JSON_LF=PASS")
        print("ROADMAP_SYNC=PASS")

        git(root, "add", ".")
        git(root, "commit", "-m", "test: seal strict governance fixture")
        final_sha = git(root, "rev-parse", "HEAD")

        if git(root, "status", "--porcelain"):
            raise RuntimeError("fixture not clean before validator chain")

        commands = [
            (
                "PROJECT_DOCS",
                [
                    sys.executable,
                    str(tool_root / "validate_project_docs.py"),
                    "--root",
                    str(root),
                ],
            ),
            (
                "DOC_QUALITY",
                [
                    sys.executable,
                    str(tool_root / "validate_doc_quality.py"),
                    "--root",
                    str(root),
                ],
            ),
            (
                "SEQUENCE",
                [
                    sys.executable,
                    str(tool_root / "validate_sequence_sessions.py"),
                    "--root",
                    str(root),
                ],
            ),
            (
                "HANDOFF",
                [
                    sys.executable,
                    str(tool_root / "validate_handoff.py"),
                    "--root",
                    str(root),
                ],
            ),
            (
                "HUMAN_COMPREHENSION",
                [
                    sys.executable,
                    str(tool_root / "validate_human_comprehension.py"),
                    "--root",
                    str(root),
                    "--require-pass",
                ],
            ),
            (
                "CROSS_DOCUMENT",
                [
                    sys.executable,
                    str(tool_root / "validate_cross_document_consistency.py"),
                    "--root",
                    str(root),
                    "--base",
                    base_sha,
                    "--require-base",
                ],
            ),
            (
                "PROJECT_TRUTH",
                [
                    sys.executable,
                    str(tool_root / "validate_project_truth.py"),
                    "--root",
                    str(root),
                ],
            ),
        ]

        cross_output = ""
        for label, command in commands:
            output = run(root, *command)
            if label == "CROSS_DOCUMENT":
                cross_output = output
            if git(root, "status", "--porcelain"):
                raise RuntimeError(label + " validator mutated the clean fixture")
            print(label + "=PASS")

        if '"claim_relations_checked": 1' not in cross_output:
            raise RuntimeError(
                "main STRICT integration fixture did not validate its non-empty Claim relation\n"
                + cross_output
            )
        print("NONEMPTY_RELATION_FIXTURE=PASS")
        print("VALID_RELATION_ACCEPTANCE=PASS")
        print("CLAIM_RELATION_REGRESSION=PASS")

        run_relation_and_truth_regressions(root)

        if final_sha != git(root, "rev-parse", "HEAD"):
            raise RuntimeError("validator chain changed final Git HEAD")

        print("STRICT_FIXTURE_BASE_SHA=" + base_sha)
        print("STRICT_FIXTURE_FINAL_SHA=" + final_sha)
        print("STRICT_VALIDATOR_CHAIN=PASS")
        print("VALIDATORS_READ_ONLY=PASS")
        print("FINAL_WORKTREE_CLEAN=PASS")
        print("STRICT_SELFTEST=PASS")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Regression tests for Governance Engine V2 primitives and SW2-02 modes."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from extract_project_facts import extract_project_facts
from governance_engine import (
    FINALIZE_NODE_NAMES,
    VERIFY_NODE_NAMES,
    ValidationDAG,
    ValidationNode,
    classify_changed_paths,
    collect_changed_paths,
    dependency_closure,
    develop_node_names,
    effective_mode,
    planned_node_names,
)
from project_snapshot import ProjectSnapshot, active_project_snapshot
from sequence_contract import compute_source_digest, source_files


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def git_text(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def snapshot_parity_and_immutability() -> None:
    with tempfile.TemporaryDirectory(prefix="sw2-snapshot-") as td:
        root = Path(td).resolve()
        git(root, "init")
        git(root, "config", "user.email", "sw2@example.invalid")
        git(root, "config", "user.name", "SW2 Test")
        (root / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
        source = root / "app.py"
        source.write_bytes(b"def value():\r\n    return 1\r\n")
        (root / "ignored.py").write_text("raise RuntimeError()\n", encoding="utf-8")
        git(root, "add", ".gitignore", "app.py")
        git(root, "commit", "-m", "fixture")

        legacy_files = source_files(root)
        legacy_digest = compute_source_digest(root)
        snapshot = ProjectSnapshot.capture(root)
        require(
            [p.resolve().relative_to(root).as_posix() for p in legacy_files]
            == [p.resolve().relative_to(root).as_posix() for p in snapshot.source_files()],
            "snapshot inventory differs from accepted standalone inventory",
        )
        require(snapshot.source_digest == legacy_digest, "snapshot digest parity failed")
        require(b"\r\n" not in snapshot.read_bytes("app.py"), "CRLF was not canonicalized")
        require(
            "ignored.py" not in [item.relative_path for item in snapshot.files],
            "ignored source leaked into snapshot",
        )

        old_digest = snapshot.source_digest
        old_text = snapshot.read_text("app.py")
        source.write_text("def value():\n    return 2\n", encoding="utf-8")
        require(snapshot.source_digest == old_digest, "snapshot digest mutated")
        require(snapshot.read_text("app.py") == old_text, "snapshot bytes mutated")
        fresh = ProjectSnapshot.capture(root)
        require(fresh.source_digest != old_digest, "fresh snapshot missed source change")
        print("SNAPSHOT_PARITY_IMMUTABILITY=PASS")


def snapshot_fact_reuse() -> None:
    with tempfile.TemporaryDirectory(prefix="sw2-reuse-") as td:
        root = Path(td).resolve()
        (root / "app.py").write_text(
            "def helper():\n    return 1\n\ndef main():\n    return helper()\n",
            encoding="utf-8",
        )
        snapshot = ProjectSnapshot.capture(root)
        with active_project_snapshot(snapshot):
            first = extract_project_facts(root)
            second = extract_project_facts(root)
            digest = compute_source_digest(root)
        require(first is second, "derived facts were not memoized")
        require(digest == snapshot.source_digest, "active digest did not reuse snapshot")
        metrics = snapshot.metrics()
        require(metrics["source_enumerations"] == 1, "source inventory repeated")
        require(metrics["file_reads"] == 1, "source file reads repeated")
        require(metrics["derived_cache_misses"] == 1, "facts should be computed once")
        require(metrics["derived_cache_hits"] >= 1, "facts cache was not reused")
        print("SNAPSHOT_FACT_REUSE=PASS")


def dag_executes_once() -> None:
    calls = {"a": 0, "b": 0, "c": 0}

    def action(name: str):
        def run() -> tuple[int, str]:
            calls[name] += 1
            return 0, name + "=PASS\n"

        return run

    results = ValidationDAG(
        [
            ValidationNode("a", (), action("a")),
            ValidationNode("b", ("a",), action("b")),
            ValidationNode("c", ("a", "b"), action("c")),
        ]
    ).run()
    require(all(item.status == "PASS" for item in results.values()), "DAG did not pass")
    require(calls == {"a": 1, "b": 1, "c": 1}, f"duplicate DAG execution: {calls}")
    print("DAG_EXECUTE_ONCE=PASS")


def dag_fail_closed() -> None:
    downstream_calls = 0

    def fail() -> tuple[int, str]:
        return 1, "EXPECTED_FAILURE\n"

    def downstream() -> tuple[int, str]:
        nonlocal downstream_calls
        downstream_calls += 1
        return 0, "UNEXPECTED\n"

    results = ValidationDAG(
        [
            ValidationNode("fail", (), fail),
            ValidationNode("downstream", ("fail",), downstream),
        ]
    ).run()
    require(results["fail"].status == "FAIL", "failure node did not fail")
    require(results["downstream"].status == "BLOCKED", "downstream was not blocked")
    require(downstream_calls == 0, "blocked node executed")

    for nodes, marker in (
        ([ValidationNode("broken", ("missing",), downstream)], "UNKNOWN_DAG_DEPENDENCY"),
        (
            [
                ValidationNode("x", ("y",), downstream),
                ValidationNode("y", ("x",), downstream),
            ],
            "DAG_CYCLE",
        ),
    ):
        try:
            ValidationDAG(nodes)
        except ValueError as exc:
            require(marker in str(exc), f"wrong DAG error: {exc}")
        else:
            raise AssertionError(marker + " not rejected")
    print("DAG_FAIL_CLOSED=PASS")


def mode_planning_contract() -> None:
    engine_paths = ("scripts/governance_engine.py",)
    engine_impacts = classify_changed_paths(engine_paths)
    require(engine_impacts == ("engine",), f"engine classification drifted: {engine_impacts}")
    require(
        effective_mode("develop", engine_impacts) == "develop",
        "known engine change should remain develop",
    )
    develop_nodes = develop_node_names(engine_paths, engine_impacts)
    require("engine_regression" in develop_nodes, "develop missed engine regression")
    require("strict_workflow_selftest" not in develop_nodes, "develop ran final-only regression")

    source_impacts = classify_changed_paths(("app.py",))
    require(source_impacts == ("source",), f"source classification drifted: {source_impacts}")
    require(effective_mode("develop", source_impacts) == "develop", "known source change should remain develop")
    source_nodes = develop_node_names(("app.py",), source_impacts)
    require("sync_project_truth" in source_nodes, "source develop missed incremental Project Truth sync")

    broad_impacts = classify_changed_paths(("scripts/new_future_validator.py",))
    require(
        effective_mode("develop", broad_impacts) == "verify",
        "unmapped source should escalate develop to verify",
    )

    reference_impacts = classify_changed_paths(("references/governance-and-project-truth.md",))
    require(
        reference_impacts == ("documentation",),
        f"normative reference fragment misclassified: {reference_impacts}",
    )
    require(
        effective_mode("verify", reference_impacts) == "verify",
        "normative reference fragment should not escalate verify to finalize",
    )

    root_doc_impacts = classify_changed_paths(
        (
            "AGENTS.md",
            "CODE_OF_CONDUCT.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "README.md",
            "SECURITY.md",
            "SKILL.md",
        )
    )
    require(
        root_doc_impacts == ("documentation",),
        f"root documentation misclassified: {root_doc_impacts}",
    )
    require(
        effective_mode("verify", root_doc_impacts) == "verify",
        "known root documentation should not escalate verify to finalize",
    )

    support_impacts = classify_changed_paths(
        (".gitattributes", "artifacts/sequence/SW2-02-GOVERNANCE.acceptance.json")
    )
    require(
        support_impacts == ("governance", "sequence"),
        f"known governance/sequence support paths misclassified: {support_impacts}",
    )
    require(
        effective_mode("verify", support_impacts) == "verify",
        "known governance support paths should not force finalize",
    )

    unknown_impacts = classify_changed_paths(("future/new-surface.bin",))
    require(unknown_impacts == ("unknown",), "unknown path was misclassified")
    require(
        effective_mode("develop", unknown_impacts) == "verify",
        "unknown develop impact did not escalate to verify",
    )
    require(
        effective_mode("verify", unknown_impacts) == "finalize",
        "unknown verify impact did not escalate to finalize",
    )

    verify_nodes = set(
        planned_node_names(
            "develop",
            "verify",
            ("scripts/new_future_validator.py",),
            broad_impacts,
        )
    )
    require(
        set(develop_nodes).issubset(verify_nodes),
        "verify is not a superset of known develop checks",
    )
    require(
        set(VERIFY_NODE_NAMES) == verify_nodes,
        "verify node set drifted from declared contract",
    )

    finalize_nodes = set(
        planned_node_names(
            "verify",
            "finalize",
            ("future/new-surface.bin",),
            unknown_impacts,
        )
    )
    verify_without_docs = set(VERIFY_NODE_NAMES) - {"validate_project_docs"}
    require(
        verify_without_docs.issubset(finalize_nodes),
        "finalize lost verify regression/validator coverage",
    )
    require(
        "sync_project_truth" in finalize_nodes,
        "finalize did not replace read-only docs validation with synchronized docs validation",
    )
    for required in (
        "strict_workflow_selftest",
        "sync_project_truth",
        "validate_project_truth",
        "governed_state_clean",
    ):
        require(required in finalize_nodes, f"finalize missing {required}")
    require(
        set(FINALIZE_NODE_NAMES) == finalize_nodes,
        "finalize node set drifted from declared contract",
    )
    print("MODE_PLANNING_CONTRACT=PASS")



def smart_validation_dag_contract() -> None:
    sequence_paths = ("docs/sequence/sessions/SW2-15-GOVERNANCE.json",)
    sequence_impacts = classify_changed_paths(sequence_paths)
    sequence_develop = planned_node_names(
        "develop", "develop", sequence_paths, sequence_impacts
    )
    require(
        sequence_develop == ("compile_scripts", "sequence_regression"),
        f"develop prerequisite closure drifted: {sequence_develop}",
    )
    print("SMART_DEVELOP_IMPACT_SELECTION=PASS")

    ci_paths = (
        ".github/actions/governance-bootstrap/action.yml",
        ".github/scripts/ci_applicability.py",
        ".github/workflows/governance-ci.yml",
    )
    ci_impacts = classify_changed_paths(ci_paths)
    require(ci_impacts == ("ci",), f"CI primitives misclassified: {ci_impacts}")
    require(
        effective_mode("develop", ci_impacts) == "verify",
        f"CI impact did not broaden develop to verify: {ci_impacts}",
    )
    require(
        effective_mode("verify", ci_impacts) == "verify",
        f"known CI impact escalated verify to finalize: {ci_impacts}",
    )
    ci_verify = planned_node_names("verify", "verify", ci_paths, ci_impacts)
    require(
        ci_verify == VERIFY_NODE_NAMES,
        f"CI verify did not retain full verify safety net: {ci_verify}",
    )
    print("CI_IMPACT_CLASSIFICATION=PASS")

    doc_paths = ("references/governance-and-project-truth.md",)
    doc_impacts = classify_changed_paths(doc_paths)
    doc_verify = planned_node_names("verify", "verify", doc_paths, doc_impacts)
    expected_doc_verify = {
        "validate_project_docs",
        "validate_human_comprehension",
        "validate_handoff",
        "validate_cross_document_consistency",
    }
    require(
        set(doc_verify) == expected_doc_verify,
        f"verify documentation closure drifted: {doc_verify}",
    )
    require(
        set(doc_verify).isdisjoint(
            {
                "engine_regression",
                "sequence_regression",
                "cross_document_regression",
                "compiler_selftest",
            }
        ),
        f"verify retained unrelated regressions: {doc_verify}",
    )

    engine_paths = ("scripts/governance_engine.py",)
    engine_impacts = classify_changed_paths(engine_paths)
    engine_verify = set(
        planned_node_names("verify", "verify", engine_paths, engine_impacts)
    )
    require(
        {
            "compile_scripts",
            "engine_regression",
            "validate_project_docs",
            "validate_human_comprehension",
            "validate_handoff",
            "validate_cross_document_consistency",
        }.issubset(engine_verify),
        f"engine verify closure incomplete: {engine_verify}",
    )

    source_paths = ("src/example.py",)
    source_impacts = classify_changed_paths(source_paths)
    source_verify = set(
        planned_node_names("verify", "verify", source_paths, source_impacts)
    )
    require(
        {
            "validate_project_docs",
            "validate_human_comprehension",
            "validate_sequence_sessions",
            "validate_handoff",
            "validate_cross_document_consistency",
        }.issubset(source_verify),
        f"source verify closure incomplete: {source_verify}",
    )
    require(
        len(doc_verify) < len(VERIFY_NODE_NAMES),
        "smart verify did not reduce known documentation work",
    )
    print("SMART_VERIFY_DEPENDENCY_CLOSURE=PASS")

    broad_paths = ("scripts/new_future_validator.py",)
    broad_impacts = classify_changed_paths(broad_paths)
    broad_verify = planned_node_names("develop", "verify", broad_paths, broad_impacts)
    require(
        broad_verify == VERIFY_NODE_NAMES,
        f"broad impact was narrowed unsafely: {broad_verify}",
    )

    adversarial = (
        (
            ("child",),
            {"child": ("missing",)},
            ("child",),
            "UNKNOWN_PLANNER_DEPENDENCY",
        ),
        (
            ("a",),
            {"a": ("b",), "b": ("a",)},
            ("a", "b"),
            "PLANNER_DAG_CYCLE",
        ),
        (
            ("ghost",),
            {"known": ()},
            ("known",),
            "UNKNOWN_PLANNER_NODE",
        ),
    )
    for seeds, dependencies, order, marker_text in adversarial:
        try:
            dependency_closure(seeds, dependencies, order)
        except ValueError as exc:
            require(marker_text in str(exc), f"wrong planner failure: {exc}")
        else:
            raise AssertionError(marker_text + " did not fail closed")
    print("SMART_PLANNER_FAIL_CLOSED=PASS")

    finalize_nodes = planned_node_names(
        "finalize",
        "finalize",
        ("scripts/governance_engine.py",),
        ("engine",),
    )
    require(
        finalize_nodes == FINALIZE_NODE_NAMES,
        f"finalize authoritative DAG was narrowed: {finalize_nodes}",
    )
    print("SMART_FINALIZE_EXHAUSTIVE=PASS")


def changed_path_collection_contract() -> None:
    with tempfile.TemporaryDirectory(prefix="sw2-impact-") as td:
        root = Path(td).resolve()
        git(root, "init")
        git(root, "config", "user.email", "sw2@example.invalid")
        git(root, "config", "user.name", "SW2 Test")
        (root / "tracked.py").write_text("value = 1\n", encoding="utf-8")
        git(root, "add", "tracked.py")
        git(root, "commit", "-m", "base")
        base = git_text(root, "rev-parse", "HEAD")

        (root / "tracked.py").write_text("value = 2\n", encoding="utf-8")
        (root / "untracked.txt").write_text("new\n", encoding="utf-8")
        dirty = set(collect_changed_paths(root, base))
        require("tracked.py" in dirty, "unstaged path missing from impact set")
        require("untracked.txt" in dirty, "untracked path missing from impact set")

        git(root, "add", "tracked.py", "untracked.txt")
        git(root, "commit", "-m", "candidate")
        committed = set(collect_changed_paths(root, base))
        require("tracked.py" in committed, "committed path missing from impact set")
        require("untracked.txt" in committed, "committed new path missing from impact set")
    print("CHANGED_PATH_COLLECTION=PASS")


def mode_cli_integration_contract() -> None:
    tool = Path(__file__).resolve().parent / "governance_engine.py"
    with tempfile.TemporaryDirectory(prefix="sw2-mode-cli-") as td:
        root = Path(td).resolve()
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        git(root, "init")
        git(root, "config", "user.email", "sw2@example.invalid")
        git(root, "config", "user.name", "SW2 Test")
        (scripts / "governance_engine.py").write_text("VALUE = 1\n", encoding="utf-8")
        (scripts / "fixture_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
        (scripts / "selftest_governance_engine.py").write_text(
            "import fixture_helper\nprint('FIXTURE_ENGINE_REGRESSION=PASS')\n",
            encoding="utf-8",
        )
        git(root, "add", "scripts")
        git(root, "commit", "-m", "base")
        base = git_text(root, "rev-parse", "HEAD")

        (scripts / "governance_engine.py").write_text("VALUE = 2\n", encoding="utf-8")
        develop_report = root / "develop-report.json"
        develop = subprocess.run(
            [
                sys.executable,
                str(tool),
                "--root",
                str(root),
                "--base",
                base,
                "--mode",
                "develop",
                "--expected-head",
                base,
                "--report",
                str(develop_report),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        require(develop.returncode == 0, "develop CLI fixture did not pass")
        develop_payload = json.loads(develop_report.read_text(encoding="utf-8"))
        require(develop_payload["requested_mode"] == "develop", "develop request not recorded")
        require(develop_payload["effective_mode"] == "develop", "known impact escalated unexpectedly")
        require(develop_payload["final_acceptance_authority"] is False, "develop gained final authority")
        require(
            set(develop_payload["selected_nodes"]) == {"compile_scripts", "engine_regression"},
            "develop CLI selected the wrong targeted checks",
        )
        require(
            all(node["status"] == "PASS" for node in develop_payload["nodes"].values()),
            "develop CLI targeted node failed",
        )
        require(
            not any(path.name == "__pycache__" for path in scripts.rglob("__pycache__")),
            "develop compile dirtied fixture worktree with __pycache__",
        )

        unknown = root / "future" / "new-surface.bin"
        unknown.parent.mkdir(parents=True)
        unknown.write_bytes(b"unknown")
        verify_report = root / "verify-report.json"
        verify = subprocess.run(
            [
                sys.executable,
                str(tool),
                "--root",
                str(root),
                "--base",
                base,
                "--mode",
                "verify",
                "--expected-head",
                base,
                "--report",
                str(verify_report),
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        require(verify.returncode != 0, "unknown verify fixture produced a false PASS")
        verify_payload = json.loads(verify_report.read_text(encoding="utf-8"))
        require(verify_payload["requested_mode"] == "verify", "verify request not recorded")
        require(verify_payload["effective_mode"] == "finalize", "unknown verify did not escalate")
        require(
            verify_payload["final_acceptance_authority"] is False,
            "verify escalation silently gained final acceptance authority",
        )
        require("unknown" in verify_payload["impact_classes"], "unknown impact not reported")
        require("validate_project_truth" in verify_payload["selected_nodes"], "escalated full graph missing final truth")
    print("MODE_CLI_INTEGRATION=PASS")


def main() -> int:
    snapshot_parity_and_immutability()
    snapshot_fact_reuse()
    dag_executes_once()
    dag_fail_closed()
    mode_planning_contract()
    smart_validation_dag_contract()
    changed_path_collection_contract()
    mode_cli_integration_contract()
    print("GOVERNANCE_ENGINE_SELFTEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

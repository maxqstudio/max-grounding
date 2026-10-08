#!/usr/bin/env python3
"""Governance Engine V2: immutable snapshot, fail-closed DAG, and execution modes.

SW2-02 adds develop, verify, and finalize modes without changing standalone
validator behavior. Fast modes may reduce intermediate work only when impact is
known. Unknown impact escalates. Finalize remains the only complete acceptance
mode and requires an exact expected HEAD.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

# Direct engine execution must not create repository-local __pycache__.
sys.dont_write_bytecode = True

import sync_project_truth
import validate_cross_document_consistency
import validate_handoff
import validate_human_comprehension
import validate_project_docs
import validate_project_truth
import validate_sequence_sessions
from project_snapshot import ProjectSnapshot, SOURCE_EXTENSIONS, active_project_snapshot
from script_runner import invoke_main


@dataclass(frozen=True)
class NodeResult:
    name: str
    status: str
    returncode: int
    seconds: float
    output: str
    blocked_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationNode:
    name: str
    dependencies: tuple[str, ...]
    action: Callable[[], tuple[int, str]]


class ValidationDAG:
    def __init__(self, nodes: list[ValidationNode]) -> None:
        self.nodes: dict[str, ValidationNode] = {}
        for node in nodes:
            if node.name in self.nodes:
                raise ValueError(f"DUPLICATE_DAG_NODE:{node.name}")
            self.nodes[node.name] = node
        for node in nodes:
            for dependency in node.dependencies:
                if dependency not in self.nodes:
                    raise ValueError(
                        f"UNKNOWN_DAG_DEPENDENCY:{node.name}:{dependency}"
                    )
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise ValueError(f"DAG_CYCLE:{name}")
            visiting.add(name)
            for dependency in self.nodes[name].dependencies:
                visit(dependency)
            visiting.remove(name)
            visited.add(name)

        for name in self.nodes:
            visit(name)

    def run(self) -> dict[str, NodeResult]:
        results: dict[str, NodeResult] = {}

        def execute(name: str) -> NodeResult:
            existing = results.get(name)
            if existing is not None:
                return existing
            node = self.nodes[name]
            dependency_results = [execute(dep) for dep in node.dependencies]
            failed = tuple(
                result.name
                for result in dependency_results
                if result.status != "PASS"
            )
            if failed:
                result = NodeResult(
                    name=name,
                    status="BLOCKED",
                    returncode=1,
                    seconds=0.0,
                    output="",
                    blocked_by=failed,
                )
                results[name] = result
                return result

            started = time.perf_counter()
            try:
                code, output = node.action()
            except Exception as exc:
                code = 1
                output = f"DAG_NODE_EXCEPTION:{type(exc).__name__}:{exc}\n"
            result = NodeResult(
                name=name,
                status="PASS" if code == 0 else "FAIL",
                returncode=code,
                seconds=time.perf_counter() - started,
                output=output,
            )
            results[name] = result
            return result

        for name in self.nodes:
            execute(name)
        return results


ENGINE_DEVELOP_FILES = {
    "scripts/governance_engine.py",
    "scripts/project_snapshot.py",
    "scripts/extract_project_facts.py",
    "scripts/script_runner.py",
}
SEQUENCE_DEVELOP_FILES = {
    "scripts/sequence_contract.py",
    "scripts/generate_sequence_actual.py",
    "scripts/generate_sequence_plan.py",
    "scripts/validate_sequence_contract.py",
    "scripts/validate_sequence_sessions.py",
}
COMPILER_DEVELOP_FILES = {
    "scripts/generate_project_docs.py",
    "scripts/sync_project_truth.py",
    "scripts/validate_project_docs.py",
    "scripts/validate_doc_quality.py",
    "scripts/project_profile.py",
    "scripts/project_truth_impact.py",
    "scripts/project_truth_projection_common.py",
    "scripts/project_truth_projection_state.py",
    "scripts/project_truth_projection_code.py",
    "scripts/project_truth_projection_governance.py",
    "scripts/project_truth_projection_contracts.py",
}
CROSSDOC_DEVELOP_FILES = {
    "scripts/validate_cross_document_consistency.py",
    "scripts/validate_human_comprehension.py",
    "scripts/validate_handoff.py",
    "scripts/validate_project_truth.py",
}
DEVELOP_TEST_MAP = {
    "scripts/selftest_governance_engine.py": "engine_regression",
    "scripts/selftest_sequence_call_resolution.py": "sequence_regression",
    "scripts/selftest_cross_document_regressions.py": "cross_document_regression",
    "scripts/selftest_project_truth_compiler.py": "compiler_selftest",
}
VERIFY_NODE_NAMES = (
    "compile_scripts",
    "engine_regression",
    "sequence_regression",
    "cross_document_regression",
    "compiler_selftest",
    "validate_project_docs",
    "validate_human_comprehension",
    "validate_sequence_sessions",
    "validate_handoff",
    "validate_cross_document_consistency",
)
FINALIZE_NODE_NAMES = (
    "compile_scripts",
    "engine_regression",
    "sequence_regression",
    "cross_document_regression",
    "compiler_selftest",
    "strict_workflow_selftest",
    "sync_project_truth",
    "validate_human_comprehension",
    "validate_sequence_sessions",
    "validate_handoff",
    "validate_cross_document_consistency",
    "validate_project_truth",
    "governed_state_clean",
)


DEVELOP_NODE_ORDER = (
    "compile_scripts",
    "engine_regression",
    "sequence_regression",
    "cross_document_regression",
    "compiler_selftest",
    "sync_project_truth",
    "validate_project_docs",
    "validate_cross_document_consistency",
    "impact_only",
)
DEVELOP_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "compile_scripts": (),
    "engine_regression": ("compile_scripts",),
    "sequence_regression": ("compile_scripts",),
    "cross_document_regression": ("compile_scripts",),
    "compiler_selftest": ("compile_scripts",),
    "sync_project_truth": (),
    "validate_project_docs": (),
    "validate_cross_document_consistency": ("sync_project_truth",),
    "impact_only": (),
}
VERIFY_NODE_ORDER = VERIFY_NODE_NAMES + ("impact_only",)
VERIFY_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "compile_scripts": (),
    "engine_regression": ("compile_scripts",),
    "sequence_regression": ("compile_scripts",),
    "cross_document_regression": ("compile_scripts",),
    "compiler_selftest": ("compile_scripts",),
    "validate_project_docs": (),
    "validate_human_comprehension": ("validate_project_docs",),
    "validate_sequence_sessions": (),
    "validate_handoff": ("validate_project_docs",),
    "validate_cross_document_consistency": (
        "validate_human_comprehension",
        "validate_handoff",
    ),
    "impact_only": (),
}
VERIFY_BROAD_IMPACTS = frozenset({"broad_source", "template", "ci"})
VERIFY_IMPACT_NODE_SEEDS: dict[str, tuple[str, ...]] = {
    "engine": ("engine_regression", "validate_cross_document_consistency"),
    "sequence": (
        "sequence_regression",
        "validate_sequence_sessions",
        "validate_cross_document_consistency",
    ),
    "compiler": ("compiler_selftest", "validate_cross_document_consistency"),
    "cross_document": (
        "cross_document_regression",
        "validate_cross_document_consistency",
    ),
    "governance": (
        "validate_sequence_sessions",
        "validate_cross_document_consistency",
    ),
    "documentation": ("validate_cross_document_consistency",),
    "source": (
        "validate_sequence_sessions",
        "validate_cross_document_consistency",
    ),
    "benchmark": (),
}


def dependency_closure(
    seed_names: tuple[str, ...] | list[str],
    dependencies: dict[str, tuple[str, ...]],
    order: tuple[str, ...],
) -> tuple[str, ...]:
    selected: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in selected:
            return
        if name in visiting:
            raise ValueError(f"PLANNER_DAG_CYCLE:{name}")
        if name not in dependencies:
            raise ValueError(f"UNKNOWN_PLANNER_NODE:{name}")
        visiting.add(name)
        for dependency in dependencies[name]:
            if dependency not in dependencies:
                raise ValueError(f"UNKNOWN_PLANNER_DEPENDENCY:{name}:{dependency}")
            visit(dependency)
        visiting.remove(name)
        selected.add(name)

    for seed in seed_names:
        visit(seed)

    missing_order = selected.difference(order)
    if missing_order:
        raise ValueError("PLANNER_ORDER_MISSING:" + ",".join(sorted(missing_order)))
    return tuple(name for name in order if name in selected)


def verify_seed_node_names(
    paths: tuple[str, ...] | list[str],
    impacts: tuple[str, ...],
) -> tuple[str, ...]:
    impact_set = set(impacts)
    if impact_set.intersection(VERIFY_BROAD_IMPACTS):
        return VERIFY_NODE_NAMES
    if "unknown" in impact_set:
        raise ValueError("VERIFY_UNKNOWN_IMPACT_REQUIRES_FINALIZE")

    seeds: set[str] = set()
    handled = set(VERIFY_IMPACT_NODE_SEEDS) | {"targeted_test"}
    unhandled = impact_set.difference(handled)
    if unhandled:
        raise ValueError("UNMAPPED_VERIFY_IMPACT:" + ",".join(sorted(unhandled)))

    for impact in sorted(impact_set):
        seeds.update(VERIFY_IMPACT_NODE_SEEDS.get(impact, ()))

    if "targeted_test" in impact_set:
        mapped = {
            node_name
            for path, node_name in DEVELOP_TEST_MAP.items()
            if path in set(paths)
        }
        if not mapped:
            raise ValueError("TARGETED_TEST_WITHOUT_VERIFY_NODE")
        seeds.update(mapped)
        seeds.add("validate_cross_document_consistency")

    if not seeds:
        seeds.add("impact_only")
    return tuple(name for name in VERIFY_NODE_ORDER if name in seeds)


def verify_node_names(
    paths: tuple[str, ...] | list[str],
    impacts: tuple[str, ...],
) -> tuple[str, ...]:
    seeds = verify_seed_node_names(paths, impacts)
    return dependency_closure(seeds, VERIFY_DEPENDENCIES, VERIFY_NODE_ORDER)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def git_z(root: Path, *args: str) -> list[str]:
    payload = subprocess.check_output(
        ["git", "-C", str(root), *args],
        stderr=subprocess.STDOUT,
    )
    return [
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in payload.split(b"\0")
        if item
    ]


def state_base(root: Path) -> str:
    path = root / ".workflow" / "state.json"
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return str(data.get("last_accepted_sha", "")).strip()


def collect_changed_paths(root: Path, base: str) -> tuple[str, ...]:
    """Collect committed, staged, unstaged, and untracked paths relative to root."""
    paths: set[str] = set()
    queries = (
        ("diff", "--name-only", "-z", "--diff-filter=ACMRD", f"{base}...HEAD"),
        ("diff", "--name-only", "-z"),
        ("diff", "--cached", "--name-only", "-z"),
        ("ls-files", "--others", "--exclude-standard", "-z"),
    )
    for args in queries:
        try:
            paths.update(git_z(root, *args))
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError(f"CHANGED_PATH_QUERY_FAILED:{args[0]}") from exc
    return tuple(sorted(paths))


def classify_path(path: str) -> str:
    path = path.replace("\\", "/")
    if path in ENGINE_DEVELOP_FILES:
        return "engine"
    if path in SEQUENCE_DEVELOP_FILES or path.startswith("docs/sequence/"):
        return "sequence"
    if path in COMPILER_DEVELOP_FILES:
        return "compiler"
    if path in CROSSDOC_DEVELOP_FILES:
        return "cross_document"
    if path in DEVELOP_TEST_MAP:
        return "targeted_test"
    if path in {"PROJECT_PROFILE.yaml", ".gitattributes"} or path.startswith(".workflow/"):
        return "governance"
    if path.startswith("artifacts/sequence/"):
        return "sequence"
    if (
        path in {
            "AGENTS.md",
            "CODE_OF_CONDUCT.md",
            "CONTRIBUTING.md",
            "LICENSE",
            "README.md",
            "SECURITY.md",
            "SKILL.md",
        }
        or path.startswith("docs/")
        or path.startswith("references/")
    ):
        return "documentation"
    if path.startswith("benchmarks/"):
        return "benchmark"
    if path.startswith("templates/"):
        return "template"
    if path.startswith(".github/"):
        return "ci"
    if path.startswith("scripts/"):
        return "broad_source"
    if Path(path).suffix.lower() in SOURCE_EXTENSIONS:
        return "source"
    return "unknown"


def classify_changed_paths(paths: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(sorted({classify_path(path) for path in paths}))


def effective_mode(requested: str, impacts: tuple[str, ...]) -> str:
    impact_set = set(impacts)
    if requested == "develop" and impact_set.intersection(
        {"unknown", "broad_source", "template", "ci"}
    ):
        return "verify"
    if requested == "verify" and "unknown" in impact_set:
        return "finalize"
    return requested


def develop_node_names(
    paths: tuple[str, ...] | list[str],
    impacts: tuple[str, ...],
) -> tuple[str, ...]:
    names: set[str] = set()
    path_set = set(paths)
    if any(path.startswith("scripts/") for path in path_set):
        names.add("compile_scripts")
    if path_set.intersection(ENGINE_DEVELOP_FILES):
        names.add("engine_regression")
    if path_set.intersection(SEQUENCE_DEVELOP_FILES) or any(
        path.startswith("docs/sequence/") for path in path_set
    ):
        names.add("sequence_regression")
    if path_set.intersection(COMPILER_DEVELOP_FILES):
        names.add("compiler_selftest")
    if path_set.intersection(CROSSDOC_DEVELOP_FILES):
        names.add("cross_document_regression")
    for path, node_name in DEVELOP_TEST_MAP.items():
        if path in path_set:
            names.add(node_name)
    impact_set = set(impacts)
    if impact_set.intersection({"governance", "documentation", "source"}):
        names.add("sync_project_truth")
    if "documentation" in impact_set:
        names.add("validate_cross_document_consistency")
    if "benchmark" in impact_set and not names:
        names.add("impact_only")
    if not names:
        names.add("impact_only")
    ordered = [name for name in DEVELOP_NODE_ORDER if name in names]
    return dependency_closure(
        tuple(ordered),
        DEVELOP_DEPENDENCIES,
        DEVELOP_NODE_ORDER,
    )


def planned_node_names(
    requested: str,
    effective: str,
    paths: tuple[str, ...],
    impacts: tuple[str, ...],
) -> tuple[str, ...]:
    if effective == "finalize":
        return FINALIZE_NODE_NAMES
    if effective == "verify":
        return verify_node_names(paths, impacts)
    return develop_node_names(paths, impacts)


def governed_status(root: Path) -> tuple[int, str]:
    status = git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        "PROJECT_PROFILE.yaml",
        ".workflow",
        "docs",
    )
    if status:
        return 1, "GOVERNED_STATE_DIRTY\n" + status + "\n"
    return 0, "GOVERNED_STATE_CLEAN\n"


def cli_action(
    main_func: Callable[[], int | None],
    argv: list[str],
    program: str,
) -> Callable[[], tuple[int, str]]:
    return lambda: invoke_main(main_func, argv, program=program)


def command_action(root: Path, argv: list[str]) -> Callable[[], tuple[int, str]]:
    def run() -> tuple[int, str]:
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = subprocess.run(
            argv,
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        return completed.returncode, completed.stdout

    return run


def compile_scripts_action(root: Path) -> Callable[[], tuple[int, str]]:
    """Compile Python sources without materializing bytecode in the governed worktree."""
    def run() -> tuple[int, str]:
        with tempfile.TemporaryDirectory(prefix="skill-workflow-pycache-") as td:
            env = os.environ.copy()
            env["PYTHONPYCACHEPREFIX"] = td
            completed = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", "scripts"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
        return completed.returncode, completed.stdout

    return run


def _regression_node(name: str, root: Path, script: str) -> ValidationNode:
    return ValidationNode(
        name=name,
        dependencies=("compile_scripts",),
        action=command_action(root, [sys.executable, script]),
    )


def build_mode_dag(
    root: Path,
    *,
    base: str,
    mode: str,
    node_names: tuple[str, ...],
    changed_paths: tuple[str, ...] = (),
) -> ValidationDAG:
    wanted = set(node_names)
    nodes: list[ValidationNode] = []

    if "impact_only" in wanted:
        nodes.append(
            ValidationNode(
                name="impact_only",
                dependencies=(),
                action=lambda: (0, "IMPACT_CLASSIFIED_NO_EXECUTABLE_CHECK_REQUIRED\n"),
            )
        )

    if "compile_scripts" in wanted:
        nodes.append(
            ValidationNode(
                name="compile_scripts",
                dependencies=(),
                action=compile_scripts_action(root),
            )
        )

    regression_specs = (
        ("engine_regression", "scripts/selftest_governance_engine.py"),
        ("sequence_regression", "scripts/selftest_sequence_call_resolution.py"),
        ("cross_document_regression", "scripts/selftest_cross_document_regressions.py"),
        ("compiler_selftest", "scripts/selftest_project_truth_compiler.py"),
    )
    for name, script in regression_specs:
        if name in wanted:
            nodes.append(_regression_node(name, root, script))

    regressions = tuple(
        name for name, _ in regression_specs if name in wanted
    )
    if "strict_workflow_selftest" in wanted:
        dependencies = regressions or (
            ("compile_scripts",) if "compile_scripts" in wanted else ()
        )
        nodes.append(
            ValidationNode(
                name="strict_workflow_selftest",
                dependencies=dependencies,
                action=command_action(
                    root,
                    [sys.executable, "scripts/selftest_strict_project_workflow.py"],
                ),
            )
        )

    if "sync_project_truth" in wanted:
        dependencies = (
            ("strict_workflow_selftest",)
            if "strict_workflow_selftest" in wanted
            else regressions
        )
        sync_args = ["--root", str(root)]
        if mode != "finalize":
            sync_args.append("--incremental")
            for path in changed_paths:
                sync_args.extend(["--changed-path", path])
        nodes.append(
            ValidationNode(
                name="sync_project_truth",
                dependencies=dependencies,
                action=cli_action(
                    sync_project_truth.main,
                    sync_args,
                    "sync_project_truth.py",
                ),
            )
        )
        docs_dependency = "sync_project_truth"
    elif "validate_project_docs" in wanted:
        dependencies = regressions
        validate_docs_args = ["--root", str(root)]
        if mode == "verify":
            validate_docs_args.append("--incremental")
            for path in changed_paths:
                validate_docs_args.extend(["--changed-path", path])
        nodes.append(
            ValidationNode(
                name="validate_project_docs",
                dependencies=dependencies,
                action=cli_action(
                    validate_project_docs.main,
                    validate_docs_args,
                    "validate_project_docs.py",
                ),
            )
        )
        docs_dependency = "validate_project_docs"
    else:
        docs_dependency = ""

    if "validate_human_comprehension" in wanted:
        nodes.append(
            ValidationNode(
                name="validate_human_comprehension",
                dependencies=((docs_dependency,) if docs_dependency else regressions),
                action=cli_action(
                    validate_human_comprehension.main,
                    ["--root", str(root), "--require-pass"],
                    "validate_human_comprehension.py",
                ),
            )
        )

    if "validate_sequence_sessions" in wanted:
        nodes.append(
            ValidationNode(
                name="validate_sequence_sessions",
                dependencies=((docs_dependency,) if docs_dependency else regressions),
                action=cli_action(
                    validate_sequence_sessions.main,
                    ["--root", str(root)],
                    "validate_sequence_sessions.py",
                ),
            )
        )

    if "validate_handoff" in wanted:
        nodes.append(
            ValidationNode(
                name="validate_handoff",
                dependencies=((docs_dependency,) if docs_dependency else regressions),
                action=cli_action(
                    validate_handoff.main,
                    ["--root", str(root)],
                    "validate_handoff.py",
                ),
            )
        )

    if "validate_cross_document_consistency" in wanted:
        dependencies: list[str] = []
        if "validate_human_comprehension" in wanted:
            dependencies.append("validate_human_comprehension")
        elif docs_dependency:
            dependencies.append(docs_dependency)
        elif regressions:
            dependencies.extend(regressions)
        nodes.append(
            ValidationNode(
                name="validate_cross_document_consistency",
                dependencies=tuple(dependencies),
                action=cli_action(
                    validate_cross_document_consistency.main,
                    ["--root", str(root), "--base", base, "--require-base"],
                    "validate_cross_document_consistency.py",
                ),
            )
        )

    if "validate_project_truth" in wanted:
        dependencies = tuple(
            name
            for name in (
                "validate_cross_document_consistency",
                "validate_sequence_sessions",
                "validate_handoff",
            )
            if name in wanted
        )
        nodes.append(
            ValidationNode(
                name="validate_project_truth",
                dependencies=dependencies,
                action=cli_action(
                    validate_project_truth.main,
                    [
                        "--root", str(root),
                        "--project-docs-already-validated",
                    ],
                    "validate_project_truth.py",
                ),
            )
        )

    if "governed_state_clean" in wanted:
        nodes.append(
            ValidationNode(
                name="governed_state_clean",
                dependencies=("validate_project_truth",),
                action=lambda: governed_status(root),
            )
        )

    return ValidationDAG(nodes)


def build_dag(root: Path, *, base: str, sync: bool) -> ValidationDAG:
    """Legacy SW2-01 orchestration retained for compatibility."""
    nodes: list[ValidationNode] = []
    if sync:
        nodes.append(
            ValidationNode(
                name="sync_project_truth",
                dependencies=(),
                action=cli_action(
                    sync_project_truth.main,
                    ["--root", str(root)],
                    "sync_project_truth.py",
                ),
            )
        )
        docs_dependency = "sync_project_truth"
    else:
        nodes.append(
            ValidationNode(
                name="validate_project_docs",
                dependencies=(),
                action=cli_action(
                    validate_project_docs.main,
                    ["--root", str(root)],
                    "validate_project_docs.py",
                ),
            )
        )
        docs_dependency = "validate_project_docs"

    nodes.append(
        ValidationNode(
            name="validate_human_comprehension",
            dependencies=(docs_dependency,),
            action=cli_action(
                validate_human_comprehension.main,
                ["--root", str(root), "--require-pass"],
                "validate_human_comprehension.py",
            ),
        )
    )
    nodes.append(
        ValidationNode(
            name="validate_cross_document_consistency",
            dependencies=("validate_human_comprehension",),
            action=cli_action(
                validate_cross_document_consistency.main,
                ["--root", str(root), "--base", base, "--require-base"],
                "validate_cross_document_consistency.py",
            ),
        )
    )
    nodes.append(
        ValidationNode(
            name="validate_project_truth",
            dependencies=("validate_cross_document_consistency",),
            action=cli_action(
                validate_project_truth.main,
                [
                    "--root", str(root),
                    "--project-docs-already-validated",
                ],
                "validate_project_truth.py",
            ),
        )
    )
    if sync:
        nodes.append(
            ValidationNode(
                name="governed_state_clean",
                dependencies=("validate_project_truth",),
                action=lambda: governed_status(root),
            )
        )
    return ValidationDAG(nodes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--base", default="")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument("--mode", choices=("develop", "verify", "finalize"), default="")
    parser.add_argument("--expected-head", default="")
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    base = args.base.strip() or state_base(root)
    if not base:
        print("FAIL BASE_SHA_REQUIRED")
        return 2
    if args.mode and args.sync:
        print("FAIL MODE_AND_LEGACY_SYNC_ARE_MUTUALLY_EXCLUSIVE")
        return 2

    snapshot = ProjectSnapshot.capture(root)
    expected_head = args.expected_head.strip()
    failures: list[str] = []
    changed_paths: tuple[str, ...] = ()
    impacts: tuple[str, ...] = ()
    requested_mode = args.mode or "legacy"
    selected_mode = requested_mode
    selected_nodes: tuple[str, ...] = ()

    if expected_head and snapshot.git_head != expected_head:
        failures.append(
            f"PROVENANCE_MISMATCH:observed={snapshot.git_head}:expected={expected_head}"
        )
    elif args.mode:
        try:
            changed_paths = collect_changed_paths(root, base)
        except ValueError as exc:
            failures.append(str(exc))
        if not failures:
            try:
                impacts = classify_changed_paths(changed_paths)
                selected_mode = effective_mode(args.mode, impacts)
                if selected_mode == "finalize" and not expected_head:
                    failures.append("FINALIZE_EXPECTED_HEAD_REQUIRED")
                else:
                    selected_nodes = planned_node_names(
                        args.mode,
                        selected_mode,
                        changed_paths,
                        impacts,
                    )
            except ValueError as exc:
                failures.append(str(exc))

    if failures:
        report = {
            "schema_version": 2,
            "result": "FAIL",
            "failures": failures,
            "base_sha": base,
            "expected_head": expected_head or "NOT_DECLARED",
            "requested_mode": requested_mode,
            "effective_mode": selected_mode,
            "changed_paths": list(changed_paths),
            "impact_classes": list(impacts),
            "selected_nodes": list(selected_nodes),
            "final_acceptance_authority": requested_mode == "finalize" and selected_mode == "finalize",
            "snapshot": snapshot.metrics(),
            "nodes": {},
        }
    else:
        with active_project_snapshot(snapshot):
            try:
                if args.mode:
                    results = build_mode_dag(
                        root,
                        base=base,
                        mode=selected_mode,
                        node_names=selected_nodes,
                        changed_paths=changed_paths,
                    ).run()
                else:
                    results = build_dag(root, base=base, sync=args.sync).run()
            except Exception as exc:
                print(f"FAIL ENGINE_DAG_ERROR:{type(exc).__name__}:{exc}")
                return 2

        failures = [
            name for name, result in results.items() if result.status != "PASS"
        ]
        report = {
            "schema_version": 2,
            "result": "FAIL" if failures else "PASS",
            "failures": failures,
            "base_sha": base,
            "expected_head": expected_head or "NOT_DECLARED",
            "requested_mode": requested_mode,
            "effective_mode": selected_mode,
            "changed_paths": list(changed_paths),
            "impact_classes": list(impacts),
            "selected_nodes": list(selected_nodes),
            "final_acceptance_authority": bool(args.mode == "finalize" and selected_mode == "finalize"),
            "snapshot": snapshot.metrics(),
            "nodes": {
                name: {
                    "status": result.status,
                    "returncode": result.returncode,
                    "seconds": result.seconds,
                    "blocked_by": list(result.blocked_by),
                    "output_tail": result.output.splitlines()[-20:],
                }
                for name, result in results.items()
            },
            "boundary": (
                "Develop and verify are intermediate evidence only. Unknown impact escalates. "
                "Finalize is the only complete mode and still requires exact candidate source, "
                "all required tests/validators, synchronized governed state, and final truth."
                if args.mode
                else
                "Legacy SW2-01 orchestration retained for compatibility. Snapshot and memoization "
                "accelerate one process only; final acceptance authority remains exact candidate "
                "source plus all required test/validator evidence."
            ),
        }

    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.report:
        path = Path(args.report)
        if not path.is_absolute():
            path = root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
    return 1 if report["result"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())

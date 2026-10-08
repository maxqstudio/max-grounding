#!/usr/bin/env python3
"""Regression tests for the SW2-20 documentation inventory/freshness contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import validate_documentation_contract as contract


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def write(root: Path, path: str, text: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")


def write_json(root: Path, path: str, value: object) -> None:
    write(root, path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def fixture_root() -> tempfile.TemporaryDirectory[str]:
    td = tempfile.TemporaryDirectory(prefix="sw2-doc-contract-")
    root = Path(td.name).resolve()
    run_git(root, "init")
    run_git(root, "config", "user.email", "sw2@example.invalid")
    run_git(root, "config", "user.name", "SW2 Test")

    write(root, "README.md", "# Demo\n\nCurrent stable release is `v2.1.0`.\n")
    write(root, "AGENTS.md", "# Agent contract\n\nCurrent phase is SW2-20.\n")
    write(root, "SKILL.md", "# Skill\n\nStable governance guidance.\n")
    write(root, "CONTRIBUTING.md", "# Contributing\n\nFollow repository governance.\n")
    write(root, "SECURITY.md", "# Security\n\nReport supported-release issues privately.\n")
    write(root, "CODE_OF_CONDUCT.md", "# Code of Conduct\n")
    write(root, "LICENSE", "MIT License\n")
    write(root, "references/execution.md", "# Execution reference\n")
    write(root, "templates/AGENTS.md", "# Agent template\n")
    write(root, "docs/README.md", "# Documentation\n")
    write(root, "docs/handbook/reference/versioning.md", "# Versioning\n\nAccepted stable release is `v2.1.0`.\n")
    write(root, "docs/handbook/architecture/system.md", "# Architecture\n")
    write(root, "docs/CURRENT_STATE.md", "# Generated current state\n")
    write(root, "docs/sequence/views/FIXTURE.md", "# Sequence view\n")
    write(root, "docs/sequence/generated/FIXTURE.mmd", "flowchart TD\n  A --> B\n")
    write_json(root, ".workflow/roadmap.json", {"current_phase": "SW2-20"})
    write_json(
        root,
        ".workflow/claims.json",
        {
            "claims": [
                {
                    "id": "TRUTH-SW2-MERGE-ENFORCEMENT",
                    "status": "PASS",
                    "claim": "The Owner-approved boundary uses no GitHub ruleset and makes no automatic enforcement claim.",
                }
            ]
        },
    )
    run_git(root, "add", "-A")
    run_git(root, "commit", "-m", "fixture")
    run_git(root, "tag", "v2.1.0")
    return td


def kinds(report: dict) -> set[str]:
    return {str(item.get("kind", "")) for item in report.get("freshness_failures", [])}


def baseline_inventory_contract() -> None:
    with fixture_root() as td:
        root = Path(td)
        report = contract.build_report(root)
        require(report["result"] == "PASS", f"baseline failed: {report['freshness_failures']}")
        require(report["authority"]["stable_release"] == "v2.1.0", "stable tag authority drifted")
        require(report["authority"]["current_phase"] == "SW2-20", "phase authority drifted")
        require(report["authority"]["license_present"] is True, "license authority drifted")
        require(report["authority"]["no_ruleset_boundary"] is True, "ruleset boundary drifted")
        require(all(item["role"] != "UNCLASSIFIED" for item in report["items"]), "baseline has unclassified docs")
        require(len({item["path"] for item in report["items"]}) == len(report["items"]), "duplicate inventory path")
    print("DOCUMENTATION_BASELINE_INVENTORY=PASS")


def stale_semantic_regressions() -> None:
    with fixture_root() as td:
        root = Path(td)
        cases = (
            ("README.md", "# Demo\n\nCurrent stable release is `v2.0.0`.\n", "STALE_STABLE_RELEASE"),
            ("CONTRIBUTING.md", "# Contributing\n\nThe default branch main enforces required checks and blocks deletion.\n", "STALE_MERGE_ENFORCEMENT"),
            ("SECURITY.md", "# Security\n\nUntil a versioned stable release exists, report all findings privately.\n", "STALE_PRE_RELEASE_STATE"),
            ("docs/handbook/reference/versioning.md", "# Versioning\n\nAccepted stable release is `v2.0.0`.\n", "STALE_STABLE_RELEASE"),
            ("docs/handbook/reference/versioning.md", "# Versioning\n\nProducer repository and commit fields are provenance hints.\n", "STALE_TOOLCHAIN_PROVENANCE"),
            ("AGENTS.md", "# Agent contract\n\nCurrently the project is SW2-19.\n", "STALE_CURRENT_PHASE"),
            ("CONTRIBUTING.md", "# Contributing\n\nThe public license has not yet been selected.\n", "STALE_LICENSE_STATE"),
            ("CONTRIBUTING.md", "# Contributing\n\nThis repository does not currently declare an Owner-approved public license.\n", "STALE_LICENSE_STATE"),
        )
        originals = {path: (root / path).read_text(encoding="utf-8") for path, _, _ in cases}
        for path, stale_text, expected_kind in cases:
            (root / path).write_text(stale_text, encoding="utf-8", newline="\n")
            report = contract.build_report(root)
            require(expected_kind in kinds(report), f"{expected_kind} not detected for {path}: {report['freshness_failures']}")
            (root / path).write_text(originals[path], encoding="utf-8", newline="\n")
    print("DOCUMENTATION_STALE_SEMANTICS=PASS cases=7")


def coverage_drift_contract() -> None:
    tool = Path(contract.__file__).resolve()
    with fixture_root() as td:
        root = Path(td)
        report_path = root / contract.REPORT_PATH
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(contract.report_text(contract.build_report(root)), encoding="utf-8", newline="\n")

        def expect_stale(label: str) -> None:
            completed = subprocess.run(
                [sys.executable, str(tool), "--root", str(root)],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            require(completed.returncode != 0, f"{label} produced false PASS")
            require("DOCUMENTATION_COVERAGE_REPORT_STALE" in completed.stdout, f"{label} wrong failure: {completed.stdout}")

        write(root, "docs/handbook/new-page.md", "# New page\n")
        run_git(root, "add", "docs/handbook/new-page.md")
        expect_stale("add")
        run_git(root, "reset", "--hard", "HEAD")

        run_git(root, "rm", "docs/handbook/architecture/system.md")
        expect_stale("delete")
        run_git(root, "reset", "--hard", "HEAD")

        run_git(root, "mv", "docs/handbook/architecture/system.md", "docs/handbook/architecture/system-renamed.md")
        expect_stale("rename")
        run_git(root, "reset", "--hard", "HEAD")

        (root / "docs/handbook/moved").mkdir(parents=True, exist_ok=True)
        run_git(root, "mv", "docs/handbook/architecture/system.md", "docs/handbook/moved/system.md")
        expect_stale("move")
    print("DOCUMENTATION_COVERAGE_DRIFT=PASS add_delete_move_rename=4")


def unclassified_surface_contract() -> None:
    with fixture_root() as td:
        root = Path(td)
        write(root, "misc/guide.md", "# Unclassified\n")
        run_git(root, "add", "misc/guide.md")
        report = contract.build_report(root)
        require(report["result"] == "FAIL", "unclassified documentation produced false PASS")
        require("UNCLASSIFIED_DOCUMENTATION" in kinds(report), f"wrong unclassified failure: {report['freshness_failures']}")
    print("DOCUMENTATION_UNCLASSIFIED_REJECTION=PASS")


def main() -> int:
    baseline_inventory_contract()
    stale_semantic_regressions()
    coverage_drift_contract()
    unclassified_surface_contract()
    print("DOCUMENTATION_CONTRACT_SELFTEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

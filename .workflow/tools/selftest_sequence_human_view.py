#!/usr/bin/env python3
"""Regression tests for Sequence V2 human projection and fidelity."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from sequence_contract import compute_source_digest, git_head, write_json


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
            "unexpected command result\n"
            + " ".join(args)
            + f"\nexpected={expect} actual={proc.returncode}\n"
            + proc.stdout
        )
    return proc.stdout


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    generator = skill_root / "scripts" / "sequence_human_view.py"
    validator = skill_root / "scripts" / "validate_sequence_human_view.py"

    with tempfile.TemporaryDirectory(prefix="skill-workflow-human-sequence-") as td:
        root = Path(td)
        run(root, "git", "init", "--quiet")
        run(root, "git", "config", "user.email", "skill-workflow-selftest@example.invalid")
        run(root, "git", "config", "user.name", "Skill Workflow Selftest")

        (root / "app.py").write_text(
            "def entry():\n    helper()\n\ndef helper():\n    return 1\n",
            encoding="utf-8",
        )
        (root / "service.py").write_text(
            "def run():\n    return 2\n",
            encoding="utf-8",
        )
        run(root, "git", "add", ".")
        run(root, "git", "commit", "--quiet", "-m", "fixture")

        source_digest = compute_source_digest(root)
        head = git_head(root)
        actual = {
            "schema_version": 1,
            "generated": True,
            "generated_by": "generate_sequence_actual.py",
            "observed_head": head,
            "source_digest": source_digest,
            "entries": ["app.py::entry"],
            "nodes": [
                {"id": "HTTP /x", "label": "HTTP /x", "locator": "HTTP /x", "kind": "external", "language": "HTTP"},
                {"id": "app.py::entry", "label": "app.py::entry", "locator": "app.py::entry", "kind": "function", "language": "Python"},
                {"id": "app.py::helper", "label": "app.py::helper", "locator": "app.py::helper", "kind": "function", "language": "Python"},
                {"id": "service.py::run", "label": "service.py::run", "locator": "service.py::run", "kind": "function", "language": "Python"}
            ],
            "edges": [
                {"from": "HTTP /x", "to": "app.py::entry", "action": "GET route", "evidence": "STATIC", "resolver": "python_route_decorator"},
                {"from": "app.py::entry", "to": "app.py::helper", "action": "helper", "evidence": "STATIC", "resolver": "python_ast"},
                {"from": "app.py::entry", "to": "service.py::run", "action": "service.run", "evidence": "STATIC", "resolver": "python_ast"},
                {"from": "app.py::helper", "to": "service.py::run", "action": "service.run", "evidence": "STATIC", "resolver": "python_ast"}
            ],
            "coverage": {"python_ast": True, "runtime_trace": False, "limitations": []}
        }

        actual_json = root / "docs" / "sequence" / "generated" / "TEST.actual.json"
        actual_mmd = root / "docs" / "sequence" / "generated" / "TEST.actual.mmd"
        human_json = root / "docs" / "sequence" / "generated" / "TEST.human.json"
        human_md = root / "docs" / "sequence" / "views" / "TEST.md"
        session_json = root / "docs" / "sequence" / "sessions" / "TEST.json"

        write_json(actual_json, actual)
        actual_mmd.parent.mkdir(parents=True, exist_ok=True)
        actual_mmd.write_text("sequenceDiagram\n", encoding="utf-8", newline="\n")
        machine_before = sha256(actual_json)

        output = run(
            root,
            sys.executable,
            str(generator),
            "--root", str(root),
            "--actual-json", "docs/sequence/generated/TEST.actual.json",
            "--actual-mermaid", "docs/sequence/generated/TEST.actual.mmd",
            "--output-json", "docs/sequence/generated/TEST.human.json",
            "--output-mermaid", "docs/sequence/generated/TEST.human.mmd",
            "--output-markdown", "docs/sequence/views/TEST.md",
            "--session-id", "TEST"
        )
        if "RESULT=PASS" not in output:
            raise RuntimeError("human generator did not report PASS\n" + output)
        if sha256(actual_json) != machine_before:
            raise RuntimeError("human projection mutated machine graph")

        human = json.loads(human_json.read_text(encoding="utf-8"))
        projection = human["projection"]
        complexity = human["complexity"]
        if projection["machine_node_count"] != 4 or projection["machine_edge_count"] != 4:
            raise RuntimeError("machine evidence counts changed")
        if projection["internal_machine_edges_collapsed"] != 1:
            raise RuntimeError("internal edge collapse count mismatch")
        if projection["cross_component_machine_edges"] != 3:
            raise RuntimeError("cross-component edge count mismatch")
        if projection["aggregated_cross_component_edges"] != 1:
            raise RuntimeError("cross-component aggregation count mismatch")
        if complexity["participant_count"] != 3:
            raise RuntimeError("expected three semantic participants")
        if complexity["interaction_count"] != 2:
            raise RuntimeError("expected two directed component interactions")
        if complexity["absolute_numeric_limit"] is not None:
            raise RuntimeError("policy invented an absolute numeric limit")

        app_service = [
            edge for edge in human["edges"]
            if edge["from"] == "module:app.py" and edge["to"] == "module:service.py"
        ]
        if len(app_service) != 1 or app_service[0]["machine_edge_count"] != 2:
            raise RuntimeError("repeated helper edges were not aggregated losslessly")

        markdown = human_md.read_text(encoding="utf-8")
        if "```mermaid\n" not in markdown:
            raise RuntimeError("GitHub Mermaid block missing")
        participant_lines = [line for line in markdown.splitlines() if line.strip().startswith(("participant ", "actor "))]
        if any("::" in line for line in participant_lines):
            raise RuntimeError("human diagram leaked raw symbol-level participants")
        if "does **not** prove runtime ordering" not in markdown:
            raise RuntimeError("human evidence boundary missing")

        session = {
            "schema_version": 1,
            "session_id": "TEST",
            "phase": "TEST",
            "mode": "DURING",
            "scope": "CURRENT",
            "critical": True,
            "actual": {
                "graph": "docs/sequence/generated/TEST.actual.json",
                "diagram": "docs/sequence/generated/TEST.actual.mmd",
                "entries": ["app.py::entry"],
                "source_digest": source_digest
            },
            "human_view": {
                "document": "docs/sequence/views/TEST.md",
                "diagram": "docs/sequence/generated/TEST.human.mmd",
                "graph": "docs/sequence/generated/TEST.human.json",
                "granularity": "module",
                "policy_id": "module-collapse-v1",
                "source_digest": source_digest
            }
        }
        write_json(session_json, session)
        output = run(
            root,
            sys.executable,
            str(validator),
            "--root", str(root),
            "--session", "docs/sequence/sessions/TEST.json"
        )
        report = json.loads(output)
        if report.get("result") != "PASS":
            raise RuntimeError("human validator did not pass deterministic fixture")

        tampered = json.loads(human_json.read_text(encoding="utf-8"))
        tampered["complexity"]["participant_count"] += 1
        write_json(human_json, tampered)
        output = run(
            root,
            sys.executable,
            str(validator),
            "--root", str(root),
            "--session", "docs/sequence/sessions/TEST.json",
            expect=1
        )
        report = json.loads(output)
        if "HUMAN_GRAPH_NOT_DETERMINISTIC_PROJECTION" not in report.get("failures", []):
            raise RuntimeError("tampered human graph was not rejected")

    print("MACHINE_EVIDENCE_IMMUTABILITY=PASS")
    print("SEMANTIC_MODULE_COLLAPSE=PASS")
    print("DIRECTED_PAIR_AGGREGATION=PASS")
    print("DERIVED_COMPLEXITY_POLICY=PASS")
    print("HUMAN_VIEW_TAMPER_REJECTION=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Regression test for conservative static call resolution.

A method called on an unresolved expression receiver, such as
Path(...).resolve(), must not be rebound to an unrelated project function that
happens to share the same leaf name.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(root: Path, *args: str) -> None:
    proc = subprocess.run(
        list(args),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("command failed\n" + " ".join(args) + "\n" + proc.stdout)


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    generator = skill_root / "scripts" / "generate_sequence_actual.py"
    extractor = skill_root / "scripts" / "extract_project_facts.py"

    with tempfile.TemporaryDirectory(prefix="skill-workflow-call-resolution-") as td:
        root = Path(td)
        run(root, "git", "init", "--quiet")
        run(root, "git", "config", "user.email", "skill-workflow-selftest@example.invalid")
        run(root, "git", "config", "user.name", "Skill Workflow Selftest")

        (root / "app.py").write_text(
            "from pathlib import Path\n\n"
            "def entry():\n"
            "    return Path('.').resolve()\n",
            encoding="utf-8",
        )
        (root / "unrelated.py").write_text(
            "def resolve():\n"
            "    return 'unrelated'\n",
            encoding="utf-8",
        )
        run(root, "git", "add", ".")
        run(root, "git", "commit", "--quiet", "-m", "test fixture")

        actual_json = root / "actual.json"
        actual_mmd = root / "actual.mmd"
        run(
            root,
            sys.executable,
            str(generator),
            "--root",
            str(root),
            "--output-json",
            str(actual_json),
            "--output-mermaid",
            str(actual_mmd),
            "--entry",
            "app.py::entry",
        )

        actual = json.loads(actual_json.read_text(encoding="utf-8"))
        false_edge = any(
            edge.get("from") == "app.py::entry"
            and edge.get("to") == "unrelated.py::resolve"
            for edge in actual.get("edges", [])
        )
        if false_edge:
            raise RuntimeError("expression receiver created false global resolve edge")

        facts_json = root / "facts.json"
        run(
            root,
            sys.executable,
            str(extractor),
            "--root",
            str(root),
            "--output",
            str(facts_json),
        )
        facts = json.loads(facts_json.read_text(encoding="utf-8"))
        false_token = any(
            call.get("caller") == "app.py::entry"
            and call.get("target_token") == "resolve"
            for call in facts.get("python_calls", [])
        )
        if false_token:
            raise RuntimeError("expression receiver degraded to global resolve token")

    print("EXPRESSION_RECEIVER_FALSE_EDGE_REJECTED=PASS")
    print("EXPRESSION_RECEIVER_FALSE_TOKEN_REJECTED=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Synchronize Project Truth Compiler outputs and record PROJECT_DOCS_SYNC.

This script is intentionally narrow:
- generate deterministic docs/facts;
- validate deterministic reproducibility;
- when generated documentation is enabled, record only documentation compiler/quality gates as PASS;
- regenerate and revalidate after recording the gate.

It does not mark semantic, runtime, sequence, or test gates PASS.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from project_profile import (
    PROFILE_FILE,
    documentation_settings,
    parse_profile,
)


def run(cmd: list[str], root: Path) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--no-record", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    profile_path = root / PROFILE_FILE
    if not profile_path.is_file():
        print("FAIL MISSING_PROJECT_PROFILE")
        return 1

    try:
        documentation = documentation_settings(parse_profile(profile_path))
    except Exception as exc:
        print("FAIL PROJECT_PROFILE_INVALID:" + str(exc))
        return 1

    tool_dir = Path(__file__).resolve().parent
    compiler = tool_dir / "generate_project_docs.py"
    validator = tool_dir / "validate_project_docs.py"
    if not compiler.is_file():
        compiler = root / "scripts" / "generate_project_docs.py"
    if not validator.is_file():
        validator = root / "scripts" / "validate_project_docs.py"
    if not compiler.is_file():
        print("FAIL PROJECT_TRUTH_COMPILER_MISSING")
        return 1
    if not validator.is_file():
        print("FAIL PROJECT_DOCS_VALIDATOR_MISSING")
        return 1

    code, output = run(
        [sys.executable, str(compiler), "--root", str(root)],
        root,
    )
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    code, output = run(
        [sys.executable, str(validator), "--root", str(root)],
        root,
    )
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    if not documentation.get("generated", False):
        print("PROJECT_DOCS_SYNC=NOT_APPLICABLE")
        return 0

    if args.no_record:
        print("PROJECT_DOCS_SYNC=PASS")
        return 0

    spec_root = Path(str(documentation.get("spec_root", ".workflow")))
    if not spec_root.is_absolute():
        spec_root = root / spec_root
    acceptance_path = spec_root / "acceptance.json"
    if not acceptance_path.is_file():
        print("FAIL ACCEPTANCE_SPEC_MISSING:" + str(acceptance_path))
        return 1

    try:
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        gates = acceptance.setdefault("truth_gates", {})
        gates["DOC_LAYOUT"] = "PASS"
        gates["PROJECT_DOCS_NORMALIZED"] = "PASS"
        gates["DOC_READABILITY"] = "PASS"
        gates["PROJECT_DOCS_SYNC"] = "PASS"
        acceptance_path.write_text(
            json.dumps(acceptance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        print("FAIL ACCEPTANCE_SPEC_UPDATE_ERROR:" + str(exc))
        return 1

    code, output = run(
        [sys.executable, str(compiler), "--root", str(root)],
        root,
    )
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    code, output = run(
        [sys.executable, str(validator), "--root", str(root)],
        root,
    )
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    print("PROJECT_DOCS_SYNC=PASS")
    try:
        gate_ref = acceptance_path.relative_to(root).as_posix()
    except ValueError:
        gate_ref = str(acceptance_path)
    print(
        "RECORDED_GATE="
        + gate_ref
        + "::truth_gates.{DOC_LAYOUT,PROJECT_DOCS_NORMALIZED,DOC_READABILITY,PROJECT_DOCS_SYNC}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate deterministic Project Truth Compiler outputs without mutating docs."""

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--report", default="")
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

    if not documentation.get("generated", False):
        print("PROJECT_DOCS_SYNC=NOT_APPLICABLE")
        return 0

    tool_dir = Path(__file__).resolve().parent
    compiler = tool_dir / "generate_project_docs.py"
    if not compiler.is_file():
        compiler = root / "scripts" / "generate_project_docs.py"
    if not compiler.is_file():
        print("FAIL PROJECT_TRUTH_COMPILER_MISSING")
        return 1

    quality_validator = tool_dir / "validate_doc_quality.py"
    if not quality_validator.is_file():
        quality_validator = root / "scripts" / "validate_doc_quality.py"
    if not quality_validator.is_file():
        print("FAIL DOC_QUALITY_VALIDATOR_MISSING")
        return 1

    cmd = [
        sys.executable,
        str(compiler),
        "--root",
        str(root),
        "--check",
    ]
    if args.report:
        cmd.extend(["--report", args.report])
    proc = subprocess.run(
        cmd,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(proc.stdout, end="")
    if proc.returncode != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return proc.returncode

    quality_cmd = [
        sys.executable,
        str(quality_validator),
        "--root",
        str(root),
    ]
    if args.report:
        report_path = Path(args.report)
        quality_report = str(
            report_path.with_name(
                report_path.stem + ".quality" + report_path.suffix
            )
        )
        quality_cmd.extend(["--report", quality_report])

    quality = subprocess.run(
        quality_cmd,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(quality.stdout, end="")
    if quality.returncode != 0:
        print("DOC_LAYOUT=FAIL")
        print("DOC_READABILITY=FAIL")
        print("PROJECT_DOCS_SYNC=FAIL")
        return quality.returncode

    print("DOC_LAYOUT=PASS")
    print("PROJECT_DOCS_NORMALIZED=PASS")
    print("DOC_READABILITY=PASS")
    print("PROJECT_DOCS_SYNC=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

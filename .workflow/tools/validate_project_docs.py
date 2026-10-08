#!/usr/bin/env python3
"""Validate deterministic Project Truth Compiler outputs without mutating docs."""

from __future__ import annotations

import argparse
from pathlib import Path

import generate_project_docs
import validate_doc_quality
from project_profile import (
    PROFILE_FILE,
    documentation_settings,
    parse_profile,
)
from script_runner import invoke_main



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--report", default="")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--changed-path", action="append", default=[])
    args = ap.parse_args()

    root = Path(args.root).resolve()
    agents_path = root / "AGENTS.md"
    if not agents_path.is_file():
        print("FAIL MISSING_ROOT_AGENTS")
        return 1
    agents_text = agents_path.read_text(encoding="utf-8", errors="strict")
    if validate_doc_quality.GENERATED_MARKER in agents_text:
        print("FAIL ROOT_AGENTS_MUST_BE_SOURCE_AUTHORED")
        return 1
    agents_ok, agents_failures = validate_doc_quality.normalized_shape(agents_text)
    if not agents_ok:
        print("FAIL ROOT_AGENTS_QUALITY:" + ";".join(agents_failures))
        return 1

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
    if args.incremental and not args.changed_path:
        print("FAIL INCREMENTAL_CHANGED_PATH_REQUIRED")
        return 2

    compiler_args = ["--root", str(root), "--check"]
    if args.incremental:
        compiler_args.append("--incremental")
        for path in args.changed_path:
            compiler_args.extend(["--changed-path", path])
    if args.report:
        compiler_args.extend(["--report", args.report])
    code, output = invoke_main(generate_project_docs.main, compiler_args, program="generate_project_docs.py")
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    quality_args = ["--root", str(root)]
    if args.report:
        report_path = Path(args.report)
        quality_report = str(report_path.with_name(report_path.stem + ".quality" + report_path.suffix))
        quality_args.extend(["--report", quality_report])
    quality_code, quality_output = invoke_main(validate_doc_quality.main, quality_args, program="validate_doc_quality.py")
    print(quality_output, end="")
    if quality_code != 0:
        print("DOC_LAYOUT=FAIL")
        print("DOC_READABILITY=FAIL")
        print("PROJECT_DOCS_SYNC=FAIL")
        return quality_code

    print("DOC_LAYOUT=PASS")
    print("PROJECT_DOCS_NORMALIZED=PASS")
    print("DOC_READABILITY=PASS")
    print("PROJECT_DOCS_INCREMENTAL=" + ("PASS" if args.incremental else "NOT_APPLICABLE"))
    print("PROJECT_DOCS_SYNC=PASS")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

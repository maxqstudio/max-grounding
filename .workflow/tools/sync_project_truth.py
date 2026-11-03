#!/usr/bin/env python3
"""Synchronize Project Truth Compiler outputs and record PROJECT_DOCS_SYNC.

Default execution remains exhaustive. Explicit --incremental execution is
intermediate-only and uses fail-closed changed-path impact planning.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import generate_project_docs
import validate_project_docs
from project_profile import PROFILE_FILE, documentation_settings, parse_profile
from project_snapshot import ProjectSnapshot, active_project_snapshot, active_snapshot_for
from script_runner import invoke_main


def run_main(main_func, argv: list[str], program: str) -> tuple[int, str]:
    return invoke_main(main_func, argv, program=program)


def compiler_args(root: Path, *, check: bool, incremental: bool, changed_paths: tuple[str, ...]) -> list[str]:
    args = ["--root", str(root)]
    if check:
        args.append("--check")
    if incremental:
        args.append("--incremental")
        for path in changed_paths:
            args.extend(["--changed-path", path])
    return args


def sync_once(
    root: Path,
    *,
    no_record: bool,
    incremental: bool,
    changed_paths: tuple[str, ...],
) -> int:
    profile_path = root / PROFILE_FILE
    if not profile_path.is_file():
        print("FAIL MISSING_PROJECT_PROFILE")
        return 1
    if incremental and not changed_paths:
        print("FAIL INCREMENTAL_CHANGED_PATH_REQUIRED")
        return 2

    try:
        documentation = documentation_settings(parse_profile(profile_path))
    except Exception as exc:
        print("FAIL PROJECT_PROFILE_INVALID:" + str(exc))
        return 1

    generate_args = compiler_args(root, check=False, incremental=incremental, changed_paths=changed_paths)
    code, output = run_main(generate_project_docs.main, generate_args, "generate_project_docs.py")
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    validate_args = ["--root", str(root)]
    if incremental:
        validate_args.append("--incremental")
        for path in changed_paths:
            validate_args.extend(["--changed-path", path])
    code, output = run_main(validate_project_docs.main, validate_args, "validate_project_docs.py")
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    if not documentation.get("generated", False):
        print("PROJECT_DOCS_SYNC=NOT_APPLICABLE")
        return 0
    if no_record:
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
        gates["ROADMAP_SYNC"] = "PASS"
        gates["DOC_LAYOUT"] = "PASS"
        gates["PROJECT_DOCS_NORMALIZED"] = "PASS"
        gates["DOC_READABILITY"] = "PASS"
        gates["PROJECT_DOCS_SYNC"] = "PASS"
        acceptance_path.write_bytes((json.dumps(acceptance, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    except Exception as exc:
        print("FAIL ACCEPTANCE_SPEC_UPDATE_ERROR:" + str(exc))
        return 1

    try:
        acceptance_rel = acceptance_path.relative_to(root).as_posix()
    except ValueError:
        acceptance_rel = str(acceptance_path)
    second_paths = changed_paths
    if incremental and acceptance_rel not in second_paths:
        second_paths = tuple(sorted(set(changed_paths + (acceptance_rel,))))

    generate_args = compiler_args(root, check=False, incremental=incremental, changed_paths=second_paths)
    code, output = run_main(generate_project_docs.main, generate_args, "generate_project_docs.py")
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    validate_args = ["--root", str(root)]
    if incremental:
        validate_args.append("--incremental")
        for path in second_paths:
            validate_args.extend(["--changed-path", path])
    code, output = run_main(validate_project_docs.main, validate_args, "validate_project_docs.py")
    print(output, end="")
    if code != 0:
        print("PROJECT_DOCS_SYNC=FAIL")
        return code

    print("PROJECT_DOCS_SYNC=PASS")
    print("PROJECT_DOCS_INCREMENTAL=" + ("PASS" if incremental else "NOT_APPLICABLE"))
    print("RECORDED_GATE=" + acceptance_rel + "::truth_gates.{ROADMAP_SYNC,DOC_LAYOUT,PROJECT_DOCS_NORMALIZED,DOC_READABILITY,PROJECT_DOCS_SYNC}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--no-record", action="store_true")
    ap.add_argument("--incremental", action="store_true")
    ap.add_argument("--changed-path", action="append", default=[])
    args = ap.parse_args()

    root = Path(args.root).resolve()
    changed_paths = tuple(sorted(set(args.changed_path)))
    if active_snapshot_for(root) is not None:
        return sync_once(root, no_record=args.no_record, incremental=args.incremental, changed_paths=changed_paths)

    snapshot = ProjectSnapshot.capture(root)
    with active_project_snapshot(snapshot):
        return sync_once(root, no_record=args.no_record, incremental=args.incremental, changed_paths=changed_paths)


if __name__ == "__main__":
    raise SystemExit(main())

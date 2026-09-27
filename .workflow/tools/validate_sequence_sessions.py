#!/usr/bin/env python3
"""Validate all sequence session contracts required by the project profile."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from project_profile import parse_profile, sequence_settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--sessions-dir", default="docs/sequence/sessions")
    args = ap.parse_args()

    start = Path(args.root).resolve()
    try:
        root = Path(subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
        ).strip())
    except Exception as exc:
        print(json.dumps({"result": "FAIL", "error": f"GIT_ERROR:{exc}"}, indent=2))
        return 1

    try:
        policy = sequence_settings(parse_profile(root / "PROJECT_PROFILE.yaml"))
    except Exception as exc:
        print(json.dumps({"result": "FAIL", "error": f"PROFILE_ERROR:{exc}"}, indent=2))
        return 1

    sessions_root = root / args.sessions_dir
    sessions = sorted(sessions_root.rglob("*.json")) if sessions_root.is_dir() else []

    if policy.get("required", False) and not sessions:
        print(json.dumps({
            "result": "FAIL",
            "sequence_required": True,
            "sessions": 0,
            "failures": ["SEQUENCE_SESSION_CONTRACT_MISSING"],
        }, indent=2))
        return 1

    tool_dir = Path(__file__).resolve().parent
    validator = tool_dir / "validate_sequence_contract.py"
    if not validator.is_file():
        validator = root / "scripts" / "validate_sequence_contract.py"
    if not validator.is_file():
        print(json.dumps({
            "result": "FAIL",
            "failures": ["SEQUENCE_VALIDATOR_MISSING"],
        }, indent=2))
        return 1

    results = []
    failed = 0

    for session in sessions:
        rel = session.relative_to(root).as_posix()
        proc = subprocess.run(
            [
                sys.executable,
                str(validator),
                "--root",
                str(root),
                "--session",
                rel,
            ],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if proc.returncode != 0:
            failed += 1
        results.append({
            "session": rel,
            "returncode": proc.returncode,
            "output": proc.stdout,
        })

    report = {
        "sequence_required": policy.get("required", False),
        "runtime_trace_required": policy.get("runtime_trace_required", False),
        "sessions": len(sessions),
        "failed_sessions": failed,
        "results": results,
        "result": "FAIL" if failed else "PASS",
    }
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

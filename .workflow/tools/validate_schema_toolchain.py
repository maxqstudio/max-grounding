#!/usr/bin/env python3
"""Validate governed schema versions and vendored toolchain identity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from project_profile import parse_profile
from schema_contract import validate_spec_tree_versions
from toolchain_identity import validate_toolchain_lock


def validate(root: Path) -> dict[str, object]:
    failures: list[str] = []
    profile_version: int | None = None
    try:
        profile = parse_profile(root / "PROJECT_PROFILE.yaml")
        profile_version = int(str(profile["schema_version"]).strip())
    except Exception as exc:
        failures.append(str(exc))

    spec_root = root / ".workflow"
    if not spec_root.is_dir():
        failures.append("SPEC_ROOT_MISSING:.workflow")
        spec_count = 0
    else:
        failures.extend(validate_spec_tree_versions(spec_root))
        spec_count = len(
            [p for p in spec_root.glob("*.json") if p.name != "toolchain.lock.json"]
        ) + len(list((spec_root / "workflows").glob("*.json")))

    tool_root = spec_root / "tools"
    lock_path = spec_root / "toolchain.lock.json"
    if tool_root.is_dir() or lock_path.is_file():
        lock_failures = validate_toolchain_lock(root, spec_root)
        failures.extend(lock_failures)
        toolchain_status = "FAIL" if lock_failures else "PASS"
    else:
        toolchain_status = "NOT_APPLICABLE_PRODUCER_REPO"

    return {
        "schema_version": 1,
        "result": "FAIL" if failures else "PASS",
        "profile_schema_version": profile_version,
        "governed_specs_checked": spec_count,
        "toolchain_lock": toolchain_status,
        "failures": failures,
        "evidence_boundary": (
            "This gate proves supported schema-version declarations and exact vendored-tool file identity. "
            "It does not treat producer commit hints, cache state, or mutable upstream state as acceptance authority."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    report = validate(root)
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.report:
        output = Path(args.report)
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

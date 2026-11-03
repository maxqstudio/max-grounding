#!/usr/bin/env python3
"""Version contracts for Skill Workflow profiles and governed JSON specs."""

from __future__ import annotations

import json
from pathlib import Path

PROFILE_SCHEMA_VERSION = 1
GOVERNED_SPEC_SCHEMA_VERSION = 1
TOOLCHAIN_LOCK_SCHEMA_VERSION = 1
TOOLCHAIN_CONTRACT_VERSION = 2


def _profile_version(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("PROJECT_PROFILE_SCHEMA_VERSION_INVALID:boolean")
    if isinstance(value, int):
        return value
    raw = str(value).strip()
    if raw.isdigit():
        return int(raw)
    raise ValueError("PROJECT_PROFILE_SCHEMA_VERSION_INVALID:" + raw)


def require_profile_schema_version(data: dict, label: str = "PROJECT_PROFILE.yaml") -> int:
    if "schema_version" not in data:
        raise ValueError(
            "PROJECT_PROFILE_SCHEMA_VERSION_MISSING:"
            + label
            + ":run scripts/migrate_governance_v1.py"
        )
    version = _profile_version(data["schema_version"])
    if version != PROFILE_SCHEMA_VERSION:
        raise ValueError(
            "PROJECT_PROFILE_SCHEMA_VERSION_UNSUPPORTED:"
            + str(version)
            + ":supported="
            + str(PROFILE_SCHEMA_VERSION)
        )
    return version


def require_json_schema_version(data: dict, label: str) -> int:
    if "schema_version" not in data:
        raise ValueError(
            "SPEC_SCHEMA_VERSION_MISSING:"
            + label
            + ":run scripts/migrate_governance_v1.py"
        )
    value = data["schema_version"]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("SPEC_SCHEMA_VERSION_INVALID:" + label + ":" + repr(value))
    if value != GOVERNED_SPEC_SCHEMA_VERSION:
        raise ValueError(
            "SPEC_SCHEMA_VERSION_UNSUPPORTED:"
            + label
            + ":"
            + str(value)
            + ":supported="
            + str(GOVERNED_SPEC_SCHEMA_VERSION)
        )
    return value


def governed_spec_paths(spec_root: Path) -> list[Path]:
    paths = [
        path
        for path in sorted(spec_root.glob("*.json"))
        if path.name != "toolchain.lock.json"
    ]
    workflow_root = spec_root / "workflows"
    if workflow_root.is_dir():
        paths.extend(sorted(workflow_root.glob("*.json")))
    return paths


def validate_spec_tree_versions(spec_root: Path) -> list[str]:
    failures: list[str] = []
    for path in governed_spec_paths(spec_root):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("top-level object required")
            require_json_schema_version(data, path.relative_to(spec_root).as_posix())
        except Exception as exc:
            failures.append(str(exc))
    return failures

#!/usr/bin/env python3
"""Deterministic identity and lock validation for vendored governance tools."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from schema_contract import TOOLCHAIN_CONTRACT_VERSION, TOOLCHAIN_LOCK_SCHEMA_VERSION

SOURCE_ONLY_TOOLS = {
    "initialize_project_truth.py",
    "migrate_governance_v1.py",
    "selftest_project_truth_compiler.py",
    "upgrade_governance_toolchain.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_tool_paths(skill_root: Path) -> list[Path]:
    source_tools = skill_root / "scripts"
    return [
        path
        for path in sorted(source_tools.glob("*.py"))
        if path.name not in SOURCE_ONLY_TOOLS
    ]


def tool_manifest(tool_root: Path) -> dict[str, str]:
    if not tool_root.is_dir():
        return {}
    return {
        path.name: _sha256(path)
        for path in sorted(tool_root.glob("*.py"))
        if path.is_file()
    }


def toolchain_digest(manifest: dict[str, str]) -> str:
    payload = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_toolchain_manifest(skill_root: Path) -> dict[str, str]:
    return {path.name: _sha256(path) for path in source_tool_paths(skill_root)}


def _git_value(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def producer_metadata(skill_root: Path) -> dict[str, str]:
    source_digest = toolchain_digest(source_toolchain_manifest(skill_root))
    repository = _git_value(skill_root, "config", "--get", "remote.origin.url")
    source_sha = _git_value(skill_root, "rev-parse", "HEAD").lower()
    release = _git_value(skill_root, "describe", "--tags", "--exact-match", "HEAD")
    if repository and re.fullmatch(r"[0-9a-f]{40}", source_sha):
        return {
            "identity_source": "GIT+CONTENT",
            "repository": repository,
            "source_sha": source_sha,
            "release": release or "UNRELEASED",
            "source_digest": source_digest,
        }
    return {
        "identity_source": "CONTENT",
        "repository": "NOT_PROVEN",
        "source_sha": "NOT_PROVEN",
        "release": "NOT_PROVEN",
        "source_digest": source_digest,
    }


def sync_vendored_tools(skill_root: Path, tool_root: Path) -> dict[str, object]:
    tool_root.mkdir(parents=True, exist_ok=True)
    writes = 0
    skips = 0
    files: list[str] = []
    for src in source_tool_paths(skill_root):
        dst = tool_root / src.name
        files.append(src.name)
        source_bytes = src.read_bytes()
        if dst.is_file() and dst.read_bytes() == source_bytes:
            skips += 1
            continue
        shutil.copyfile(src, dst)
        writes += 1
    return {"writes": writes, "skips": skips, "files": files}


def write_toolchain_lock(
    project_root: Path,
    spec_root: Path,
    tool_root: Path,
    skill_root: Path,
) -> str:
    project_root = project_root.resolve()
    spec_root = spec_root.resolve()
    tool_root = tool_root.resolve()
    relative_tool_root = tool_root.relative_to(project_root).as_posix()
    manifest = tool_manifest(tool_root)
    payload = {
        "schema_version": TOOLCHAIN_LOCK_SCHEMA_VERSION,
        "toolchain_contract_version": TOOLCHAIN_CONTRACT_VERSION,
        "tool_root": relative_tool_root,
        "toolchain_digest": toolchain_digest(manifest),
        "files": manifest,
        "producer": producer_metadata(skill_root.resolve()),
    }
    target = spec_root / "toolchain.lock.json"
    expected = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if target.is_file() and target.read_text(encoding="utf-8") == expected:
        return "SKIP"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8", newline="\n")
    return "WRITE"


def validate_toolchain_lock(project_root: Path, spec_root: Path) -> list[str]:
    failures: list[str] = []
    lock_path = spec_root / "toolchain.lock.json"
    if not lock_path.is_file():
        return ["TOOLCHAIN_LOCK_MISSING:.workflow/toolchain.lock.json"]
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return ["TOOLCHAIN_LOCK_INVALID_JSON:" + str(exc)]
    if not isinstance(lock, dict):
        return ["TOOLCHAIN_LOCK_TOP_LEVEL_OBJECT_REQUIRED"]

    version = lock.get("schema_version")
    if isinstance(version, bool) or version != TOOLCHAIN_LOCK_SCHEMA_VERSION:
        failures.append(
            "TOOLCHAIN_LOCK_SCHEMA_VERSION_UNSUPPORTED:"
            + repr(version)
            + ":supported="
            + str(TOOLCHAIN_LOCK_SCHEMA_VERSION)
        )
    contract_version = lock.get("toolchain_contract_version")
    if isinstance(contract_version, bool) or contract_version != TOOLCHAIN_CONTRACT_VERSION:
        failures.append(
            "TOOLCHAIN_CONTRACT_VERSION_UNSUPPORTED:"
            + repr(contract_version)
            + ":supported="
            + str(TOOLCHAIN_CONTRACT_VERSION)
        )

    raw_tool_root = str(lock.get("tool_root", "")).strip()
    tool_path = Path(raw_tool_root)
    if not raw_tool_root or tool_path.is_absolute() or ".." in tool_path.parts:
        failures.append("TOOLCHAIN_TOOL_ROOT_INVALID:" + raw_tool_root)
        return failures
    tool_root = project_root / tool_path
    if not tool_root.is_dir():
        failures.append("TOOLCHAIN_TOOL_ROOT_MISSING:" + raw_tool_root)
        return failures

    declared = lock.get("files")
    if not isinstance(declared, dict):
        failures.append("TOOLCHAIN_FILES_MANIFEST_INVALID")
        return failures
    actual = tool_manifest(tool_root)
    if declared != actual:
        missing = sorted(set(declared) - set(actual))
        extra = sorted(set(actual) - set(declared))
        changed = sorted(
            name
            for name in set(declared).intersection(actual)
            if declared[name] != actual[name]
        )
        failures.append(
            "TOOLCHAIN_MANIFEST_MISMATCH:missing="
            + ",".join(missing)
            + ";extra="
            + ",".join(extra)
            + ";changed="
            + ",".join(changed)
        )
    expected_digest = toolchain_digest(actual)
    if str(lock.get("toolchain_digest", "")) != expected_digest:
        failures.append("TOOLCHAIN_DIGEST_MISMATCH")

    producer = lock.get("producer")
    if not isinstance(producer, dict):
        failures.append("TOOLCHAIN_PRODUCER_METADATA_INVALID")
        return failures
    identity_source = str(producer.get("identity_source", "")).strip()
    if identity_source not in {"GIT+CONTENT", "CONTENT"}:
        failures.append("TOOLCHAIN_PRODUCER_IDENTITY_SOURCE_INVALID")
    source_digest = str(producer.get("source_digest", "")).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_digest):
        failures.append("TOOLCHAIN_PRODUCER_SOURCE_DIGEST_INVALID")
    elif source_digest != expected_digest:
        failures.append("TOOLCHAIN_PRODUCER_SOURCE_DIGEST_MISMATCH")

    repository = str(producer.get("repository", "")).strip()
    source_sha = str(producer.get("source_sha", "")).strip().lower()
    release = str(producer.get("release", "")).strip()
    if identity_source == "GIT+CONTENT":
        if not repository or repository == "NOT_PROVEN":
            failures.append("TOOLCHAIN_PRODUCER_REPOSITORY_MISSING")
        if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
            failures.append("TOOLCHAIN_PRODUCER_SOURCE_SHA_INVALID")
        if not release or release == "NOT_PROVEN":
            failures.append("TOOLCHAIN_PRODUCER_RELEASE_MISSING")
    elif identity_source == "CONTENT":
        if repository != "NOT_PROVEN":
            failures.append("TOOLCHAIN_PRODUCER_CONTENT_REPOSITORY_MUST_BE_NOT_PROVEN")
        if source_sha != "not_proven":
            failures.append("TOOLCHAIN_PRODUCER_CONTENT_SOURCE_SHA_MUST_BE_NOT_PROVEN")
        if release != "NOT_PROVEN":
            failures.append("TOOLCHAIN_PRODUCER_CONTENT_RELEASE_MUST_BE_NOT_PROVEN")
    if "commit_hint" in producer:
        failures.append("TOOLCHAIN_PRODUCER_LEGACY_COMMIT_HINT")
    return failures

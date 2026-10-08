#!/usr/bin/env python3
"""Read-only exact-SHA classification of representative real consumer snapshots.

PASS here means the pinned snapshot and its expected compatibility class match,
NOT that an incompatible consumer has passed migration or final acceptance.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
FIELDS = ("PROJECT_PROFILE.yaml", "AGENTS.md", ".workflow/toolchain.lock.json")
API = "https://api.github.com/repos/"


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("MATRIX_OBJECT_REQUIRED")
    return value


def classify(files: dict[str, bytes | None]) -> dict[str, str]:
    profile_bytes = files.get("PROJECT_PROFILE.yaml")
    if profile_bytes is None:
        return {"classification": "PROFILE_MISSING", "profile": "MISSING",
                "root_agents": "MISSING", "toolchain_lock": "MISSING"}
    text = profile_bytes.decode("utf-8", errors="strict")
    profile_match = re.search(r"(?m)^\s*profile:\s*([a-z]+)\s*$", text)
    profile = profile_match.group(1) if profile_match else "UNKNOWN"
    agents = "PRESENT" if files.get("AGENTS.md") else "MISSING"
    lock_bytes = files.get(".workflow/toolchain.lock.json")
    if agents == "MISSING" or lock_bytes is None:
        return {"classification": "LEGACY_PRECONDITIONS_MISSING", "profile": profile,
                "root_agents": agents, "toolchain_lock": "PRESENT" if lock_bytes else "MISSING"}
    if not re.search(r"(?m)^\s*schema_version:\s*1\s*$", text):
        return {"classification": "UNSUPPORTED_SCHEMA", "profile": profile,
                "root_agents": agents, "toolchain_lock": "PRESENT"}
    try:
        lock = json.loads(lock_bytes)
        if not isinstance(lock, dict):
            raise ValueError("not object")
    except (ValueError, TypeError):
        return {"classification": "MALFORMED_LOCK", "profile": profile,
                "root_agents": agents, "toolchain_lock": "MALFORMED"}
    producer = lock.get("producer")
    if not isinstance(producer, dict):
        return {"classification": "UNSUPPORTED_IDENTITY", "profile": profile,
                "root_agents": agents, "toolchain_lock": "UNKNOWN"}
    if "commit_hint" in producer and "identity_source" not in producer:
        return {"classification": "EXPLICIT_MIGRATION_REQUIRED", "profile": profile,
                "root_agents": agents, "toolchain_lock": "LEGACY_HINT_ONLY"}
    identity = producer.get("identity_source")
    digest = str(producer.get("source_digest", "")).lower()
    if identity not in {"GIT+CONTENT", "CONTENT"} or not DIGEST.fullmatch(digest):
        return {"classification": "UNSUPPORTED_IDENTITY", "profile": profile,
                "root_agents": agents, "toolchain_lock": "UNVERIFIED"}
    if identity == "CONTENT":
        if any(producer.get(k) != "NOT_PROVEN" for k in ("repository", "source_sha", "release")):
            return {"classification": "UNSUPPORTED_IDENTITY", "profile": profile,
                    "root_agents": agents, "toolchain_lock": "UNVERIFIED"}
    else:
        if not SHA.fullmatch(str(producer.get("source_sha", ""))):
            return {"classification": "UNSUPPORTED_IDENTITY", "profile": profile,
                    "root_agents": agents, "toolchain_lock": "UNVERIFIED"}
    return {"classification": "REQUIRES_FULL_VALIDATION", "profile": profile,
            "root_agents": agents, "toolchain_lock": identity}


def _request(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "SW2-23-ReadOnlyCompatibility"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=25) as response:
        if response.status != 200:
            raise ValueError("GITHUB_API_UNEXPECTED_STATUS:" + str(response.status))
        data = json.loads(response.read(4_000_000))
    if not isinstance(data, dict):
        raise ValueError("GITHUB_API_PAYLOAD_INVALID")
    return data


def fetch_snapshot(repository: str, sha: str) -> dict[str, bytes | None]:
    if not REPO.fullmatch(repository) or not SHA.fullmatch(sha):
        raise ValueError("UNSAFE_REPOSITORY_OR_SHA")
    commit = _request(API + repository + "/commits/" + sha)
    if commit.get("sha") != sha:
        raise ValueError("PINNED_COMMIT_IDENTITY_MISMATCH")
    files: dict[str, bytes | None] = {}
    for path in FIELDS:
        try:
            payload = _request(API + repository + "/contents/" + path + "?ref=" + sha)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                files[path] = None
                continue
            raise
        if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            raise ValueError("UNSUPPORTED_CONTENT_ENCODING:" + path)
        files[path] = base64.b64decode(payload["content"].replace("\n", "").replace("\r", ""), validate=True)
    return files


def audit(matrix: dict, *, fetcher=fetch_snapshot) -> dict:
    if matrix.get("schema_version") != 1:
        raise ValueError("MATRIX_SCHEMA_UNSUPPORTED")
    rows = matrix.get("consumer_snapshots")
    if not isinstance(rows, list) or len(rows) < 3:
        raise ValueError("REPRESENTATIVE_CONSUMERS_INSUFFICIENT")
    seen: set[str] = set()
    reports: list[dict] = []
    failures: list[str] = []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("MATRIX_ENTRY_INVALID")
        repository, sha = item.get("repository", ""), item.get("sha", "")
        if not isinstance(repository, str) or not isinstance(sha, str):
            raise ValueError("MATRIX_REPOSITORY_OR_SHA_INVALID")
        if repository in seen:
            raise ValueError("MATRIX_DUPLICATE_REPOSITORY")
        seen.add(repository)
        if not REPO.fullmatch(repository) or not SHA.fullmatch(sha):
            raise ValueError("MATRIX_PIN_INVALID")
        files = fetcher(repository, sha)
        actual = classify(files)
        expected = {k: item.get(k) for k in ("profile", "root_agents", "toolchain_lock", "classification")}
        same = actual == expected
        if not same:
            failures.append("PINNED_CONSUMER_CLASSIFICATION_MISMATCH:" + repository)
        reports.append({
            "repository": repository, "pinned_sha": sha,
            "observed": actual, "expected": expected,
            "classification_match": same,
            "adoption_authority": "NOT_PROVEN",
            "repository_mutation": "NONE",
        })
    return {
        "schema_version": 1,
        "result": "PASS" if not failures else "FAIL",
        "evidence_boundary": "Read-only exact-SHA classification only, not migration, source tests, full compatibility, or consumer runtime acceptance.",
        "snapshots": reports,
        "failures": failures,
        "full_adoption_proven": False
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix", default=".workflow/consumer_compatibility_matrix.json")
    ap.add_argument("--report", default="")
    args = ap.parse_args()
    try:
        report = audit(read_json(Path(args.matrix)))
        if args.report:
            path = Path(args.report)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        print("CONSUMER_MATRIX_JSON=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
        print("CONSUMER_MATRIX_RESULT=" + str(report["result"]))
        return 0 if report["result"] == "PASS" else 1
    except Exception as exc:
        print("CONSUMER_MATRIX_RESULT=FAIL")
        print("FIRST_FAILED_GATE=" + type(exc).__name__ + ":" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

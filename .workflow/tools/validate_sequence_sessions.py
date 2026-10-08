#!/usr/bin/env python3
"""Validate sequence sessions with optional content-addressed historical freeze.

Normal validation preserves backward compatibility: projects without a historical
manifest continue to replay historical contracts. Once a manifest is explicitly
created, historical sessions are verified by immutable file identity while only
CURRENT sessions are fully replayed. Historical replay remains available through
an explicit authorization flag.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from project_profile import parse_profile, sequence_settings

MANIFEST_SCHEMA_VERSION = 1
DEFAULT_MANIFEST = ".workflow/historical_evidence.json"


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def git_commit_exists(root: Path, commit: str) -> bool:
    if not commit:
        return False
    proc = subprocess.run(
        ["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return proc.returncode == 0


def git_show_bytes(root: Path, commit: str, relpath: str) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(root), "show", f"{commit}:{relpath}"],
        stderr=subprocess.STDOUT,
    )


def git_path_clean(root: Path, relpath: str) -> bool:
    for args in (("diff", "--quiet", "--", relpath), ("diff", "--cached", "--quiet", "--", relpath)):
        proc = subprocess.run(["git", "-C", str(root), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if proc.returncode != 0:
            return False
    return True


def repo_relative_path(root: Path, value: str) -> str:
    raw = value.replace("\\", "/").strip()
    if not raw:
        raise ValueError("EMPTY_EVIDENCE_PATH")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ValueError(f"UNSAFE_EVIDENCE_PATH:{raw}")
    resolved = (root / candidate).resolve()
    try:
        rel = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"EVIDENCE_PATH_OUTSIDE_REPO:{raw}") from exc
    return rel


def load_session(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def session_scope(data: dict) -> str:
    return str(data.get("scope", "CURRENT")).strip().upper()


def historical_sessions(session_paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    for path in session_paths:
        try:
            data = load_session(path)
        except Exception:
            continue
        if session_scope(data) == "HISTORICAL":
            result.append(path)
    return sorted(result)


def evidence_paths(root: Path, session_path: Path, data: dict) -> list[str]:
    values: set[str] = {session_path.relative_to(root).as_posix()}

    def add(value: object) -> None:
        text = str(value or "").strip()
        if text:
            values.add(repo_relative_path(root, text))

    add(data.get("acceptance_report"))
    for section, keys in (
        (data.get("actual", {}), ("graph", "diagram")),
        (data.get("human_view", {}), ("graph", "diagram", "document")),
        (data.get("plan", {}), ("contract", "diagram")),
        (data.get("runtime_trace", {}), ("graph",)),
    ):
        if isinstance(section, dict):
            for key in keys:
                add(section.get(key))
    return sorted(values)


def build_historical_manifest(
    root: Path,
    session_paths: list[Path],
    *,
    frozen_commit: str,
    authorization_reason: str,
    generator_path: Path | None = None,
) -> dict:
    reason = authorization_reason.strip()
    if not reason:
        raise ValueError("HISTORICAL_MIGRATION_AUTHORIZATION_REQUIRED")
    if not git_commit_exists(root, frozen_commit):
        raise ValueError(f"HISTORICAL_FROZEN_COMMIT_INVALID:{frozen_commit}")

    entries: list[dict] = []
    seen_files: dict[str, str] = {}
    for session_path in historical_sessions(session_paths):
        data = load_session(session_path)
        rel_session = session_path.relative_to(root).as_posix()
        actual = data.get("actual", {}) if isinstance(data.get("actual"), dict) else {}
        files: list[dict] = []
        for rel in evidence_paths(root, session_path, data):
            path = root / rel
            if not path.is_file():
                try:
                    git_show_bytes(root, frozen_commit, rel)
                except subprocess.CalledProcessError:
                    files.append({"path": rel, "present": False})
                    continue
                raise ValueError(f"HISTORICAL_EVIDENCE_CURRENTLY_MISSING:{rel}")
            if not git_path_clean(root, rel):
                raise ValueError(f"HISTORICAL_EVIDENCE_WORKTREE_DIRTY:{rel}")
            try:
                current = git_show_bytes(root, "HEAD", rel)
            except subprocess.CalledProcessError as exc:
                raise ValueError(f"HISTORICAL_EVIDENCE_NOT_IN_HEAD:{rel}") from exc
            try:
                frozen = git_show_bytes(root, frozen_commit, rel)
            except subprocess.CalledProcessError as exc:
                raise ValueError(f"HISTORICAL_EVIDENCE_NOT_IN_FROZEN_COMMIT:{rel}") from exc
            current_sha = sha256_bytes(current)
            frozen_sha = sha256_bytes(frozen)
            if current_sha != frozen_sha:
                raise ValueError(
                    f"HISTORICAL_EVIDENCE_FROZEN_MISMATCH:{rel}:{current_sha}!={frozen_sha}"
                )
            previous = seen_files.get(rel)
            if previous is not None and previous != current_sha:
                raise ValueError(f"HISTORICAL_EVIDENCE_IDENTITY_CONFLICT:{rel}")
            seen_files[rel] = current_sha
            files.append({
                "path": rel,
                "present": True,
                "sha256": current_sha,
                "bytes": len(current),
            })
        entries.append({
            "session": rel_session,
            "session_id": str(data.get("session_id", "")),
            "phase": str(data.get("phase", "")),
            "session_schema_version": int(data.get("schema_version", 0) or 0),
            "source_digest": str(actual.get("source_digest", "")),
            "files": files,
        })

    tool = generator_path or Path(__file__).resolve()
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_by": "validate_sequence_sessions.py",
        "frozen_commit": frozen_commit,
        "authorization_reason": reason,
        "tool_sha256": sha256_file(tool) if tool.is_file() else "",
        "historical_sessions": entries,
    }


def verify_historical_manifest(
    root: Path,
    session_paths: list[Path],
    manifest_path: Path,
) -> tuple[list[str], dict]:
    failures: list[str] = []
    if not manifest_path.is_file():
        return ["HISTORICAL_MANIFEST_MISSING"], {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"HISTORICAL_MANIFEST_INVALID:{exc}"], {}

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        failures.append("HISTORICAL_MANIFEST_SCHEMA_UNSUPPORTED")
    if manifest.get("generated_by") != "validate_sequence_sessions.py":
        failures.append("HISTORICAL_MANIFEST_GENERATOR_INVALID")
    frozen_commit = str(manifest.get("frozen_commit", "")).strip()
    if not git_commit_exists(root, frozen_commit):
        failures.append("HISTORICAL_MANIFEST_FROZEN_COMMIT_INVALID")

    expected_paths = [
        path.relative_to(root).as_posix()
        for path in historical_sessions(session_paths)
    ]
    raw_entries = manifest.get("historical_sessions", [])
    if not isinstance(raw_entries, list):
        failures.append("HISTORICAL_MANIFEST_ENTRIES_INVALID")
        raw_entries = []
    entries = {
        str(item.get("session", "")): item
        for item in raw_entries
        if isinstance(item, dict)
    }
    if sorted(entries) != expected_paths:
        failures.append(
            "HISTORICAL_SESSION_SET_MISMATCH:"
            + json.dumps({"expected": expected_paths, "observed": sorted(entries)}, sort_keys=True)
        )

    for rel_session in expected_paths:
        entry = entries.get(rel_session)
        if not isinstance(entry, dict):
            continue
        session_path = root / rel_session
        try:
            data = load_session(session_path)
        except Exception as exc:
            failures.append(f"HISTORICAL_SESSION_INVALID:{rel_session}:{exc}")
            continue
        actual = data.get("actual", {}) if isinstance(data.get("actual"), dict) else {}
        metadata = {
            "session_id": str(data.get("session_id", "")),
            "phase": str(data.get("phase", "")),
            "session_schema_version": int(data.get("schema_version", 0) or 0),
            "source_digest": str(actual.get("source_digest", "")),
        }
        for key, expected in metadata.items():
            if entry.get(key) != expected:
                failures.append(f"HISTORICAL_METADATA_MISMATCH:{rel_session}:{key}")

        expected_files = evidence_paths(root, session_path, data)
        raw_files = entry.get("files", [])
        if not isinstance(raw_files, list):
            failures.append(f"HISTORICAL_FILE_SET_INVALID:{rel_session}")
            continue
        files = {
            str(item.get("path", "")): item
            for item in raw_files
            if isinstance(item, dict)
        }
        if sorted(files) != expected_files:
            failures.append(f"HISTORICAL_FILE_SET_MISMATCH:{rel_session}")
        for rel in expected_files:
            record = files.get(rel)
            if not isinstance(record, dict):
                continue
            path = root / rel
            expected_present = bool(record.get("present", True))
            if not expected_present:
                if path.exists():
                    failures.append(f"HISTORICAL_ABSENT_EVIDENCE_APPEARED:{rel}")
                if frozen_commit:
                    try:
                        git_show_bytes(root, frozen_commit, rel)
                    except subprocess.CalledProcessError:
                        pass
                    else:
                        failures.append(f"HISTORICAL_FROZEN_ABSENCE_MISMATCH:{rel}")
                continue
            if not path.is_file():
                failures.append(f"HISTORICAL_EVIDENCE_MISSING:{rel}")
                continue
            if not git_path_clean(root, rel):
                failures.append(f"HISTORICAL_WORKTREE_DIRTY:{rel}")
                continue
            try:
                payload = git_show_bytes(root, "HEAD", rel)
            except subprocess.CalledProcessError:
                failures.append(f"HISTORICAL_HEAD_FILE_MISSING:{rel}")
                continue
            expected_sha = str(record.get("sha256", ""))
            if sha256_bytes(payload) != expected_sha:
                failures.append(f"HISTORICAL_CURRENT_HASH_MISMATCH:{rel}")
            if len(payload) != record.get("bytes"):
                failures.append(f"HISTORICAL_CURRENT_SIZE_MISMATCH:{rel}")
            if frozen_commit:
                try:
                    frozen = git_show_bytes(root, frozen_commit, rel)
                except subprocess.CalledProcessError:
                    failures.append(f"HISTORICAL_FROZEN_FILE_MISSING:{rel}")
                else:
                    if sha256_bytes(frozen) != expected_sha:
                        failures.append(f"HISTORICAL_FROZEN_HASH_MISMATCH:{rel}")
                    if len(frozen) != record.get("bytes"):
                        failures.append(f"HISTORICAL_FROZEN_SIZE_MISMATCH:{rel}")
    return failures, manifest


def run_validator(
    root: Path,
    validator: Path,
    rel: str,
    child_env: dict[str, str],
) -> tuple[int, str]:
    command = [
        sys.executable,
        str(validator),
        "--root",
        str(root),
        "--session",
        rel,
    ]
    if validator.name == "validate_sequence_contract.py":
        command.append("--no-write-report")
    proc = subprocess.run(
        command,
        cwd=root,
        env=child_env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return proc.returncode, proc.stdout


def fail_payload(error: str) -> int:
    print(json.dumps({"result": "FAIL", "error": error}, indent=2))
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--sessions-dir", default="docs/sequence/sessions")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--freeze-historical", action="store_true")
    ap.add_argument("--authorize-migration", default="")
    ap.add_argument("--frozen-commit", default="")
    ap.add_argument("--replay-historical", action="store_true")
    ap.add_argument("--authorize-replay", action="store_true")
    args = ap.parse_args()

    start = Path(args.root).resolve()
    try:
        root = Path(subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
        ).strip())
    except Exception as exc:
        return fail_payload(f"GIT_ERROR:{exc}")

    try:
        policy = sequence_settings(parse_profile(root / "PROJECT_PROFILE.yaml"))
    except Exception as exc:
        return fail_payload(f"PROFILE_ERROR:{exc}")

    sessions_root = root / args.sessions_dir
    sessions = sorted(sessions_root.rglob("*.json")) if sessions_root.is_dir() else []
    historical = historical_sessions(sessions)
    manifest_path = root / args.manifest

    if policy.get("required", False) and not sessions:
        print(json.dumps({
            "result": "FAIL",
            "sequence_required": True,
            "sessions": 0,
            "failures": ["SEQUENCE_SESSION_CONTRACT_MISSING"],
        }, indent=2))
        return 1

    if args.freeze_historical:
        try:
            manifest = build_historical_manifest(
                root,
                sessions,
                frozen_commit=args.frozen_commit.strip(),
                authorization_reason=args.authorize_migration,
            )
        except ValueError as exc:
            return fail_payload(str(exc))
        write_json(manifest_path, manifest)
        failures, verified = verify_historical_manifest(root, sessions, manifest_path)
        report = {
            "result": "FAIL" if failures else "PASS",
            "operation": "FREEZE_HISTORICAL",
            "manifest": manifest_path.relative_to(root).as_posix(),
            "manifest_sha256": sha256_file(manifest_path),
            "frozen_commit": verified.get("frozen_commit", ""),
            "authorization_reason": verified.get("authorization_reason", ""),
            "historical_sessions": len(historical),
            "failures": failures,
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if failures else 0

    if args.replay_historical and not args.authorize_replay:
        return fail_payload("HISTORICAL_REPLAY_AUTHORIZATION_REQUIRED")

    manifest_failures: list[str] = []
    manifest: dict = {}
    frozen_identity_active = bool(historical) and manifest_path.is_file()
    if frozen_identity_active:
        manifest_failures, manifest = verify_historical_manifest(
            root, sessions, manifest_path
        )
        if manifest_failures:
            print(json.dumps({
                "result": "FAIL",
                "historical_identity": "FAIL",
                "manifest": manifest_path.relative_to(root).as_posix(),
                "failures": manifest_failures,
            }, indent=2, sort_keys=True))
            return 1

    tool_dir = Path(__file__).resolve().parent
    contract_validator = tool_dir / "validate_sequence_contract.py"
    if not contract_validator.is_file():
        contract_validator = root / "scripts" / "validate_sequence_contract.py"
    if not contract_validator.is_file():
        return fail_payload("SEQUENCE_VALIDATOR_MISSING")

    human_validator = tool_dir / "validate_sequence_human_view.py"
    if not human_validator.is_file():
        human_validator = root / "scripts" / "validate_sequence_human_view.py"

    results = []
    failed = 0
    human_views = 0
    failed_human_views = 0
    identity_sessions = 0
    replayed_historical = 0
    child_env = os.environ.copy()
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"

    for session in sessions:
        rel = session.relative_to(root).as_posix()
        try:
            session_data = load_session(session)
        except Exception:
            session_data = {}
        scope = session_scope(session_data)
        human_declared = isinstance(session_data.get("human_view"), dict) and bool(
            session_data.get("human_view")
        )
        if human_declared:
            human_views += 1

        if scope == "HISTORICAL" and frozen_identity_active and not args.replay_historical:
            identity_sessions += 1
            results.append({
                "session": rel,
                "scope": scope,
                "validation_mode": "FROZEN_IDENTITY",
                "returncode": 0,
                "output": "HISTORICAL_IDENTITY=PASS\n",
                "human_view_declared": human_declared,
                "human_view_returncode": 0,
                "human_view_output": (
                    "HISTORICAL_HUMAN_IDENTITY=PASS\n"
                    if human_declared
                    else "NOT_APPLICABLE:HUMAN_VIEW_NOT_DECLARED\n"
                ),
            })
            continue

        if scope == "HISTORICAL":
            replayed_historical += 1

        contract_rc, contract_output = run_validator(
            root, contract_validator, rel, child_env
        )
        human_rc = 0
        human_output = "NOT_APPLICABLE:HUMAN_VIEW_NOT_DECLARED\n"
        if human_declared:
            if not human_validator.is_file():
                human_rc = 1
                human_output = "HUMAN_SEQUENCE_VALIDATOR_MISSING\n"
            else:
                human_rc, human_output = run_validator(
                    root, human_validator, rel, child_env
                )
            if human_rc != 0:
                failed_human_views += 1

        session_failed = contract_rc != 0 or human_rc != 0
        if session_failed:
            failed += 1
        results.append({
            "session": rel,
            "scope": scope,
            "validation_mode": (
                "AUTHORIZED_REPLAY"
                if scope == "HISTORICAL" and args.replay_historical
                else "FULL_CURRENT"
                if scope == "CURRENT"
                else "LEGACY_REPLAY"
            ),
            "returncode": contract_rc,
            "output": contract_output,
            "human_view_declared": human_declared,
            "human_view_returncode": human_rc,
            "human_view_output": human_output,
        })

    report = {
        "sequence_required": policy.get("required", False),
        "runtime_trace_required": policy.get("runtime_trace_required", False),
        "sessions": len(sessions),
        "historical_sessions": len(historical),
        "historical_identity_active": frozen_identity_active,
        "historical_identity_sessions": identity_sessions,
        "historical_replayed_sessions": replayed_historical,
        "historical_manifest": (
            manifest_path.relative_to(root).as_posix() if manifest_path.is_file() else ""
        ),
        "historical_manifest_sha256": (
            sha256_file(manifest_path) if manifest_path.is_file() else ""
        ),
        "frozen_commit": manifest.get("frozen_commit", "") if manifest else "",
        "failed_sessions": failed,
        "human_views": human_views,
        "failed_human_views": failed_human_views,
        "results": results,
        "result": "FAIL" if failed else "PASS",
    }
    print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

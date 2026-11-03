#!/usr/bin/env python3
"""Regression tests for SW2-16 historical evidence freeze semantics."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from validate_sequence_sessions import (
    build_historical_manifest,
    evidence_paths,
    historical_sessions,
    verify_historical_manifest,
    write_json,
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def git_run(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def fixture() -> None:
    with tempfile.TemporaryDirectory(prefix="sw2-historical-") as td:
        root = Path(td).resolve()
        git_run(root, "init")
        git_run(root, "config", "user.email", "sw2@example.invalid")
        git_run(root, "config", "user.name", "SW2 Test")

        session_dir = root / "docs" / "sequence" / "sessions"
        generated = root / "docs" / "sequence" / "generated"
        views = root / "docs" / "sequence" / "views"
        artifacts = root / "artifacts" / "sequence"
        workflow = root / ".workflow"
        for path in (session_dir, generated, views, artifacts, workflow):
            path.mkdir(parents=True, exist_ok=True)

        actual_json = generated / "OLD.actual.json"
        actual_mmd = generated / "OLD.actual.mmd"
        human_json = generated / "OLD.human.json"
        human_mmd = generated / "OLD.human.mmd"
        human_md = views / "OLD.md"
        acceptance = artifacts / "OLD.acceptance.json"
        actual_json.write_text('{"source_digest":"abc","generated":true}\n', encoding="utf-8", newline="\n")
        actual_mmd.write_text("sequenceDiagram\n", encoding="utf-8", newline="\n")
        human_json.write_text('{"source_digest":"abc"}\n', encoding="utf-8", newline="\n")
        human_mmd.write_text("sequenceDiagram\n", encoding="utf-8", newline="\n")
        human_md.write_text("# Historical view\n", encoding="utf-8", newline="\n")
        acceptance.write_text('{"result":"PASS"}\n', encoding="utf-8", newline="\n")

        session_path = session_dir / "OLD.json"
        session = {
            "schema_version": 1,
            "session_id": "OLD",
            "phase": "OLD",
            "scope": "HISTORICAL",
            "mode": "DURING",
            "acceptance_report": "artifacts/sequence/OLD.acceptance.json",
            "actual": {
                "graph": "docs/sequence/generated/OLD.actual.json",
                "diagram": "docs/sequence/generated/OLD.actual.mmd",
                "source_digest": "abc",
            },
            "human_view": {
                "graph": "docs/sequence/generated/OLD.human.json",
                "diagram": "docs/sequence/generated/OLD.human.mmd",
                "document": "docs/sequence/views/OLD.md",
                "source_digest": "abc",
            },
            "plan": {"required": False, "contract": "", "diagram": ""},
            "runtime_trace": {"required": False, "graph": ""},
        }
        session_path.write_text(
            json.dumps(session, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        git_run(root, "add", ".")
        git_run(root, "commit", "-m", "historical fixture")
        frozen_commit = git(root, "rev-parse", "HEAD")
        sessions = [session_path]

        try:
            build_historical_manifest(
                root,
                sessions,
                frozen_commit=frozen_commit,
                authorization_reason="",
                generator_path=Path(__file__),
            )
        except ValueError as exc:
            require(
                "HISTORICAL_MIGRATION_AUTHORIZATION_REQUIRED" in str(exc),
                f"wrong migration authorization failure: {exc}",
            )
        else:
            raise AssertionError("missing migration authorization produced a false PASS")
        print("HISTORICAL_MIGRATION_AUTHORIZATION=PASS")

        first = build_historical_manifest(
            root,
            sessions,
            frozen_commit=frozen_commit,
            authorization_reason="fixture migration",
            generator_path=Path(__file__),
        )
        second = build_historical_manifest(
            root,
            sessions,
            frozen_commit=frozen_commit,
            authorization_reason="fixture migration",
            generator_path=Path(__file__),
        )
        require(first == second, "historical manifest was not deterministic")
        manifest_path = workflow / "historical_evidence.json"
        write_json(manifest_path, first)
        manifest_bytes = manifest_path.read_bytes()
        print("HISTORICAL_MANIFEST_DETERMINISM=PASS")

        historical = historical_sessions(sessions)
        require(historical == [session_path], "historical inventory drifted")
        expected_files = evidence_paths(root, session_path, session)
        before = {rel: (root / rel).read_bytes() for rel in expected_files}
        failures, _ = verify_historical_manifest(root, sessions, manifest_path)
        require(not failures, f"clean historical manifest failed: {failures}")
        after = {rel: (root / rel).read_bytes() for rel in expected_files}
        require(before == after, "normal identity verification rewrote historical evidence")
        require(manifest_path.read_bytes() == manifest_bytes, "manifest changed during verification")
        print("HISTORICAL_NO_REWRITE=PASS")

        original_actual = actual_json.read_bytes()
        actual_json.write_text('{"source_digest":"tampered"}\n', encoding="utf-8")
        failures, _ = verify_historical_manifest(root, sessions, manifest_path)
        require(
            any(item.startswith(("HISTORICAL_WORKTREE_DIRTY:", "HISTORICAL_CURRENT_HASH_MISMATCH:")) for item in failures),
            f"tampered evidence was not rejected: {failures}",
        )
        actual_json.write_bytes(original_actual)
        print("HISTORICAL_TAMPER_DETECTION=PASS")

        actual_json.write_text('{"source_digest":"different-commit"}\n', encoding="utf-8")
        git_run(root, "add", str(actual_json.relative_to(root)))
        git_run(root, "commit", "-m", "different historical bytes")
        different_commit = git(root, "rev-parse", "HEAD")
        git_run(root, "reset", "--hard", frozen_commit)
        altered = json.loads(manifest_bytes.decode("utf-8"))
        altered["frozen_commit"] = different_commit
        write_json(manifest_path, altered)
        failures, _ = verify_historical_manifest(root, sessions, manifest_path)
        require(
            any(item.startswith("HISTORICAL_FROZEN_HASH_MISMATCH:") for item in failures),
            f"frozen provenance mismatch was not rejected: {failures}",
        )
        manifest_path.write_bytes(manifest_bytes)
        print("HISTORICAL_FROZEN_PROVENANCE=PASS")


def replay_authorization_contract() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    tool = repo_root / "scripts" / "validate_sequence_sessions.py"
    proc = subprocess.run(
        [sys.executable, str(tool), "--root", str(repo_root), "--replay-historical"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    require(proc.returncode != 0, "historical replay without authorization passed")
    require(
        "HISTORICAL_REPLAY_AUTHORIZATION_REQUIRED" in proc.stdout,
        f"wrong replay authorization output: {proc.stdout}",
    )
    print("HISTORICAL_REPLAY_AUTHORIZATION=PASS")


def main() -> int:
    fixture()
    replay_authorization_contract()
    print("HISTORICAL_EVIDENCE_SELFTEST=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

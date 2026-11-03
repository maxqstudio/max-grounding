#!/usr/bin/env python3
"""Adversarial regression for schema migration and toolchain locking."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cwd: Path, *args: str, expect: int = 0) -> str:
    proc = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != expect:
        raise RuntimeError(
            "command failed expected="
            + str(expect)
            + " actual="
            + str(proc.returncode)
            + "\n"
            + proc.stdout
        )
    return proc.stdout


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="skill-workflow-schema-v1-") as td:
        root = Path(td)
        run(
            skill_root,
            sys.executable,
            str(skill_root / "scripts" / "initialize_project_truth.py"),
            "--root",
            str(root),
        )
        profile_path = root / "PROJECT_PROFILE.yaml"
        agents_path = root / "AGENTS.md"
        spec_root = root / ".workflow"
        tool_root = spec_root / "tools"
        lock_path = spec_root / "toolchain.lock.json"
        validator = tool_root / "validate_schema_toolchain.py"
        migration = skill_root / "scripts" / "migrate_governance_v1.py"

        profile_text = profile_path.read_text(encoding="utf-8")
        assert "schema_version: 1" in profile_text
        assert agents_path.is_file()
        assert lock_path.is_file()
        baseline = run(root, sys.executable, str(validator), "--root", str(root))
        assert '"result": "PASS"' in baseline

        tamper = tool_root / "sync_project_truth.py"
        tamper.write_text(
            tamper.read_text(encoding="utf-8") + "\n# tamper\n",
            encoding="utf-8",
            newline="\n",
        )
        tampered = run(
            root,
            sys.executable,
            str(validator),
            "--root",
            str(root),
            expect=1,
        )
        assert "TOOLCHAIN_MANIFEST_MISMATCH" in tampered
        run(root, sys.executable, str(migration), "--root", str(root))
        run(root, sys.executable, str(validator), "--root", str(root))

        profile_path.write_text(
            profile_path.read_text(encoding="utf-8").replace(
                "schema_version: 1\n", "", 1
            ),
            encoding="utf-8",
            newline="\n",
        )
        state_path = spec_root / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.pop("schema_version", None)
        write_json(state_path, state)
        flow_path = spec_root / "workflows" / "FLOW-EXAMPLE.json"
        flow = json.loads(flow_path.read_text(encoding="utf-8"))
        flow.pop("schema_version", None)
        write_json(flow_path, flow)
        lock_path.unlink()
        agents_path.unlink()

        legacy = run(
            root,
            sys.executable,
            str(validator),
            "--root",
            str(root),
            expect=1,
        )
        assert "PROJECT_PROFILE_SCHEMA_VERSION_MISSING" in legacy
        assert "SPEC_SCHEMA_VERSION_MISSING" in legacy
        assert "TOOLCHAIN_LOCK_MISSING" in legacy

        run(root, sys.executable, str(migration), "--root", str(root))
        migrated = run(root, sys.executable, str(validator), "--root", str(root))
        assert '"result": "PASS"' in migrated
        assert agents_path.is_file()
        stable_before = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in (agents_path, profile_path, state_path, flow_path, lock_path)
        }
        second = run(root, sys.executable, str(migration), "--root", str(root))
        stable_after = {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in (agents_path, profile_path, state_path, flow_path, lock_path)
        }
        assert stable_before == stable_after
        assert "MIGRATION_CHANGES=0" in second

        good_profile = profile_path.read_text(encoding="utf-8")
        bad_profile = good_profile.replace("schema_version: 1", "schema_version: 99", 1)
        profile_path.write_text(bad_profile, encoding="utf-8", newline="\n")
        unsupported_profile = run(
            root,
            sys.executable,
            str(migration),
            "--root",
            str(root),
            expect=1,
        )
        assert "PROJECT_PROFILE_SCHEMA_VERSION_UNSUPPORTED" in unsupported_profile
        assert profile_path.read_text(encoding="utf-8") == bad_profile
        profile_path.write_text(good_profile, encoding="utf-8", newline="\n")

        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["schema_version"] = 99
        write_json(state_path, state)
        bad_state = state_path.read_bytes()
        unsupported_spec = run(
            root,
            sys.executable,
            str(migration),
            "--root",
            str(root),
            expect=1,
        )
        assert "SPEC_SCHEMA_VERSION_UNSUPPORTED" in unsupported_spec
        assert state_path.read_bytes() == bad_state

    provenance_upgrade = run(
        skill_root,
        sys.executable,
        str(skill_root / "scripts" / "selftest_toolchain_provenance_upgrade.py"),
    )
    assert "EXACT_PRODUCER_IDENTITY=PASS" in provenance_upgrade
    assert "READ_ONLY_UPGRADE_PLAN=PASS" in provenance_upgrade
    assert "SEMANTIC_AUTHORITY_PRESERVATION=PASS" in provenance_upgrade
    assert "UPGRADE_IDEMPOTENCE=PASS" in provenance_upgrade
    assert "INTERRUPTED_UPGRADE_FAIL_CLOSED=PASS" in provenance_upgrade
    assert "LEGACY_PROVENANCE_MIGRATION=PASS" in provenance_upgrade
    assert "CONTENT_PACKAGE_IDENTITY=PASS" in provenance_upgrade
    assert "STRONG_PROVENANCE_NO_DOWNGRADE=PASS" in provenance_upgrade
    assert "RESULT=PASS" in provenance_upgrade

    print("SCHEMA_V1_BASELINE=PASS")
    print("ROOT_AGENTS_MIGRATION=PASS")
    print("LEGACY_EXPLICIT_MIGRATION=PASS")
    print("MIGRATION_IDEMPOTENCE=PASS")
    print("FUTURE_PROFILE_VERSION_REJECTION=PASS")
    print("FUTURE_SPEC_VERSION_REJECTION=PASS")
    print("TOOLCHAIN_TAMPER_REJECTION=PASS")
    print("CACHE_NOT_AUTHORITY=PASS")
    print("TOOLCHAIN_PROVENANCE_UPGRADE=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

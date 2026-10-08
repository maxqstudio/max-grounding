#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cwd: Path, *args: str, expect: int = 0) -> str:
    proc = subprocess.run(list(args), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != expect:
        raise RuntimeError(f"command failed expected={expect} actual={proc.returncode}\n{proc.stdout}")
    return proc.stdout


def fail(code: str) -> int:
    print("FAIL " + code)
    return 1


def producer(lock: dict) -> dict:
    value = lock.get("producer")
    return value if isinstance(value, dict) else {}


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    source_sha = run(skill_root, "git", "rev-parse", "HEAD").strip()
    with tempfile.TemporaryDirectory(prefix="skill-workflow-toolchain-upgrade-") as td:
        temp_root = Path(td)
        root = temp_root / "git-consumer"
        root.mkdir()
        run(skill_root, sys.executable, str(skill_root / "scripts" / "initialize_project_truth.py"), "--root", str(root))
        lock_path = root / ".workflow" / "toolchain.lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        identity = producer(lock)
        recorded_sha = str(identity.get("source_sha", "")).strip()
        recorded_digest = str(identity.get("source_digest", "")).strip()
        if str(identity.get("identity_source", "")).strip() != "GIT+CONTENT":
            return fail("GIT_CONTENT_IDENTITY_MISSING")
        if not re.fullmatch(r"[0-9a-f]{64}", recorded_digest):
            return fail("PRODUCER_SOURCE_DIGEST_MISSING")
        if not re.fullmatch(r"[0-9a-f]{40}", recorded_sha):
            return fail("PRODUCER_SOURCE_SHA_MISSING")
        if recorded_sha != source_sha:
            return fail("PRODUCER_SOURCE_SHA_MISMATCH")
        if not str(identity.get("repository", "")).strip() or identity.get("repository") == "NOT_PROVEN":
            return fail("PRODUCER_REPOSITORY_MISSING")
        if not str(identity.get("release", "")).strip() or identity.get("release") == "NOT_PROVEN":
            return fail("PRODUCER_RELEASE_IDENTITY_MISSING")
        if "commit_hint" in identity:
            return fail("LEGACY_COMMIT_HINT_STILL_AUTHORITATIVE")

        upgrader = skill_root / "scripts" / "upgrade_governance_toolchain.py"
        if not upgrader.is_file():
            return fail("UPGRADE_COMMAND_MISSING")
        baseline = run(skill_root, sys.executable, str(upgrader), "--root", str(root), "--check")
        if "UPGRADE_REQUIRED=NO" not in baseline or "RESULT=PASS" not in baseline:
            return fail("CURRENT_CHECK_NOT_CLEAN")
        plan_line = next((line for line in baseline.splitlines() if line.startswith("PLAN_JSON=")), "")
        if not plan_line:
            return fail("MACHINE_READABLE_PLAN_MISSING")
        plan = json.loads(plan_line.split("=", 1)[1])
        file_plan = plan.get("files") or {}
        if sorted(file_plan) != ["added", "changed", "removed", "unchanged", "unmanaged"]:
            return fail("PLAN_FILE_CLASSES_INCOMPLETE")

        tool = root / ".workflow" / "tools" / "sync_project_truth.py"
        tool.write_text(tool.read_text(encoding="utf-8") + "\n# sw2-22 tamper\n", encoding="utf-8", newline="\n")
        tampered_before = tool.read_bytes()
        check = run(skill_root, sys.executable, str(upgrader), "--root", str(root), "--check", expect=1)
        if "UPGRADE_REQUIRED=YES" not in check:
            return fail("TAMPER_NOT_PLANNED")
        if tool.read_bytes() != tampered_before:
            return fail("CHECK_MODE_MUTATED_CONSUMER")
        # Simulate an interrupted tool-copy: bytes changed but the previously
        # accepted lock did not. Validation must reject this half-updated state.
        invalid = run(root, sys.executable,
            str(root / ".workflow" / "tools" / "validate_schema_toolchain.py"),
            "--root", str(root), expect=1)
        if "TOOLCHAIN" not in invalid:
            return fail("INTERRUPTED_UPGRADE_FALSE_PASS")

        agents = root / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8") + "\nOWNER_SENTINEL\n", encoding="utf-8", newline="\n")
        state_path = root / ".workflow" / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["owner_sentinel"] = "preserve-me"
        state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8", newline="\n")

        applied = run(skill_root, sys.executable, str(upgrader), "--root", str(root), "--apply")
        if "RESULT=PASS" not in applied:
            return fail("APPLY_FAILED")
        if "OWNER_SENTINEL" not in agents.read_text(encoding="utf-8"):
            return fail("AGENTS_OVERWRITTEN")
        if json.loads(state_path.read_text(encoding="utf-8")).get("owner_sentinel") != "preserve-me":
            return fail("SEMANTIC_AUTHORITY_OVERWRITTEN")
        run(root, sys.executable, str(root / ".workflow" / "tools" / "validate_schema_toolchain.py"), "--root", str(root))

        second = run(skill_root, sys.executable, str(upgrader), "--root", str(root), "--apply")
        if "TOOL_FILES_WRITTEN=0" not in second or "LOCK_WRITTEN=NO" not in second:
            return fail("APPLY_NOT_IDEMPOTENT")

        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        lock["producer"] = {"repository": producer(lock)["repository"], "commit_hint": source_sha}
        lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        legacy_check = run(skill_root, sys.executable, str(upgrader), "--root", str(root), "--check", expect=1)
        if "UPGRADE_REQUIRED=YES" not in legacy_check:
            return fail("LEGACY_HINT_ONLY_NOT_DETECTED")
        run(skill_root, sys.executable, str(upgrader), "--root", str(root), "--apply")
        migrated = json.loads(lock_path.read_text(encoding="utf-8"))
        if str(producer(migrated).get("source_sha", "")) != source_sha:
            return fail("LEGACY_PROVENANCE_NOT_MIGRATED")
        if str(producer(migrated).get("identity_source", "")) != "GIT+CONTENT":
            return fail("LEGACY_PROVENANCE_NOT_STRENGTHENED")

        package_root = temp_root / "package-copy"
        shutil.copytree(skill_root / "scripts", package_root / "scripts")
        shutil.copytree(skill_root / "templates", package_root / "templates")
        if (package_root / ".git").exists():
            return fail("PACKAGE_COPY_UNEXPECTED_GIT_METADATA")

        package_consumer = temp_root / "package-consumer"
        package_consumer.mkdir()
        package_initializer = package_root / "scripts" / "initialize_project_truth.py"
        run(package_root, sys.executable, str(package_initializer), "--root", str(package_consumer))
        package_lock_path = package_consumer / ".workflow" / "toolchain.lock.json"
        package_lock = json.loads(package_lock_path.read_text(encoding="utf-8"))
        package_identity = producer(package_lock)
        package_digest = str(package_identity.get("source_digest", "")).strip()
        if str(package_identity.get("identity_source", "")) != "CONTENT":
            return fail("PACKAGE_CONTENT_IDENTITY_MISSING")
        if not re.fullmatch(r"[0-9a-f]{64}", package_digest):
            return fail("PACKAGE_SOURCE_DIGEST_MISSING")
        if package_identity.get("repository") != "NOT_PROVEN":
            return fail("PACKAGE_REPOSITORY_NOT_FAIL_CLOSED")
        if package_identity.get("source_sha") != "NOT_PROVEN":
            return fail("PACKAGE_SOURCE_SHA_NOT_FAIL_CLOSED")
        if package_identity.get("release") != "NOT_PROVEN":
            return fail("PACKAGE_RELEASE_NOT_FAIL_CLOSED")
        run(
            package_consumer,
            sys.executable,
            str(package_consumer / ".workflow" / "tools" / "validate_schema_toolchain.py"),
            "--root",
            str(package_consumer),
        )

        package_upgrader = package_root / "scripts" / "upgrade_governance_toolchain.py"
        no_downgrade = run(package_root, sys.executable, str(package_upgrader), "--root", str(root), "--check")
        if "UPGRADE_REQUIRED=NO" not in no_downgrade or "RESULT=PASS" not in no_downgrade:
            return fail("STRONG_PROVENANCE_DOWNGRADE_REQUESTED")
        preserved_lock = json.loads(lock_path.read_text(encoding="utf-8"))
        if str(producer(preserved_lock).get("identity_source", "")) != "GIT+CONTENT":
            return fail("STRONG_PROVENANCE_WAS_DOWNGRADED")

    print("EXACT_PRODUCER_IDENTITY=PASS")
    print("READ_ONLY_UPGRADE_PLAN=PASS")
    print("TOOL_OWNED_APPLY=PASS")
    print("SEMANTIC_AUTHORITY_PRESERVATION=PASS")
    print("UPGRADE_IDEMPOTENCE=PASS")
    print("INTERRUPTED_UPGRADE_FAIL_CLOSED=PASS")
    print("LEGACY_PROVENANCE_MIGRATION=PASS")
    print("CONTENT_PACKAGE_IDENTITY=PASS")
    print("STRONG_PROVENANCE_NO_DOWNGRADE=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

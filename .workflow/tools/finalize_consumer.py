#!/usr/bin/env python3
"""Fail-closed consumer final acceptance; producer selftests remain producer-only.

Re-executes the consumer-owned source tests from .workflow/acceptance.json,
and all required strict project validators on a clean exact Git HEAD.
Does not promote any historical runtime or external CI evidence.
"""
from __future__ import annotations
import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

SHA = re.compile(r"^[0-9a-f]{40}$")
VALIDATORS = (
    ("schema_toolchain", "validate_schema_toolchain.py", ()),
    ("project_docs", "validate_project_docs.py", ()),
    ("doc_quality", "validate_doc_quality.py", ()),
    ("human_comprehension", "validate_human_comprehension.py", ("--require-pass",)),
    ("sequence_sessions", "validate_sequence_sessions.py", ()),
    ("handoff", "validate_handoff.py", ()),
    ("cross_document", "validate_cross_document_consistency.py", ("--base", "{base}", "--require-base")),
    ("project_truth", "validate_project_truth.py", ("--project-docs-already-validated",)),
)
SOURCE_TEST_PREFIXES = (
    ("python", "-m", "unittest"), ("python", "-m", "pytest"),
    ("pytest",), ("go", "test"), ("cargo", "test"),
    ("dotnet", "test"), ("npm", "test"), ("flutter", "test"),
)


def git(root: Path, *args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(root), *args], text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return p.returncode, p.stdout.strip()


def require_clean(root: Path) -> None:
    rc, status = git(root, "status", "--porcelain", "--untracked-files=all")
    if rc or status:
        raise ValueError("CONSUMER_FINALIZE_WORKTREE_DIRTY:" + status[:240])


def parse_owner_command(raw: str) -> list[str]:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("TEST_COMMAND_EMPTY")
    if any(ord(c) < 32 for c in raw):
        raise ValueError("TEST_COMMAND_CONTROL_CHAR")
    try:
        parts = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise ValueError("TEST_COMMAND_INVALID_QUOTING") from exc
    if not parts or parts[0].lower() in {"sh", "bash", "cmd", "cmd.exe", "powershell", "pwsh"}:
        raise ValueError("TEST_COMMAND_SHELL_FORBIDDEN")
    if any(any(c in p for c in (";", "|", "<", ">", "&", "\u0060"))
           or "$(" in p or "$" + "{" in p for p in parts):
        raise ValueError("TEST_COMMAND_SHELL_OPERATOR_FORBIDDEN")
    if parts[0].lower() in {"python", "python3", "py"}:
        if any(p in ("-c", "-e") for p in parts[1:]):
            raise ValueError("TEST_COMMAND_INLINE_CODE_FORBIDDEN")
        parts[0] = sys.executable
    return parts


def is_source_test(command: list[str]) -> bool:
    normalized = ["python" if p == sys.executable else p for p in command]
    return any(tuple(normalized[:len(prefix)]) == prefix for prefix in SOURCE_TEST_PREFIXES)


def owner_tests(root: Path) -> list[list[str]]:
    path = root / ".workflow/acceptance.json"
    if not path.is_file():
        raise ValueError("CONSUMER_ACCEPTANCE_SPEC_MISSING")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("CONSUMER_ACCEPTANCE_SCHEMA_UNSUPPORTED")
    entries = data.get("test_commands")
    if not isinstance(entries, list) or not entries:
        raise ValueError("CONSUMER_SOURCE_TEST_COMMANDS_MISSING")
    parsed = [parse_owner_command(raw) for raw in entries]
    if not any(is_source_test(argv) for argv in parsed):
        raise ValueError("CONSUMER_SOURCE_TEST_AUTHORITY_MISSING")
    return parsed


def plan(root: Path, expected_head: str, base: str) -> tuple[list[tuple[str, list[str]]], dict]:
    root = root.resolve()
    if not root.is_dir() or not SHA.fullmatch(expected_head) or not SHA.fullmatch(base):
        raise ValueError("CONSUMER_FINALIZE_EXACT_SHA_REQUIRED")
    rc, head = git(root, "rev-parse", "HEAD")
    if rc or head != expected_head:
        raise ValueError("CONSUMER_FINALIZE_HEAD_MISMATCH:" + head)
    rc, _ = git(root, "merge-base", "--is-ancestor", base, expected_head)
    if rc:
        raise ValueError("CONSUMER_FINALIZE_ACCEPTED_BASE_NOT_ANCESTOR")
    require_clean(root)
    tests = owner_tests(root)
    gates = [("source_test_" + str(i), args) for i, args in enumerate(tests, 1)]
    for name, filename, rest in VALIDATORS:
        script = root / ".workflow/tools" / filename
        if not script.is_file() or not script.resolve().is_relative_to(root):
            raise ValueError("MANDATORY_CONSUMER_VALIDATOR_MISSING:" + filename)
        argv = [sys.executable, str(script), "--root", str(root)]
        argv.extend(base if arg == "{base}" else arg for arg in rest)
        gates.append((name, argv))
    meta = {"expected_head": expected_head, "base": base,
            "source_test_commands": len(tests), "required_validators": len(VALIDATORS),
            "producer_selftests": "NOT_APPLICABLE_ON_CONSUMER",
            "runtime_e2e": "NOT_REEXECUTED_BY_THIS_COMMAND",
            "final_acceptance_authority": True}
    return gates, meta


def finalize(root: Path, expected_head: str, base: str) -> dict:
    result = {"schema_version": 1, "result": "FAIL", "first_failed_gate": "",
        "gates": [], "consumer_source_head": expected_head,
        "source_tests_executed": 0,
        "evidence_boundary": "Reexecutes Owner-declared consumer source tests and complete STRICT validators on one clean HEAD; historical runtime E2E and external CI remain independent."}
    try:
        root = root.resolve()
        gates, meta = plan(root, expected_head, base)
        result.update(meta)
        for name, command in gates:
            t = time.monotonic()
            p = subprocess.run(command, cwd=root, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            item = {"gate": name, "exit_code": p.returncode,
                "seconds": round(time.monotonic() - t, 3),
                "result": "PASS" if p.returncode == 0 else "FAIL",
                "output_tail": p.stdout[-1400:]}
            result["gates"].append(item)
            if p.returncode:
                result["first_failed_gate"] = name
                return result
            if name.startswith("source_test_"):
                # unittest exits 0 even if zero tests are discovered.
                if is_source_test(command) and "-m" in command and "unittest" in command:
                    found = re.search(r"Ran (\d+) tests?", p.stdout)
                    if found is None or int(found.group(1)) == 0:
                        item["result"] = "FAIL"
                        item["failure"] = "CONSUMER_SOURCE_TEST_ZERO_OR_UNVERIFIED"
                        result["first_failed_gate"] = name + ":NO_TESTS"
                        return result
                result["source_tests_executed"] += 1
            try:
                require_clean(root)
                rc, after = git(root, "rev-parse", "HEAD")
                if rc or after != expected_head:
                    raise ValueError("CONSUMER_FINALIZE_HEAD_CHANGED")
            except ValueError as exc:
                item["result"] = "FAIL"
                item["failure"] = str(exc)
                result["first_failed_gate"] = name + ":SOURCE_MUTATION"
                return result
        result["result"] = "PASS"
    except Exception as exc:
        result["first_failed_gate"] = type(exc).__name__ + ":" + str(exc)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    result = finalize(Path(args.root), args.expected_head, args.base)
    if args.report:
        location = Path(args.report).resolve()
        if location.is_relative_to(Path(args.root).resolve()):
            print("CONSUMER_FINALIZE=FAIL")
            print("FIRST_FAILED_GATE=REPORT_MUST_BE_OUTSIDE_CONSUMER")
            return 1
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print("CONSUMER_FINALIZE_JSON=" + json.dumps(result, sort_keys=True, separators=(",", ":")))
    print("CONSUMER_FINALIZE=" + result["result"])
    if result["first_failed_gate"]:
        print("FIRST_FAILED_GATE=" + result["first_failed_gate"])
    return 0 if result["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

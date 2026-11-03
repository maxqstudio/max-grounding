#!/usr/bin/env python3
"""Hermetic, cross-platform negative-path tests for SW2-23 consumer classification."""
import copy
import json
from pathlib import Path
import validate_consumer_compatibility_matrix as cc

SHA1 = "a" * 40
SHA2 = "b" * 40
SHA3 = "c" * 40
PROFILE = b"schema_version: 1\nprofile: strict\n"
LEGACY_PROFILE = b"profile: strict\n"
AGENTS = b"# AGENTS.md\nRead project authority before mutation.\n"
HINT_LOCK = json.dumps({"schema_version": 1, "producer": {
    "repository": "https://github.com/maxqstudio/Skill_Workflow",
    "commit_hint": SHA1}}).encode()
CONTENT_LOCK = json.dumps({"schema_version": 1, "producer": {
    "identity_source": "CONTENT", "source_digest": "d" * 64,
    "repository": "NOT_PROVEN", "source_sha": "NOT_PROVEN",
    "release": "NOT_PROVEN"}}).encode()


def assert_class(files: dict, expected: str) -> None:
    observed = cc.classify(files)["classification"]
    assert observed == expected, (expected, observed)


def main() -> int:
    a = {"PROJECT_PROFILE.yaml": LEGACY_PROFILE, "AGENTS.md": None,
         ".workflow/toolchain.lock.json": None}
    b = {"PROJECT_PROFILE.yaml": PROFILE, "AGENTS.md": AGENTS,
         ".workflow/toolchain.lock.json": HINT_LOCK}
    c = {"PROJECT_PROFILE.yaml": PROFILE, "AGENTS.md": None,
         ".workflow/toolchain.lock.json": None}
    assert_class(a, "LEGACY_PRECONDITIONS_MISSING")
    assert_class(b, "EXPLICIT_MIGRATION_REQUIRED")
    assert_class(c, "LEGACY_PRECONDITIONS_MISSING")
    assert_class({**c, "AGENTS.md": AGENTS}, "LEGACY_PRECONDITIONS_MISSING")
    assert_class({**b, ".workflow/toolchain.lock.json": b"garbage"}, "MALFORMED_LOCK")
    assert_class({**b, ".workflow/toolchain.lock.json": CONTENT_LOCK}, "REQUIRES_FULL_VALIDATION")
    unsupported = json.loads(CONTENT_LOCK)
    unsupported["producer"]["source_sha"] = SHA1
    assert_class({**b, ".workflow/toolchain.lock.json": json.dumps(unsupported).encode()},
                 "UNSUPPORTED_IDENTITY")
    assert_class({**b, "PROJECT_PROFILE.yaml": LEGACY_PROFILE}, "UNSUPPORTED_SCHEMA")
    assert_class({**b, "PROJECT_PROFILE.yaml": None}, "PROFILE_MISSING")
    matrix = {
       "schema_version": 1,
       "consumer_snapshots": [
           {"repository": "maxqstudio/max-grounding", "sha": SHA1,
            "profile": "strict", "root_agents": "MISSING", "toolchain_lock": "MISSING",
            "classification": "LEGACY_PRECONDITIONS_MISSING"},
           {"repository": "maxqstudio/DoctorCode", "sha": SHA2,
            "profile": "strict", "root_agents": "PRESENT", "toolchain_lock": "LEGACY_HINT_ONLY",
            "classification": "EXPLICIT_MIGRATION_REQUIRED"},
           {"repository": "maxqstudio/max-remote-commander", "sha": SHA3,
            "profile": "strict", "root_agents": "MISSING", "toolchain_lock": "MISSING",
            "classification": "LEGACY_PRECONDITIONS_MISSING"}
       ]
    }
    inputs = {SHA1: a, SHA2: b, SHA3: c}
    frozen_before = copy.deepcopy(inputs)
    def source(repository: str, sha: str) -> dict:
        assert repository in {x["repository"] for x in matrix["consumer_snapshots"]}
        return inputs[sha]
    report = cc.audit(matrix, fetcher=source)
    assert report["result"] == "PASS", report
    assert report["full_adoption_proven"] is False
    assert all(x["adoption_authority"] == "NOT_PROVEN" for x in report["snapshots"])
    assert inputs == frozen_before, "read-only audit mutated fixture input"
    tampered = copy.deepcopy(matrix)
    tampered["consumer_snapshots"][0]["root_agents"] = "PRESENT"
    report = cc.audit(tampered, fetcher=source)
    assert report["result"] == "FAIL", report
    assert report["failures"] == ["PINNED_CONSUMER_CLASSIFICATION_MISMATCH:maxqstudio/max-grounding"]
    bad_pin = copy.deepcopy(matrix)
    bad_pin["consumer_snapshots"][0]["sha"] = "mutable-main"
    try:
        cc.audit(bad_pin, fetcher=source)
        raise AssertionError("mutable ref was accepted")
    except ValueError as exc:
        assert "MATRIX_PIN_INVALID" in str(exc)
    print("CONSUMER_READ_ONLY_PINNED_MATRIX=PASS")
    print("CONSUMER_INCOMPATIBILITY_IS_NOT_ADOPTION=PASS")
    print("CONSUMER_TAMPER_AND_MUTABLE_REF=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

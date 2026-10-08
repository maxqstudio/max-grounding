#!/usr/bin/env python3
"""Regression tests for repository-health and Owner-controlled license handling."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from validate_repository_health import REQUIRED_HEALTH_FILES, REQUIRED_ROOT_FILES, validate


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / "README.md").write_text(
            "# Fixture\n\n" + "\n".join(f"[{name}]({name})" for name in REQUIRED_HEALTH_FILES) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for relative in REQUIRED_ROOT_FILES + REQUIRED_HEALTH_FILES:
            (root / relative).write_text(f"# {relative}\n\nFixture policy.\n", encoding="utf-8", newline="\n")
        write_json(root / ".workflow" / "decisions.json", {"schema_version": 1, "decisions": []})

        baseline = validate(root)
        assert baseline["result"] == "PASS"
        assert baseline["license_status"] == "NOT_PROVEN"
        assert baseline["temporary_workflows"] == []
        assert validate(root, require_owner_approved_license=True)["result"] == "FAIL"

        workflow_root = root / ".github" / "workflows"
        workflow_root.mkdir(parents=True, exist_ok=True)
        temporary_workflow = workflow_root / "tmp-stale-proof.yml"
        temporary_workflow.write_text("name: temporary fixture\n", encoding="utf-8", newline="\n")
        stale = validate(root)
        assert stale["result"] == "FAIL"
        assert stale["temporary_workflows"] == [".github/workflows/tmp-stale-proof.yml"]
        assert "TEMPORARY_WORKFLOW_TRACKED:.github/workflows/tmp-stale-proof.yml" in stale["failures"]
        temporary_workflow.unlink()
        assert validate(root)["result"] == "PASS"

        (root / "LICENSE").write_text("Fixture license text.\n", encoding="utf-8", newline="\n")
        assert validate(root)["result"] == "FAIL"

        write_json(
            root / ".workflow" / "decisions.json",
            {
                "schema_version": 1,
                "decisions": [
                    {
                        "id": "SW2-ADR-010",
                        "title": "Owner-approved public license",
                        "decision": "Fixture license is explicitly approved by the Owner.",
                        "rationale": "Fixture evidence only.",
                        "status": "ACCEPTED",
                    }
                ],
            },
        )
        approved = validate(root, require_owner_approved_license=True)
        assert approved["result"] == "PASS"
        assert approved["license_status"] == "PASS"

        (root / "AGENTS.md").unlink()
        missing_agents = validate(root)
        assert missing_agents["result"] == "FAIL"
        assert "REPOSITORY_ROOT_FILE_MISSING:AGENTS.md" in missing_agents["failures"]
        (root / "AGENTS.md").write_text("# AGENTS.md\n\nFixture policy.\n", encoding="utf-8", newline="\n")

        (root / "SECURITY.md").unlink()
        assert validate(root)["result"] == "FAIL"

    print("REPOSITORY_HEALTH_BASELINE=PASS")
    print("TEMPORARY_WORKFLOW_REJECTION=PASS")
    print("LICENSE_NOT_PROVEN_WITHOUT_OWNER_DECISION=PASS")
    print("UNAPPROVED_LICENSE_REJECTION=PASS")
    print("OWNER_APPROVED_LICENSE_FIXTURE=PASS")
    print("MISSING_ROOT_AGENTS_REJECTION=PASS")
    print("MISSING_SECURITY_POLICY_REJECTION=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

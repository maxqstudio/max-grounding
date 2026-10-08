#!/usr/bin/env python3
"""Validate repository-health files without inventing Owner license authority."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from validate_doc_quality import GENERATED_MARKER, normalized_shape

REQUIRED_HEALTH_FILES = (
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
)
REQUIRED_ROOT_FILES = ("AGENTS.md",)
README_HEALTH_LINKS = REQUIRED_HEALTH_FILES
LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt")
LICENSE_DECISION_ID = "SW2-ADR-010"
TEMPORARY_WORKFLOW_PREFIXES = ("tmp-",)


def _load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _temporary_workflows(root: Path) -> list[str]:
    workflow_root = root / ".github" / "workflows"
    if not workflow_root.is_dir():
        return []
    return sorted(
        path.relative_to(root).as_posix()
        for path in workflow_root.iterdir()
        if path.is_file() and path.name.startswith(TEMPORARY_WORKFLOW_PREFIXES)
    )


def validate(root: Path, *, require_owner_approved_license: bool = False) -> dict[str, object]:
    failures: list[str] = []
    checked: list[str] = []

    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8", errors="strict") if readme_path.is_file() else ""
    if not readme:
        failures.append("README_MISSING")
    else:
        for relative in README_HEALTH_LINKS:
            if relative not in readme:
                failures.append("README_HEALTH_LINK_MISSING:" + relative)

    for relative in REQUIRED_ROOT_FILES:
        path = root / relative
        if not path.is_file():
            failures.append("REPOSITORY_ROOT_FILE_MISSING:" + relative)
            continue
        checked.append(relative)
        text = path.read_text(encoding="utf-8", errors="strict")
        if GENERATED_MARKER in text:
            failures.append("REPOSITORY_ROOT_FILE_GENERATED:" + relative)
        ok, shape = normalized_shape(text)
        if not ok:
            failures.extend("REPOSITORY_ROOT_FILE_QUALITY:" + relative + ":" + item for item in shape)

    for relative in REQUIRED_HEALTH_FILES:
        path = root / relative
        if not path.is_file():
            failures.append("REPOSITORY_HEALTH_FILE_MISSING:" + relative)
            continue
        checked.append(relative)
        text = path.read_text(encoding="utf-8", errors="strict")
        if GENERATED_MARKER in text:
            failures.append("REPOSITORY_HEALTH_FILE_GENERATED:" + relative)
        ok, shape = normalized_shape(text)
        if not ok:
            failures.extend("REPOSITORY_HEALTH_QUALITY:" + relative + ":" + item for item in shape)

    temporary_workflows = _temporary_workflows(root)
    for relative in temporary_workflows:
        failures.append("TEMPORARY_WORKFLOW_TRACKED:" + relative)

    license_paths = [name for name in LICENSE_FILES if (root / name).is_file()]
    decisions_path = root / ".workflow" / "decisions.json"
    decision_accepted = False
    if decisions_path.is_file():
        try:
            decisions = _load_json(decisions_path).get("decisions", [])
        except (OSError, ValueError, TypeError):
            decisions = []
        for item in decisions if isinstance(decisions, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == LICENSE_DECISION_ID and item.get("status") == "ACCEPTED":
                decision_accepted = True
                break

    if license_paths and not decision_accepted:
        failures.append("UNAPPROVED_LICENSE_PRESENT:" + ",".join(sorted(license_paths)))
    if decision_accepted and not license_paths:
        failures.append("APPROVED_LICENSE_FILE_MISSING")

    license_proven = bool(license_paths and decision_accepted)
    if require_owner_approved_license and not license_proven:
        failures.append("OWNER_APPROVED_LICENSE_NOT_PROVEN")

    return {
        "schema_version": 1,
        "result": "FAIL" if failures else "PASS",
        "repository_health_files_checked": checked,
        "required_root_files": list(REQUIRED_ROOT_FILES),
        "temporary_workflows": temporary_workflows,
        "temporary_workflow_prefixes": list(TEMPORARY_WORKFLOW_PREFIXES),
        "license_files": license_paths,
        "license_decision_id": LICENSE_DECISION_ID,
        "license_status": "PASS" if license_proven else "NOT_PROVEN",
        "require_owner_approved_license": require_owner_approved_license,
        "failures": failures,
        "evidence_boundary": (
            "This gate proves mandatory root AGENTS.md plus repository-health file presence/shape, rejects tracked temporary workflows, "
            "and guards against an unapproved license file. It does not infer an Owner license choice. License PASS requires an "
            "accepted SW2-ADR-010 decision plus a license file."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--require-owner-approved-license", action="store_true")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    report = validate(root, require_owner_approved_license=args.require_owner_approved_license)
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.report:
        output = Path(args.report)
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

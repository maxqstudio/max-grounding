#!/usr/bin/env python3
"""Validate Skill Workflow's source-authored public documentation layer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import validate_documentation_contract as documentation_contract
from validate_doc_quality import GENERATED_MARKER, normalized_shape

PUBLIC_MARKER = "PUBLIC PRODUCT DOCUMENTATION - SOURCE-AUTHORED"
REQUIRED_PUBLIC_FILES = (
    "docs/README.md",
    "docs/handbook/README.md",
    "docs/handbook/getting-started/installation.md",
    "docs/handbook/getting-started/adoption.md",
    "docs/handbook/concepts/governance-model.md",
    "docs/handbook/guides/troubleshooting.md",
    "docs/handbook/reference/commands.md",
    "docs/handbook/reference/versioning.md",
    "docs/handbook/reference/repository-governance.md",
    "docs/handbook/reference/release-process.md",
    "docs/handbook/reference/documentation-system.md",
    "docs/handbook/architecture/validation-engine.md",
    "docs/handbook/sequence/README.md",
)
README_REQUIRED_HEADINGS = (
    "# Skill Workflow",
    "## Why Skill Workflow",
    "## Quick start",
    "## How it works",
    "## Documentation",
    "## Supported agents",
    "## Project state and governance",
    "## Support",
)
README_DEEP_REFERENCE_HEADINGS = (
    "## Project Truth Compiler",
    "## Why SYMBOL_INDEX matters",
    "## Automatic structural indexing",
    "## Generated Sequence Contract acceptance",
    "## Agent discipline: docs are a hard gate",
    "## Project Truth Synchronization",
    "## Cross-document consistency validator",
)
HANDBOOK_LINKS = (
    "getting-started/installation.md",
    "getting-started/adoption.md",
    "concepts/governance-model.md",
    "guides/troubleshooting.md",
    "reference/commands.md",
    "reference/versioning.md",
    "reference/repository-governance.md",
    "reference/release-process.md",
    "reference/documentation-system.md",
    "architecture/validation-engine.md",
    "sequence/README.md",
)
GENERATED_REFERENCE_FILES = (
    "docs/SYSTEM_OVERVIEW.md",
    "docs/CURRENT_STATE.md",
    "docs/ROADMAP.md",
    "docs/PROJECT_TRUTH_SYNC.md",
    "docs/MODULE_MAP.md",
    "docs/FLOW_INDEX.md",
    "docs/SYMBOL_INDEX.md",
)
PUBLIC_CONTENT_REQUIREMENTS = {
    "docs/handbook/reference/versioning.md": (
        "## Explicit migration",
        "## Rollback after migration",
        "git revert <migration-commit>",
        "validate_schema_toolchain.py",
        "sync_project_truth.py",
    ),
    "docs/handbook/reference/release-process.md": (
        "## Publication transaction",
        "## Release rollback",
        "Never move or retarget an existing stable tag",
        "semantic patch release",
    ),
    "docs/handbook/reference/documentation-system.md": (
        "## Documentation layers",
        "## GitHub-facing sequence views",
        "Generated Markdown is a projection",
        "docs/sequence/views/",
    ),
}


def validate(root: Path) -> dict[str, object]:
    failures: list[str] = []
    public_files_checked = 0

    readme_path = root / "README.md"
    if not readme_path.is_file():
        failures.append("README_MISSING")
        readme = ""
    else:
        readme = readme_path.read_text(encoding="utf-8", errors="strict")
        ok, shape = normalized_shape(readme)
        if not ok:
            failures.extend("README_QUALITY:" + item for item in shape)
        for heading in README_REQUIRED_HEADINGS:
            if heading not in readme:
                failures.append("README_SECTION_MISSING:" + heading)
        for heading in README_DEEP_REFERENCE_HEADINGS:
            if heading in readme:
                failures.append("README_DEEP_REFERENCE_SECTION_PRESENT:" + heading)
        if "docs/handbook/README.md" not in readme:
            failures.append("README_HANDBOOK_LINK_MISSING")
        for machine_index in ("docs/SYMBOL_INDEX.md", "docs/MODULE_MAP.md", "docs/FLOW_INDEX.md"):
            if machine_index in readme:
                failures.append("README_MACHINE_INDEX_DIRECT_LINK:" + machine_index)

    for relative in REQUIRED_PUBLIC_FILES:
        path = root / relative
        if not path.is_file():
            failures.append("PUBLIC_DOC_MISSING:" + relative)
            continue
        public_files_checked += 1
        text = path.read_text(encoding="utf-8", errors="strict")
        if PUBLIC_MARKER not in text:
            failures.append("PUBLIC_DOC_MARKER_MISSING:" + relative)
        if GENERATED_MARKER in text:
            failures.append("PUBLIC_DOC_GENERATED_MARKER_PRESENT:" + relative)
        ok, shape = normalized_shape(text)
        if not ok:
            failures.extend("PUBLIC_DOC_QUALITY:" + relative + ":" + item for item in shape)
        for required in PUBLIC_CONTENT_REQUIREMENTS.get(relative, ()):
            if required not in text:
                failures.append("PUBLIC_DOC_CONTENT_MISSING:" + relative + ":" + required)

    docs_index = root / "docs" / "README.md"
    if docs_index.is_file():
        text = docs_index.read_text(encoding="utf-8")
        if "handbook/README.md" not in text:
            failures.append("DOCS_INDEX_HANDBOOK_LINK_MISSING")
        for reference in ("SYSTEM_OVERVIEW.md", "CURRENT_STATE.md", "ROADMAP.md", "PROJECT_TRUTH_SYNC.md"):
            if reference not in text:
                failures.append("DOCS_INDEX_GOVERNANCE_LINK_MISSING:" + reference)

    handbook_index = root / "docs" / "handbook" / "README.md"
    if handbook_index.is_file():
        text = handbook_index.read_text(encoding="utf-8")
        for link in HANDBOOK_LINKS:
            if link not in text:
                failures.append("HANDBOOK_NAV_LINK_MISSING:" + link)

    for relative in GENERATED_REFERENCE_FILES:
        path = root / relative
        if not path.is_file():
            failures.append("GENERATED_GOVERNANCE_REFERENCE_MISSING:" + relative)
            continue
        if GENERATED_MARKER not in path.read_text(encoding="utf-8", errors="strict"):
            failures.append("GENERATED_GOVERNANCE_MARKER_MISSING:" + relative)

    documentation_contract_status = "NOT_APPLICABLE"
    if (root / ".git").exists():
        documentation_report = documentation_contract.build_report(root)
        documentation_contract_status = str(documentation_report.get("result", "FAIL"))
        expected_coverage = documentation_contract.report_text(documentation_report)
        coverage_path = root / documentation_contract.REPORT_PATH
        if not coverage_path.is_file():
            failures.append("DOCUMENTATION_COVERAGE_REPORT_MISSING:" + documentation_contract.REPORT_PATH)
        else:
            try:
                actual_coverage = coverage_path.read_text(encoding="utf-8", errors="strict")
            except Exception as exc:
                failures.append("DOCUMENTATION_COVERAGE_REPORT_READ_FAILURE:" + type(exc).__name__)
            else:
                if actual_coverage != expected_coverage:
                    failures.append("DOCUMENTATION_COVERAGE_REPORT_STALE:" + documentation_contract.REPORT_PATH)
        for finding in documentation_report.get("freshness_failures", []):
            if not isinstance(finding, dict):
                continue
            failures.append(
                "DOCUMENTATION_FRESHNESS:"
                + str(finding.get("kind", "UNKNOWN"))
                + ":"
                + str(finding.get("path", "UNKNOWN"))
                + ":"
                + str(finding.get("line", 0))
            )

    return {
        "schema_version": 1,
        "result": "FAIL" if failures else "PASS",
        "public_files_checked": public_files_checked,
        "public_marker": PUBLIC_MARKER,
        "public_root": "docs/handbook",
        "generated_governance_root": "docs",
        "canonical_consumer_layout_changed": False,
        "documentation_contract": documentation_contract_status,
        "failures": failures,
        "evidence_boundary": (
            "This gate proves public documentation structure, navigation, source-authored/generated-layer separation, "
            "deterministic tracked-document coverage/freshness, required migration/rollback guidance, and obvious README "
            "reference-manual regressions. It does not by itself prove that an operational rollback has been executed "
            "against an external consumer."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    report = validate(Path(args.root).resolve())
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.report:
        output = Path(args.report)
        if not output.is_absolute():
            output = Path(args.root).resolve() / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8", newline="\n")
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

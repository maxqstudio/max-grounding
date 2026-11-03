#!/usr/bin/env python3
"""Deterministic fail-closed impact graph for Project Truth projections.

Incremental planning is intermediate evidence only. Unknown or ambiguous
paths broaden to the complete generated document set. Final acceptance must
still use exhaustive Project Truth generation/validation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from project_snapshot import SOURCE_EXTENSIONS


DOC_DEPENDENCIES: dict[str, frozenset[str]] = {
    "SYSTEM_OVERVIEW.md": frozenset({"project", "architecture", "state", "acceptance", "authority", "workflows", "facts"}),
    "PROJECT_MANIFEST.md": frozenset({"project", "state", "authority", "facts"}),
    "CURRENT_STATE.md": frozenset({"state", "roadmap", "acceptance", "project", "facts"}),
    "ROADMAP.md": frozenset({"roadmap", "state", "acceptance"}),
    "SOURCE_AUTHORITY_MAP.md": frozenset({"authority"}),
    "ARCHITECTURE.md": frozenset({"architecture", "facts"}),
    "WORKFLOW_STATE_MACHINE.md": frozenset({"workflows"}),
    "SEQUENCE_CONTRACTS.md": frozenset({"acceptance", "workflows"}),
    "MODULE_MAP.md": frozenset({"facts"}),
    "SYMBOL_INDEX.md": frozenset({"facts"}),
    "FLOW_INDEX.md": frozenset({"workflows", "facts"}),
    "TEST_ACCEPTANCE_MATRIX.md": frozenset({"acceptance", "facts"}),
    "DOC_SYNC_MATRIX.md": frozenset({"profile"}),
    "PROJECT_TRUTH_SYNC.md": frozenset({"acceptance", "claims"}),
    "API_CONTRACTS.md": frozenset({"contracts"}),
    "DATA_CONTRACTS.md": frozenset({"contracts"}),
    "UI_INFORMATION_ARCHITECTURE.md": frozenset({"contracts"}),
    "RUNBOOK.md": frozenset({"contracts"}),
    "DECISIONS.md": frozenset({"decisions"}),
    "KNOWN_DEFECTS.md": frozenset({"known_defects"}),
    "GLOSSARY.md": frozenset({"glossary"}),
    "CHANGELOG.md": frozenset({"changelog"}),
}

SPEC_TOKENS = {
    ".workflow/project.json": "project",
    ".workflow/authority.json": "authority",
    ".workflow/state.json": "state",
    ".workflow/roadmap.json": "roadmap",
    ".workflow/architecture.json": "architecture",
    ".workflow/contracts.json": "contracts",
    ".workflow/acceptance.json": "acceptance",
    ".workflow/decisions.json": "decisions",
    ".workflow/known_defects.json": "known_defects",
    ".workflow/glossary.json": "glossary",
    ".workflow/changelog.json": "changelog",
}

BROAD_TOOL_PATHS = {
    "scripts/generate_project_docs.py",
    "scripts/project_truth_impact.py",
    "scripts/sync_project_truth.py",
    "scripts/validate_project_docs.py",
    "scripts/extract_project_facts.py",
    "scripts/project_snapshot.py",
    "scripts/project_profile.py",
    "scripts/schema_contract.py",
    "scripts/project_truth_projection_common.py",
    "scripts/project_truth_projection_state.py",
    "scripts/project_truth_projection_code.py",
    "scripts/project_truth_projection_governance.py",
    "scripts/project_truth_projection_contracts.py",
}

NO_PROJECTION_PATHS = {
    "README.md",
    "SKILL.md",
    "AGENTS.md",
    "LICENSE",
    ".gitattributes",
    ".gitignore",
    ".workflow/toolchain.lock.json",
}
NO_PROJECTION_PREFIXES = (
    ".github/",
    "artifacts/sequence/",
    "benchmarks/",
    "docs/sequence/",
    "references/",
    "templates/",
)


def normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def plan_project_truth_impact(
    changed_paths: Iterable[str],
    available_docs: Iterable[str],
) -> dict[str, object]:
    """Return a deterministic conservative projection-impact plan."""
    paths = tuple(sorted({normalize_path(path) for path in changed_paths if normalize_path(path)}))
    docs = tuple(sorted(set(available_docs)))
    docs_set = set(docs)
    tokens: set[str] = set()
    direct_docs: set[str] = set()
    unknown_paths: list[str] = []
    reasons: dict[str, str] = {}
    broad_docs = False
    facts_affected = False

    for path in paths:
        if path == "PROJECT_PROFILE.yaml":
            broad_docs = True
            reasons[path] = "PROFILE_CAN_CHANGE_DOCUMENT_SET"
            continue
        if path == ".workflow/claims.json":
            broad_docs = True
            tokens.add("claims")
            reasons[path] = "CLAIM_BACKLINKS_CAN_TARGET_ANY_DOCUMENT"
            continue
        if path in BROAD_TOOL_PATHS or path.startswith(".workflow/tools/"):
            broad_docs = True
            facts_affected = True
            reasons[path] = "COMPILER_OR_FACT_TOOLING_CHANGED"
            continue
        token = SPEC_TOKENS.get(path)
        if token:
            tokens.add(token)
            reasons[path] = "SPEC:" + token
            continue
        if path.startswith(".workflow/workflows/") and path.endswith(".json"):
            tokens.add("workflows")
            reasons[path] = "WORKFLOW_SPEC"
            continue
        if path == ".workflow/generated/code_facts.json":
            tokens.add("facts")
            facts_affected = True
            reasons[path] = "FACT_ARTIFACT_CHANGED"
            continue
        if path.startswith("docs/"):
            name = Path(path).name
            if name in docs_set and path.count("/") == 1:
                direct_docs.add(name)
                reasons[path] = "GENERATED_PROJECTION_CHANGED"
                continue
        if path in NO_PROJECTION_PATHS or path.startswith(NO_PROJECTION_PREFIXES):
            reasons[path] = "NO_PROJECT_TRUTH_PROJECTION"
            continue
        if Path(path).suffix.lower() in SOURCE_EXTENSIONS:
            tokens.add("facts")
            facts_affected = True
            reasons[path] = "SOURCE_FACTS_CHANGED"
            continue

        broad_docs = True
        facts_affected = True
        unknown_paths.append(path)
        reasons[path] = "UNKNOWN_FAIL_CLOSED"

    if broad_docs:
        affected = set(docs)
    else:
        affected = set(direct_docs)
        for name in docs:
            if DOC_DEPENDENCIES.get(name, frozenset()).intersection(tokens):
                affected.add(name)

    return {
        "schema_version": 1,
        "changed_paths": list(paths),
        "affected_docs": sorted(affected),
        "facts_affected": facts_affected,
        "broad": broad_docs,
        "unknown_paths": sorted(unknown_paths),
        "reasons": {key: reasons[key] for key in sorted(reasons)},
        "final_acceptance_authority": False,
    }

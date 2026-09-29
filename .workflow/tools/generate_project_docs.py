#!/usr/bin/env python3
"""Deterministic Project Truth Compiler.

Inputs:
- PROJECT_PROFILE.yaml
- .workflow/*.json semantic/governance specs
- .workflow/workflows/*.json
- machine-observed code facts from extract_project_facts.py

Outputs:
- deterministic human-facing Markdown under repository-root docs/

The compiler never invents missing business intent. Missing required semantic
inputs fail closed. Generated Markdown is a projection, not upstream authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from extract_project_facts import extract_project_facts
from project_profile import (
    contract_settings,
    documentation_settings,
    normalized_profile,
    parse_profile,
    required_docs,
    sequence_settings,
)

SPEC_FILES = [
    "project.json",
    "authority.json",
    "state.json",
    "architecture.json",
    "contracts.json",
    "claims.json",
    "acceptance.json",
    "decisions.json",
    "known_defects.json",
    "glossary.json",
    "changelog.json",
]


def git_root(start: Path) -> Path:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(value)
    except Exception:
        return start.resolve()


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Top-level JSON object required: " + str(path))
    return data


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def clean(value: Any) -> str:
    return str(value or "").strip()


def cell(value: Any) -> str:
    return clean(value).replace("|", "\\|").replace("\n", "<br>")


def bullets(values: list[Any], empty: str = "- None declared.") -> str:
    rows = [clean(x) for x in values if clean(x)]
    return "\n".join("- " + x for x in rows) if rows else empty


def normalize_markdown(text: str) -> str:
    """Apply safe deterministic Markdown normalization.

    This is formatting-only. It must not reorder semantic lists, lifecycle
    transitions, decision chronology, sequence edges, or evidence rows.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    in_fence = False
    previous_blank = False

    for raw in text.split("\n"):
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("~~~") or stripped.startswith(chr(96) * 3):
            in_fence = not in_fence
            out.append(line)
            previous_blank = False
            continue

        if not in_fence and not line:
            if previous_blank:
                continue
            out.append("")
            previous_blank = True
            continue

        out.append(line)
        previous_blank = False

    while out and not out[-1]:
        out.pop()

    return "\n".join(out) + "\n"


def read_specs(spec_root: Path) -> tuple[dict[str, dict], list[dict]]:
    specs: dict[str, dict] = {}
    missing: list[str] = []
    for name in SPEC_FILES:
        path = spec_root / name
        if not path.is_file():
            missing.append(name)
        else:
            specs[name] = load_json(path)
    if missing:
        raise ValueError("Missing specs: " + ", ".join(missing))

    workflows: list[dict] = []
    workflow_root = spec_root / "workflows"
    if workflow_root.is_dir():
        for path in sorted(workflow_root.glob("*.json")):
            item = load_json(path)
            if not clean(item.get("flow_id")):
                raise ValueError("Workflow missing flow_id: " + str(path))
            workflows.append(item)
    return specs, workflows


def validate_inputs(
    profile: str,
    specs: dict[str, dict],
    workflows: list[dict],
    contracts: dict[str, str],
    sequence: dict[str, bool],
) -> list[str]:
    failures: list[str] = []
    project = specs["project.json"].get("project", {})
    state = specs["state.json"]
    acceptance = specs["acceptance.json"]

    for key in ("name", "repository", "purpose"):
        value = clean(project.get(key))
        if not value or value == "replace-me":
            failures.append("PROJECT_SPEC_MISSING:" + key)

    if not project.get("primary_users"):
        failures.append("PROJECT_SPEC_MISSING:primary_users")

    for key in ("phase", "status"):
        value = clean(state.get(key))
        if not value or value == "replace-me":
            failures.append("STATE_SPEC_MISSING:" + key)

    if not specs["authority.json"].get("authorities"):
        failures.append("AUTHORITY_SPEC_EMPTY")

    boundary = clean(acceptance.get("evidence_boundary"))
    if not boundary or boundary == "replace-me":
        failures.append("ACCEPTANCE_SPEC_MISSING:evidence_boundary")

    if profile in {"standard", "strict"} and not workflows:
        failures.append("WORKFLOW_SPECS_EMPTY")

    if sequence.get("required", False):
        for flow in workflows:
            if flow.get("critical") and not clean(flow.get("sequence_session")):
                failures.append(
                    "CRITICAL_FLOW_SEQUENCE_SESSION_MISSING:"
                    + clean(flow.get("flow_id"))
                )

    contract_map = {
        "api_contracts": "api_contracts",
        "data_contracts": "data_contracts",
        "ui_information_architecture": "ui_surfaces",
        "runbook": "runbook_steps",
    }
    contract_spec = specs["contracts.json"]
    for profile_key, spec_key in contract_map.items():
        if contracts.get(profile_key) == "required" and not contract_spec.get(spec_key):
            failures.append("REQUIRED_CONTRACT_SPEC_EMPTY:" + profile_key)

    if contracts.get("decisions") == "required":
        if not specs["decisions.json"].get("decisions"):
            failures.append("REQUIRED_CONTRACT_SPEC_EMPTY:decisions")

    return failures


def input_digest(
    profile_text: str,
    specs: dict[str, dict],
    workflows: list[dict],
    source_digest: str,
) -> str:
    h = hashlib.sha256()
    h.update(profile_text.encode("utf-8"))
    for name in sorted(specs):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(canonical_bytes(specs[name]))
        h.update(b"\0")
    for flow in sorted(workflows, key=lambda x: clean(x.get("flow_id"))):
        h.update(clean(flow.get("flow_id")).encode("utf-8"))
        h.update(b"\0")
        h.update(canonical_bytes(flow))
        h.update(b"\0")
    h.update(source_digest.encode("utf-8"))
    return h.hexdigest()


def generated_header(digest: str, source_digest: str) -> str:
    # Keep the tracked header stable. Global input/source digests belong in the
    # compiler report; embedding them in every document would create needless
    # whole-pack churn after unrelated source edits.
    return "<!-- GENERATED BY PROJECT TRUTH COMPILER - DO NOT EDIT -->\n\n"


def auth_lookup(specs: dict[str, dict], concern: str) -> dict:
    for item in specs["authority.json"].get("authorities", []):
        if clean(item.get("concern")) == concern:
            return item
    return {}


def render_system_overview(
    specs: dict[str, dict], workflows: list[dict], facts: dict
) -> str:
    project = specs["project.json"]["project"]
    architecture = specs["architecture.json"]
    state = specs["state.json"]
    acceptance = specs["acceptance.json"]
    human_status = clean(
        acceptance.get("human_comprehension_status", "NOT_PROVEN")
    ).upper()

    component_rows = []
    for item in architecture.get("components", []):
        component_rows.append(
            "| "
            + cell(item.get("name") or item.get("id"))
            + " | "
            + cell(item.get("purpose"))
            + " | "
            + cell(", ".join(item.get("owns", [])))
            + " | "
            + cell(", ".join(item.get("depends_on", [])))
            + " |"
        )
    if not component_rows:
        component_rows.append("| None declared | | | |")

    authority_rows = []
    mutable: list[str] = []
    immutable: list[str] = []
    for item in specs["authority.json"].get("authorities", []):
        authority_rows.append(
            "| "
            + cell(item.get("concern"))
            + " | "
            + cell(item.get("authority"))
            + " | "
            + cell(item.get("meaning"))
            + " |"
        )
        summary = clean(item.get("concern")) + ": " + clean(item.get("meaning"))
        if item.get("mutable") is True:
            mutable.append(summary)
        elif item.get("mutable") is False:
            immutable.append(summary)
    if not authority_rows:
        authority_rows.append("| None declared | | |")

    data_flow_rows = []
    for item in architecture.get("data_flows", []):
        data_flow_rows.append(
            "- "
            + clean(item.get("from"))
            + " -> "
            + clean(item.get("to"))
            + ": "
            + clean(item.get("meaning"))
        )

    flow_text: list[str] = []
    failure_text: list[str] = []
    for flow in workflows:
        flow_text.extend(
            [
                "### "
                + clean(flow.get("flow_id"))
                + " — "
                + clean(flow.get("title")),
                "",
                clean(flow.get("purpose")) or "No purpose declared.",
                "",
                "Authority: " + (clean(flow.get("authority")) or "NOT_DECLARED"),
                "",
            ]
        )
        for transition in flow.get("transitions", []):
            flow_text.append(
                "- "
                + clean(transition.get("from"))
                + " -> "
                + clean(transition.get("to"))
                + (
                    " : " + clean(transition.get("action"))
                    if clean(transition.get("action"))
                    else ""
                )
            )
        flow_text.append("")
        for item in flow.get("failure_behavior", []):
            failure_text.append(clean(flow.get("flow_id")) + ": " + clean(item))

    gate_questions = [
        ("What is the project and what problem does it solve?", "One-minute summary"),
        ("Who uses it and what are the primary outcomes?", "One-minute summary"),
        ("What are the major components and how do they relate?", "Major components"),
        ("How does important data flow through the system?", "Main data flow"),
        ("What are the main user/domain workflows?", "Main user workflows"),
        ("What are the important lifecycle states and transitions?", "Lifecycle and state"),
        ("Who/what is authoritative for important decisions?", "Authority model"),
        ("What is mutable and what is immutable?", "Mutable vs immutable"),
        ("How does failure/recovery behave?", "Failure and recovery"),
        ("What is the current project state?", "Current project state"),
        ("What is proven and what is not proven?", "Proven vs not proven"),
        ("What may happen next and what is blocked?", "Current project state"),
    ]
    declared = acceptance.get("human_comprehension_questions", {})
    gate_rows = []
    for question, location in gate_questions:
        gate_rows.append(
            "| "
            + question
            + " | "
            + cell(declared.get(question, "NOT_PROVEN"))
            + " | "
            + location
            + " |"
        )

    return """# SYSTEM OVERVIEW

Status: CURRENT
Human comprehension status: {human}

## One-minute summary

Project: {name}

Purpose: {purpose}

Primary users: {users}

Expected outcomes:
{outcomes}

## System at a glance

Users / External Systems
    -> Project Control Surface
    -> Declared Workflows
    -> State / Evidence Authorities
    -> External Runtime / Outputs

Observed source inventory: {files} files, {languages} language categories.

## Major components

| Component | Purpose | Owns / Decides | Depends On |
|---|---|---|---|
{components}

## Main data flow

{dataflows}

## Main user workflows

{flows}

## Lifecycle and state

Current phase: {phase}

Current status: {status}

See WORKFLOW_STATE_MACHINE.md for generated lifecycle contracts.

## Authority model

| Concern | Authority | Meaning |
|---|---|---|
{authorities}

## Mutable vs immutable

### Mutable current state

{mutable}

### Immutable history / evidence

{immutable}

### Configuration vs execution snapshot

Configuration/snapshot semantics come from authority and workflow specs. The
compiler does not infer them from implementation names.

## Failure and recovery

{failures}

## Current project state

Next authorized actions:
{next_actions}

Blocked actions:
{blocked_actions}

Known blockers:
{blockers}

## Proven vs not proven

### Proven

{proven}

### Not proven

{not_proven}

## Important limitations

{limitations}

## Glossary

See GLOSSARY.md.

## Where to read deeper

| Need | Document |
|---|---|
| Current state | CURRENT_STATE.md |
| Project identity | PROJECT_MANIFEST.md |
| Architecture | ARCHITECTURE.md |
| Lifecycle | WORKFLOW_STATE_MACHINE.md |
| Sequence evidence | SEQUENCE_CONTRACTS.md |
| Engineering flows | FLOW_INDEX.md |
| Modules | MODULE_MAP.md |
| Symbols | SYMBOL_INDEX.md |
| APIs | API_CONTRACTS.md |
| Data | DATA_CONTRACTS.md |
| Acceptance | TEST_ACCEPTANCE_MATRIX.md |
| Truth traceability | PROJECT_TRUTH_SYNC.md |

## Human comprehension gate

| Question | Status | Answer location |
|---|---|---|
{gate_rows}

The compiler projects the declared human-comprehension status. It does not
grant PASS automatically.
""".format(
        human=human_status,
        name=clean(project.get("name")),
        purpose=clean(project.get("purpose")),
        users=", ".join(project.get("primary_users", [])),
        outcomes=bullets(project.get("expected_outcomes", [])),
        files=facts["source_summary"]["files"],
        languages=len(facts["source_summary"]["languages"]),
        components="\n".join(component_rows),
        dataflows="\n".join(data_flow_rows) or "- None declared.",
        flows="\n".join(flow_text) or "No workflows declared.",
        phase=clean(state.get("phase")),
        status=clean(state.get("status")),
        authorities="\n".join(authority_rows),
        mutable=bullets(mutable),
        immutable=bullets(immutable),
        failures=bullets(failure_text),
        next_actions=bullets(state.get("next_authorized_actions", [])),
        blocked_actions=bullets(state.get("blocked_actions", [])),
        blockers=bullets(state.get("blockers", [])),
        proven=bullets(state.get("proven", [])),
        not_proven=bullets(state.get("not_proven", [])),
        limitations=bullets(facts.get("coverage", {}).get("limitations", [])),
        gate_rows="\n".join(gate_rows),
    )


def render_project_manifest(profile: str, specs: dict[str, dict], facts: dict) -> str:
    project_spec = specs["project.json"]
    project = project_spec["project"]
    tech = project_spec.get("technology", {})
    state = specs["state.json"]
    source = auth_lookup(specs, "source")
    runtime = auth_lookup(specs, "runtime")
    acceptance = auth_lookup(specs, "acceptance")

    entry_rows = []
    for item in project_spec.get("entry_points", []):
        entry_rows.append(
            "| "
            + cell(item.get("name") or item.get("kind"))
            + " | "
            + cell(item.get("path"))
            + " | "
            + cell(item.get("purpose"))
            + " |"
        )
    if not entry_rows:
        entry_rows.append("| None declared | | |")

    return """# PROJECT MANIFEST

## Project
Name: {name}
Purpose: {purpose}
Primary users: {users}
Governance profile: {profile}

## Repositories
Repository: {repo}
Active branch: {branch}
Current authoritative SHA: external final acceptance evidence
Last accepted SHA: {accepted}
Current source digest: {digest}

## Authorities
Source authority: {source}
Runtime authority: {runtime}
Acceptance authority: {acceptance}
Data authority: see SOURCE_AUTHORITY_MAP.md
UI authority: see SOURCE_AUTHORITY_MAP.md
Historical/reference authority: see SOURCE_AUTHORITY_MAP.md

## Technology
Languages: {languages}
Frameworks: {frameworks}
Persistence: {persistence}
External systems: {external}

## Entry points

| Entry | Path | Purpose |
|---|---|---|
{entries}

## Critical directories

Generated from code inventory. See MODULE_MAP.md.

## Required reading order
1. ../PROJECT_PROFILE.yaml
2. SYSTEM_OVERVIEW.md
3. CURRENT_STATE.md
4. PROJECT_MANIFEST.md
5. profile-required authority / architecture / workflow docs
6. SEQUENCE_CONTRACTS.md when enabled
7. MODULE_MAP.md
8. FLOW_INDEX.md
9. SYMBOL_INDEX.md
10. TEST_ACCEPTANCE_MATRIX.md
11. DOC_SYNC_MATRIX.md
12. PROJECT_TRUTH_SYNC.md when applicable

## Profile-specific applicability

Generated from PROJECT_PROFILE.yaml.

## Non-negotiable constraints

{constraints}
""".format(
        name=clean(project.get("name")),
        purpose=clean(project.get("purpose")),
        users=", ".join(project.get("primary_users", [])),
        profile=profile,
        repo=clean(project.get("repository")),
        branch=clean(state.get("working_branch")) or "NOT_DECLARED",
        accepted=clean(state.get("last_accepted_sha")) or "NOT_DECLARED",
        digest=facts["source_digest"],
        source=clean(source.get("meaning")) or clean(source.get("authority")),
        runtime=clean(runtime.get("meaning")) or clean(runtime.get("authority")),
        acceptance=clean(acceptance.get("meaning")) or clean(acceptance.get("authority")),
        languages=", ".join(tech.get("languages", [])),
        frameworks=", ".join(tech.get("frameworks", [])),
        persistence=", ".join(tech.get("persistence", [])),
        external=", ".join(tech.get("external_systems", [])),
        entries="\n".join(entry_rows),
        constraints=bullets(project_spec.get("constraints", [])),
    )


def render_current_state(
    profile: str,
    specs: dict[str, dict],
    facts: dict,
    sequence_required: bool,
) -> str:
    state = specs["state.json"]
    acceptance = specs["acceptance.json"]
    project = specs["project.json"]["project"]
    return """# CURRENT STATE

Last updated: generated from current specs
Authority verified at SHA: {accepted}
Governance profile: {profile}

## Current phase
Phase: {phase}
Status: {status}

## Source
Repository: {repository}
Branch: {branch}
Authoritative SHA: external final acceptance evidence
Last accepted SHA: {accepted}
Current candidate SHA: external final acceptance evidence
Current source digest: {digest}

## Runtime
Environment: see SOURCE_AUTHORITY_MAP.md and RUNBOOK.md
Runtime status: {runtime}

## Documentation governance
Documentation root: docs/
Documentation mode: GENERATED
DOC_LAYOUT: {doc_layout}
PROJECT_DOCS_NORMALIZED: {docs_normalized}
DOC_READABILITY: {doc_readability}
PROJECT_DOCS_SYNC: {project_docs_sync}

## Sequence governance
Sequence policy: {sequence_policy}
Current sequence mode: {sequence_mode}
Current sequence session: {sequence_session}
SEQUENCE_SYNC: {sequence_sync}

## Proven
{proven}

## Not proven
{not_proven}

## Known blockers
{blockers}

## Known defects
See KNOWN_DEFECTS.md.

## Next authorized action
{next_actions}

## Explicitly blocked
{blocked}
""".format(
        accepted=clean(state.get("last_accepted_sha")) or "NOT_DECLARED",
        profile=profile,
        repository=clean(project.get("repository")),
        phase=clean(state.get("phase")),
        status=clean(state.get("status")),
        branch=clean(state.get("working_branch")) or "NOT_DECLARED",
        digest=facts["source_digest"],
        runtime=clean(acceptance.get("runtime_status", "NOT_PROVEN")),
        sequence_policy="REQUIRED" if sequence_required else "OPTIONAL / NOT_APPLICABLE",
        sequence_mode=clean(acceptance.get("sequence_mode", "NOT_APPLICABLE")),
        sequence_session=clean(acceptance.get("sequence_session")) or "NOT_APPLICABLE",
        sequence_sync=clean(acceptance.get("sequence_sync_status", "NOT_PROVEN")),
        doc_layout=clean(
            acceptance.get("truth_gates", {}).get("DOC_LAYOUT", "NOT_PROVEN")
        ),
        docs_normalized=clean(
            acceptance.get("truth_gates", {}).get(
                "PROJECT_DOCS_NORMALIZED", "NOT_PROVEN"
            )
        ),
        doc_readability=clean(
            acceptance.get("truth_gates", {}).get("DOC_READABILITY", "NOT_PROVEN")
        ),
        project_docs_sync=clean(
            acceptance.get("truth_gates", {}).get("PROJECT_DOCS_SYNC", "NOT_PROVEN")
        ),
        proven=bullets(state.get("proven", [])),
        not_proven=bullets(state.get("not_proven", [])),
        blockers=bullets(state.get("blockers", [])),
        next_actions=bullets(state.get("next_authorized_actions", [])),
        blocked=bullets(state.get("blocked_actions", [])),
    )


def render_authority(specs: dict[str, dict]) -> str:
    rows = []
    for item in specs["authority.json"].get("authorities", []):
        rows.append(
            "| "
            + cell(item.get("concern"))
            + " | "
            + cell(item.get("authority"))
            + " | "
            + cell(item.get("meaning"))
            + " | "
            + ("YES" if item.get("mutable") is True else "NO")
            + " |"
        )
    if not rows:
        rows.append("| None declared | | | |")
    return """# SOURCE AUTHORITY MAP

Canonical authority is declared in .workflow/authority.json.

| Concern | Authority | Meaning | Mutable |
|---|---|---|---|
{rows}

## Invariants

{invariants}

## Conflict rule

If authorities conflict, fail closed. Repair the semantic spec/source contract;
do not hand-edit this generated projection.
""".format(
        rows="\n".join(rows),
        invariants=bullets(specs["authority.json"].get("invariants", [])),
    )


def render_architecture(specs: dict[str, dict], facts: dict) -> str:
    architecture = specs["architecture.json"]
    rows = []
    for item in architecture.get("components", []):
        rows.append(
            "| "
            + cell(item.get("id"))
            + " | "
            + cell(item.get("name"))
            + " | "
            + cell(item.get("purpose"))
            + " | "
            + cell(", ".join(item.get("owns", [])))
            + " | "
            + cell(", ".join(item.get("depends_on", [])))
            + " |"
        )
    if not rows:
        rows.append("| | | | | |")

    flow_rows = []
    for item in architecture.get("data_flows", []):
        flow_rows.append(
            "- "
            + clean(item.get("from"))
            + " -> "
            + clean(item.get("to"))
            + ": "
            + clean(item.get("meaning"))
        )

    boundary_rows = []
    for item in architecture.get("external_boundaries", []):
        boundary_rows.append(
            "- "
            + clean(item.get("name") or item.get("system"))
            + ": "
            + clean(item.get("contract") or item.get("meaning"))
        )

    languages = ", ".join(
        key + "=" + str(value)
        for key, value in facts["source_summary"]["languages"].items()
    )

    return """# ARCHITECTURE

Current source digest: {digest}

## Components

| ID | Component | Purpose | Owns | Depends On |
|---|---|---|---|---|
{rows}

## Data flow

{flows}

## External boundaries

{boundaries}

## Observed implementation inventory

Source files: {files}
Source lines: {lines}
Languages: {languages}

Structural facts come from the code extractor. Component meaning comes from
.workflow/architecture.json.
""".format(
        digest=facts["source_digest"],
        rows="\n".join(rows),
        flows="\n".join(flow_rows) or "- None declared.",
        boundaries="\n".join(boundary_rows) or "- None declared.",
        files=facts["source_summary"]["files"],
        lines=facts["source_summary"]["lines"],
        languages=languages,
    )


def render_workflows(workflows: list[dict]) -> str:
    out = ["# WORKFLOW STATE MACHINE", ""]
    if not workflows:
        return "# WORKFLOW STATE MACHINE\n\nNo workflow contracts declared.\n"
    for flow in workflows:
        out.extend(
            [
                "## "
                + clean(flow.get("flow_id"))
                + " — "
                + clean(flow.get("title")),
                "",
                "Purpose: " + clean(flow.get("purpose")),
                "Critical: " + str(bool(flow.get("critical"))).upper(),
                "Entry condition: " + clean(flow.get("entry_condition")),
                "Authority: " + clean(flow.get("authority")),
                "",
                "### States",
                "",
                bullets(flow.get("states", [])),
                "",
                "### Legal transitions",
                "",
                "| From | To | Action | Authority | Side effects |",
                "|---|---|---|---|---|",
            ]
        )
        transitions = flow.get("transitions", [])
        if transitions:
            for item in transitions:
                out.append(
                    "| "
                    + cell(item.get("from"))
                    + " | "
                    + cell(item.get("to"))
                    + " | "
                    + cell(item.get("action"))
                    + " | "
                    + cell(item.get("authority") or flow.get("authority"))
                    + " | "
                    + cell(", ".join(item.get("side_effects", [])))
                    + " |"
                )
        else:
            out.append("| | | | | |")
        out.extend(
            [
                "",
                "### Invariants",
                "",
                bullets(flow.get("invariants", [])),
                "",
                "### Failure behavior",
                "",
                bullets(flow.get("failure_behavior", [])),
                "",
                "### Restart behavior",
                "",
                bullets(flow.get("restart_behavior", [])),
                "",
                "### Rollback behavior",
                "",
                bullets(flow.get("rollback_behavior", [])),
                "",
            ]
        )
    return "\n".join(out) + "\n"


def render_sequence(specs: dict[str, dict], workflows: list[dict]) -> str:
    acceptance = specs["acceptance.json"]
    rows = []
    for flow in workflows:
        rows.append(
            "| "
            + cell(flow.get("flow_id"))
            + " | "
            + cell(acceptance.get("sequence_mode", "NOT_APPLICABLE"))
            + " | "
            + ("YES" if flow.get("critical") else "NO")
            + " | "
            + cell(flow.get("sequence_session"))
            + " | "
            + cell(acceptance.get("sequence_sync_status", "NOT_PROVEN"))
            + " |"
        )
    if not rows:
        rows.append("| None declared | | | | |")

    return """# SEQUENCE CONTRACTS

Status: CURRENT

## Modes

| Mode | Plan | Actual | Acceptance |
|---|---|---|---|
| BEFORE | Frozen before implementation | Generated from code | PLAN versus ACTUAL |
| DURING | Not applicable | Generated from current code | ACTUAL versus SOURCE/TEST/RUNTIME |
| AFTER | Not applicable | Generated from final code | FINAL ACTUAL versus SOURCE/TEST/RUNTIME |

## Flow inventory

| Flow | Mode | Critical | Sequence session | Status |
|---|---|---|---|---|
{rows}

## Mismatch handling

Allowed classifications:
- CODE_DEFECT
- PLAN_CHANGE
- GENERATOR_DEFECT

Canonical Mermaid is generated and must not be hand-edited.

## Current vs historical sequence sessions

CURRENT evidence binds to current source content. HISTORICAL evidence remains
bound to its accepted historical source digest.
""".format(rows="\n".join(rows))


def render_modules(facts: dict) -> str:
    rows = []
    for item in facts.get("modules", []):
        rows.append(
            "| "
            + cell(item.get("file"))
            + " | "
            + cell(item.get("language"))
            + " | "
            + str(item.get("lines"))
            + " | "
            + cell(item.get("directory"))
            + " | "
            + ("YES" if item.get("is_test") else "NO")
            + " |"
        )
    if not rows:
        rows.append("| | | | | |")
    return """# MODULE MAP

Authority SHA: external final acceptance evidence
Source digest: {digest}
Generated/refreshed: current compiler run

| Module / File | Language | Lines | Directory | Test file |
|---|---|---:|---|---|
{rows}

Machine-derived facts do not invent semantic ownership.
""".format(digest=facts["source_digest"], rows="\n".join(rows))


def render_symbols(facts: dict) -> str:
    rows = []
    for item in facts.get("python_symbols", []):
        start = clean(item.get("line_start"))
        end = clean(item.get("line_end"))
        line_range = start + ("-" + end if end else "")
        rows.append(
            "| "
            + cell(item.get("file"))
            + " | "
            + cell(item.get("symbol"))
            + " | "
            + cell(item.get("kind"))
            + " | "
            + cell(line_range)
            + " | Observed Python symbol | | | |"
        )
    if not rows:
        rows.append("| | | | | | | | |")
    return """# SYMBOL INDEX

Authority SHA: external final acceptance evidence
Source digest: {digest}
Status: CURRENT

| File | Symbol | Kind | Lines@SHA | Responsibility | Reads/Writes | Called By | Tests |
|---|---|---|---|---|---|---|---|
{rows}

## Coverage

{coverage}
""".format(
        digest=facts["source_digest"],
        rows="\n".join(rows),
        coverage=bullets(facts.get("coverage", {}).get("limitations", [])),
    )


def render_flows(workflows: list[dict], facts: dict) -> str:
    rows = []
    for flow in workflows:
        targets = [
            clean(x.get("to"))
            for x in flow.get("transitions", [])
            if clean(x.get("to"))
        ]
        rows.append(
            "| "
            + cell(flow.get("flow_id"))
            + " | "
            + cell(flow.get("entry_condition"))
            + " | "
            + cell(", ".join(flow.get("source_owners", [])))
            + " | "
            + cell(", ".join(targets))
            + " | "
            + cell(", ".join(flow.get("tests", [])))
            + " | "
            + cell(flow.get("sequence_session"))
            + " | DECLARED |"
        )
    if not rows:
        rows.append("| | | | | | | |")

    route_rows = []
    for item in facts.get("python_routes", []):
        route_rows.append(
            "| "
            + cell(item.get("method"))
            + " | "
            + cell(item.get("route"))
            + " | "
            + cell(item.get("handler"))
            + " |"
        )
    if not route_rows:
        route_rows.append("| | | |")

    return """# FLOW INDEX

Authority SHA: external final acceptance evidence
Source digest: {digest}

## Flow inventory

| Flow | Entry | Authority symbol | State mutation | Tests | Sequence session | Sequence status |
|---|---|---|---|---|---|---|
{rows}

## Observed Python HTTP routes

| Method | Route | Handler |
|---|---|---|
{routes}

Declared flow semantics come from .workflow/workflows. Observed implementation
facts come from source extraction and sequence artifacts.
""".format(
        digest=facts["source_digest"],
        rows="\n".join(rows),
        routes="\n".join(route_rows),
    )


def render_acceptance(specs: dict[str, dict], facts: dict) -> str:
    acceptance = specs["acceptance.json"]
    rows = []
    for item in acceptance.get("requirements", []):
        rows.append(
            "| "
            + cell(item.get("id"))
            + " | "
            + cell(item.get("requirement"))
            + " | "
            + cell(item.get("evidence"))
            + " | "
            + cell(item.get("status", "NOT_PROVEN"))
            + " |"
        )
    if not rows:
        rows.append("| | | | NOT_PROVEN |")

    return """# TEST ACCEPTANCE MATRIX

## Evidence boundary

{boundary}

Final tested source: external final acceptance evidence.
Current source digest: {digest}

| Requirement | Contract | Evidence | Status |
|---|---|---|---|
{rows}

## Test commands

{tests}

## Runtime checks

{runtime}

## Sequence contract evidence

Sequence mode for this phase/session: {sequence_mode}
Sequence session contract: {sequence_session}
SEQUENCE_SYNC: {sequence_sync}

## Project Truth Compiler evidence

Documentation root: docs/
Documentation mode: GENERATED
DOC_LAYOUT: {doc_layout}
PROJECT_DOCS_NORMALIZED: {docs_normalized}
DOC_READABILITY: {doc_readability}
PROJECT_DOCS_SYNC: {project_docs_sync}

## Human comprehension evidence

SYSTEM_OVERVIEW status: {human}
HUMAN_COMPREHENSION_GATE: {human}

Generated documentation never upgrades NOT_RUN or NOT_PROVEN to PASS.
""".format(
        boundary=clean(acceptance.get("evidence_boundary")),
        digest=facts["source_digest"],
        rows="\n".join(rows),
        tests=bullets(acceptance.get("test_commands", [])),
        runtime=bullets(acceptance.get("runtime_checks", [])),
        sequence_mode=clean(acceptance.get("sequence_mode", "NOT_APPLICABLE")),
        sequence_session=clean(acceptance.get("sequence_session")) or "NOT_APPLICABLE",
        sequence_sync=clean(acceptance.get("sequence_sync_status", "NOT_PROVEN")),
        doc_layout=clean(
            acceptance.get("truth_gates", {}).get("DOC_LAYOUT", "NOT_PROVEN")
        ),
        docs_normalized=clean(
            acceptance.get("truth_gates", {}).get(
                "PROJECT_DOCS_NORMALIZED", "NOT_PROVEN"
            )
        ),
        doc_readability=clean(
            acceptance.get("truth_gates", {}).get("DOC_READABILITY", "NOT_PROVEN")
        ),
        project_docs_sync=clean(
            acceptance.get("truth_gates", {}).get("PROJECT_DOCS_SYNC", "NOT_PROVEN")
        ),
        human=clean(acceptance.get("human_comprehension_status", "NOT_PROVEN")),
    )


def render_doc_sync(profile: str, sequence_required: bool) -> str:
    return """# DOCUMENTATION SYNC MATRIX

Governance profile: {profile}
Documentation mode: GENERATED
Sequence contracts required: {sequence}

## Compiler rule

CODE FACTS + GOVERNANCE SPECS + EVIDENCE DECLARATIONS
-> DETERMINISTIC MARKDOWN PROJECTIONS

Generated Markdown lives under repository-root docs/ and is not manually edited.

## Change mapping

| Change type | Upstream authority to update |
|---|---|
| Project identity/purpose/users/outcomes | .workflow/project.json |
| Authority/mutability/invariants | .workflow/authority.json |
| Current phase/blockers/next action | .workflow/state.json |
| Architecture/component/data-flow | .workflow/architecture.json |
| Workflow/lifecycle semantics | .workflow/workflows/*.json |
| API/data/UI/runbook | .workflow/contracts.json |
| Critical claims | .workflow/claims.json |
| Acceptance/evidence status | .workflow/acceptance.json |
| Durable decision | .workflow/decisions.json |
| Known defect | .workflow/known_defects.json |
| Glossary | .workflow/glossary.json |
| Implementation structure | source code; extracted automatically |

## Acceptance

Run: python .workflow/tools/validate_project_docs.py

If generated output differs from tracked Markdown:
PROJECT_DOCS_SYNC = FAIL

Repair source/spec authority and regenerate. Never patch generated Markdown by
hand.
""".format(
        profile=profile,
        sequence=str(sequence_required).upper(),
    )


def render_truth(specs: dict[str, dict], sequence_required: bool) -> str:
    acceptance = specs["acceptance.json"]
    gates = dict(acceptance.get("truth_gates", {}))
    gates.setdefault(
        "HUMAN_COMPREHENSION",
        clean(acceptance.get("human_comprehension_status", "NOT_PROVEN")),
    )
    gates.setdefault(
        "SEQUENCE_SYNC",
        clean(
            acceptance.get(
                "sequence_sync_status",
                "NOT_PROVEN" if sequence_required else "NOT_APPLICABLE",
            )
        ),
    )
    names = [
        "SOURCE_TESTS",
        "RUNTIME_E2E",
        "PROVENANCE_SYNC",
        "REFERENCE_SYNC",
        "STRUCTURAL_SYNC",
        "SEMANTIC_SYNC",
        "BEHAVIORAL_SYNC",
        "CROSS_DOCUMENT_CONSISTENCY",
        "HUMAN_COMPREHENSION",
        "SEQUENCE_SYNC",
        "DOC_LAYOUT",
        "PROJECT_DOCS_NORMALIZED",
        "DOC_READABILITY",
        "PROJECT_DOCS_SYNC",
        "DOC_SOURCE_TRACEABILITY",
        "DOC_TEST_TRACEABILITY",
        "TEST_RUNTIME_TRACEABILITY",
        "PROJECT_STATE_SYNC",
    ]
    gate_rows = [
        "| " + name + " | " + cell(gates.get(name, "NOT_PROVEN")) + " | |"
        for name in names
    ]

    claim_rows = []
    for item in specs["claims.json"].get("claims", []):
        claim_rows.append(
            "| "
            + cell(item.get("id"))
            + " | "
            + cell(item.get("claim"))
            + " | "
            + cell("; ".join(item.get("documents", [])))
            + " | "
            + cell("; ".join(item.get("source_owners", [])))
            + " | "
            + cell("; ".join(item.get("tests", [])))
            + " | "
            + cell("; ".join(item.get("runtime_evidence", [])) or "NOT_APPLICABLE")
            + " | "
            + cell(item.get("status", "NOT_PROVEN"))
            + " |"
        )
    if not claim_rows:
        claim_rows.append("| | | | | | | NOT_PROVEN |")

    relation_rows = []
    for item in specs["claims.json"].get("relations", []):
        relation_rows.append(
            "| "
            + cell(item.get("claim_id"))
            + " | "
            + cell(item.get("relation"))
            + " | "
            + cell(item.get("other_claim_id"))
            + " | "
            + cell(item.get("notes"))
            + " |"
        )
    if not relation_rows:
        relation_rows.append("| | | | |")

    return """# PROJECT TRUTH SYNC

## Provenance policy

Generated docs bind to semantic-spec digest plus source-content digest. Final Git
HEAD is recorded externally after the commit exists.

## Truth gates

| Gate | Status | Evidence / Notes |
|---|---|---|
{gates}

## Critical claim traceability

| Claim ID | Claim | Documents | Source owner(s) | Test(s) | Runtime/E2E evidence | Status |
|---|---|---|---|---|---|---|
{claims}

## Claim relations

| Claim ID | Relation | Other Claim ID | Notes |
|---|---|---|---|
{relations}

## Cross-document consistency audit

All generated projections come from one compiler input snapshot.

## Broken / unresolved references

Validator evidence is external/generated.

## Contradictions

Contradictions in specs/source/evidence block PROJECT_STATE_SYNC.

## Final counters

STALE_DOCUMENTS: validator authority
BROKEN_REFERENCES: validator authority
UNRESOLVED_CONTRACTS: validator authority
CONTRADICTORY_CLAIMS: validator authority

## Validator evidence

HANDOFF_VALIDATOR:
HUMAN_COMPREHENSION_VALIDATOR:
SEQUENCE_CONTRACT_VALIDATOR:
CROSS_DOCUMENT_VALIDATOR:
PROJECT_TRUTH_VALIDATOR:
PROJECT_DOC_COMPILER:

## Human comprehension truth rule

HUMAN_COMPREHENSION is projected from .workflow/acceptance.json after semantic
review. The compiler does not infer PASS.

## Sequence synchronization truth rule

SEQUENCE_SYNC is projected from sequence acceptance evidence. The compiler does
not infer PASS from a diagram.
""".format(
        gates="\n".join(gate_rows),
        claims="\n".join(claim_rows),
        relations="\n".join(relation_rows),
    )


def render_api(specs: dict[str, dict]) -> str:
    rows = []
    for item in specs["contracts.json"].get("api_contracts", []):
        rows.append(
            "| "
            + cell(item.get("method"))
            + " | "
            + cell(item.get("path"))
            + " | "
            + cell(item.get("purpose"))
            + " | "
            + cell(item.get("authority"))
            + " | "
            + cell(item.get("mutation"))
            + " | "
            + cell(item.get("error_behavior"))
            + " |"
        )
    if not rows:
        rows.append("| | | | | | |")
    return """# API CONTRACTS

| Method | Path / Event | Purpose | Authority | Mutation | Error behavior |
|---|---|---|---|---|---|
{rows}

Declared in .workflow/contracts.json. Observed routes are listed in FLOW_INDEX.
""".format(rows="\n".join(rows))


def render_data(specs: dict[str, dict]) -> str:
    rows = []
    for item in specs["contracts.json"].get("data_contracts", []):
        rows.append(
            "| "
            + cell(item.get("name"))
            + " | "
            + cell(item.get("source_of_truth"))
            + " | "
            + cell(item.get("mutability"))
            + " | "
            + cell(", ".join(item.get("legal_writes", [])))
            + " | "
            + cell(item.get("retention"))
            + " | "
            + cell("; ".join(item.get("invariants", [])))
            + " |"
        )
    if not rows:
        rows.append("| | | | | | |")
    return """# DATA CONTRACTS

| Data / Artifact | Source of truth | Mutability | Legal writes | Retention | Invariants |
|---|---|---|---|---|---|
{rows}
""".format(rows="\n".join(rows))


def render_ui(specs: dict[str, dict]) -> str:
    rows = []
    for item in specs["contracts.json"].get("ui_surfaces", []):
        rows.append(
            "| "
            + cell(item.get("name"))
            + " | "
            + cell(item.get("purpose"))
            + " | "
            + cell(", ".join(item.get("actions", [])))
            + " | "
            + cell(item.get("authority"))
            + " | "
            + cell(item.get("flow_id"))
            + " |"
        )
    if not rows:
        rows.append("| | | | | |")
    return """# UI INFORMATION ARCHITECTURE

| Surface | Purpose | Actions | Authority semantics | Flow |
|---|---|---|---|---|
{rows}

The UI presents authority; it does not create authority.
""".format(rows="\n".join(rows))


def render_runbook(specs: dict[str, dict]) -> str:
    rows = []
    for index, item in enumerate(
        specs["contracts.json"].get("runbook_steps", []), start=1
    ):
        row = (
            str(index)
            + ". "
            + clean(item.get("name"))
            + " — "
            + clean(item.get("command"))
        )
        if clean(item.get("expected")):
            row += " — expected: " + clean(item.get("expected"))
        rows.append(row)
    return "# RUNBOOK\n\n" + ("\n".join(rows) or "No runbook steps declared.") + "\n"


def render_decisions(specs: dict[str, dict]) -> str:
    out = ["# DECISIONS", ""]
    items = specs["decisions.json"].get("decisions", [])
    if not items:
        out.append("No durable decisions declared.")
    for item in items:
        out.extend(
            [
                "## " + clean(item.get("id")) + " — " + clean(item.get("title")),
                "",
                "Status: " + clean(item.get("status", "ACCEPTED")),
                "",
                clean(item.get("decision")),
                "",
                "Rationale: " + clean(item.get("rationale")),
                "",
            ]
        )
    return "\n".join(out) + "\n"


def render_defects(specs: dict[str, dict]) -> str:
    rows = []
    for item in specs["known_defects.json"].get("defects", []):
        rows.append(
            "| "
            + cell(item.get("id"))
            + " | "
            + cell(item.get("status"))
            + " | "
            + cell(item.get("summary"))
            + " | "
            + cell(item.get("evidence"))
            + " |"
        )
    if not rows:
        rows.append("| | | | |")
    return """# KNOWN DEFECTS

| ID | Status | Summary | Evidence |
|---|---|---|---|
{rows}

Use explicit OPEN, FIXED/ACCEPTED, HISTORICAL, or NOT_PROVEN semantics.
""".format(rows="\n".join(rows))


def render_changelog(specs: dict[str, dict]) -> str:
    out = ["# CHANGELOG", ""]
    entries = specs["changelog.json"].get("entries", [])
    if not entries:
        out.append("No changelog entries declared.")
    for item in entries:
        out.extend(
            [
                "## " + clean(item.get("date")) + " — " + clean(item.get("title")),
                "",
                "Type: " + clean(item.get("type", "change")),
                "",
                bullets(item.get("changes", [])),
                "",
            ]
        )
    return "\n".join(out) + "\n"


def render_glossary(specs: dict[str, dict]) -> str:
    rows = []
    for item in specs["glossary.json"].get("terms", []):
        rows.append(
            "| " + cell(item.get("term")) + " | " + cell(item.get("definition")) + " |"
        )
    if not rows:
        rows.append("| | |")
    return "# GLOSSARY\n\n| Term | Definition |\n|---|---|\n" + "\n".join(rows) + "\n"



def render_roadmap(specs: dict[str, dict]) -> str:
    roadmap = specs["project.json"].get("roadmap", {})
    rows = []
    for item in roadmap.get("phases", []):
        rows.append(
            "| "
            + cell(item.get("phase"))
            + " | "
            + cell(item.get("name"))
            + " | "
            + cell(item.get("status"))
            + " |"
        )
    if not rows:
        rows.append("| | | |")

    expansion = []
    for item in roadmap.get("optional_expansion", []):
        expansion.append(
            "- Phase "
            + clean(item.get("phase"))
            + " — "
            + clean(item.get("name"))
        )

    return """# ROADMAP

Target: {target}

Planned core phases: {count}

## Production V1 roadmap

| Phase | Scope | Status |
|---:|---|---|
{rows}

## Optional post-V1 expansion

{expansion}

## Roadmap rules

{rules}

This document is generated from `.workflow/project.json`; it is planning
authority only. Acceptance truth remains in `.workflow/state.json` and
`.workflow/acceptance.json`.
""".format(
        target=clean(roadmap.get("target", "NOT_PROVEN")),
        count=clean(roadmap.get("phase_count", "NOT_PROVEN")),
        rows="\n".join(rows),
        expansion=bullets(expansion) if expansion else "- None declared.",
        rules=bullets(roadmap.get("rules", [])),
    )

def render_all(
    profile: str,
    specs: dict[str, dict],
    workflows: list[dict],
    facts: dict,
    required: set[str],
    contracts: dict[str, str],
    sequence: dict[str, bool],
    digest: str,
) -> dict[str, str]:
    renderers = {
        "SYSTEM_OVERVIEW.md": lambda: render_system_overview(specs, workflows, facts),
        "PROJECT_MANIFEST.md": lambda: render_project_manifest(profile, specs, facts),
        "ROADMAP.md": lambda: render_roadmap(specs),
        "CURRENT_STATE.md": lambda: render_current_state(
            profile, specs, facts, sequence.get("required", False)
        ),
        "SOURCE_AUTHORITY_MAP.md": lambda: render_authority(specs),
        "ARCHITECTURE.md": lambda: render_architecture(specs, facts),
        "WORKFLOW_STATE_MACHINE.md": lambda: render_workflows(workflows),
        "SEQUENCE_CONTRACTS.md": lambda: render_sequence(specs, workflows),
        "MODULE_MAP.md": lambda: render_modules(facts),
        "SYMBOL_INDEX.md": lambda: render_symbols(facts),
        "FLOW_INDEX.md": lambda: render_flows(workflows, facts),
        "TEST_ACCEPTANCE_MATRIX.md": lambda: render_acceptance(specs, facts),
        "DOC_SYNC_MATRIX.md": lambda: render_doc_sync(
            profile, sequence.get("required", False)
        ),
        "PROJECT_TRUTH_SYNC.md": lambda: render_truth(
            specs, sequence.get("required", False)
        ),
        "API_CONTRACTS.md": lambda: render_api(specs),
        "DATA_CONTRACTS.md": lambda: render_data(specs),
        "UI_INFORMATION_ARCHITECTURE.md": lambda: render_ui(specs),
        "RUNBOOK.md": lambda: render_runbook(specs),
        "DECISIONS.md": lambda: render_decisions(specs),
        "KNOWN_DEFECTS.md": lambda: render_defects(specs),
        "GLOSSARY.md": lambda: render_glossary(specs),
        "CHANGELOG.md": lambda: render_changelog(specs),
    }

    wanted = set(required)
    wanted.update(
        {
            "SYSTEM_OVERVIEW.md",
            "PROJECT_MANIFEST.md",
            "ROADMAP.md",
            "CURRENT_STATE.md",
            "MODULE_MAP.md",
            "TEST_ACCEPTANCE_MATRIX.md",
            "DECISIONS.md",
            "KNOWN_DEFECTS.md",
            "GLOSSARY.md",
        }
    )
    if sequence.get("required", False):
        wanted.add("SEQUENCE_CONTRACTS.md")

    optional_map = {
        "api_contracts": "API_CONTRACTS.md",
        "data_contracts": "DATA_CONTRACTS.md",
        "ui_information_architecture": "UI_INFORMATION_ARCHITECTURE.md",
        "runbook": "RUNBOOK.md",
        "decisions": "DECISIONS.md",
        "known_defects": "KNOWN_DEFECTS.md",
        "glossary": "GLOSSARY.md",
        "changelog": "CHANGELOG.md",
    }
    for key, filename in optional_map.items():
        if contracts.get(key) != "not_applicable":
            wanted.add(filename)

    result: dict[str, str] = {}
    head = generated_header(digest, facts["source_digest"])
    for name in sorted(wanted):
        renderer = renderers.get(name)
        if renderer is None:
            continue
        result[name] = normalize_markdown(head + renderer())
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--spec-root", default="")
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--facts-output",
        default=".workflow/generated/code_facts.json",
    )
    parser.add_argument("--report", default="")
    args = parser.parse_args()

    root = git_root(Path(args.root).resolve())
    profile_path = root / "PROJECT_PROFILE.yaml"
    if not profile_path.is_file():
        print("FAIL MISSING_PROJECT_PROFILE")
        return 1

    try:
        profile_data = parse_profile(profile_path)
        profile = normalized_profile(profile_data)
        required = required_docs(profile_data)
        contracts = contract_settings(profile_data)
        sequence = sequence_settings(profile_data)
        documentation = documentation_settings(profile_data)
    except Exception as exc:
        print("FAIL PROJECT_PROFILE_INVALID:" + str(exc))
        return 1

    spec_root_value = args.spec_root or str(documentation.get("spec_root", ".workflow"))
    spec_root = Path(spec_root_value)
    if not spec_root.is_absolute():
        spec_root = root / spec_root

    docs_root = root / str(documentation.get("docs_root", "docs"))

    try:
        specs, workflows = read_specs(spec_root)
        failures = validate_inputs(
            profile, specs, workflows, contracts, sequence
        )
    except Exception as exc:
        print("FAIL SPEC_ERROR:" + str(exc))
        return 1

    if failures:
        for item in failures:
            print("FAIL " + item)
        print("RESULT=FAIL failures=" + str(len(failures)))
        return 1

    facts = extract_project_facts(root)
    facts_output = Path(args.facts_output)
    if not facts_output.is_absolute():
        facts_output = root / facts_output
    expected_facts_text = json.dumps(facts, indent=2, sort_keys=True) + "\n"
    expected_facts_bytes = expected_facts_text.encode("utf-8")
    facts_missing = False
    facts_stale = False
    if args.check:
        if not facts_output.is_file():
            facts_missing = True
        elif facts_output.read_bytes() != expected_facts_bytes:
            facts_stale = True
    else:
        facts_output.parent.mkdir(parents=True, exist_ok=True)
        facts_output.write_bytes(expected_facts_bytes)

    digest = input_digest(
        profile_path.read_text(encoding="utf-8"),
        specs,
        workflows,
        facts["source_digest"],
    )
    docs = render_all(
        profile,
        specs,
        workflows,
        facts,
        required,
        contracts,
        sequence,
        digest,
    )

    missing: list[str] = []
    stale: list[str] = []
    root_duplicates: list[str] = []

    if not args.check:
        docs_root.mkdir(parents=True, exist_ok=True)

    for name, expected in docs.items():
        path = docs_root / name
        legacy_root_path = root / name
        if legacy_root_path.is_file():
            root_duplicates.append(name)

        if args.check:
            if not path.is_file():
                missing.append("docs/" + name)
            elif path.read_bytes() != expected.encode("utf-8"):
                stale.append("docs/" + name)
        else:
            path.write_bytes(expected.encode("utf-8"))

    report = {
        "schema_version": 1,
        "profile": profile,
        "docs_root": str(docs_root.relative_to(root).as_posix()),
        "source_digest": facts["source_digest"],
        "input_digest": digest,
        "generated_docs": sorted("docs/" + name for name in docs),
        "missing_docs": missing,
        "stale_docs": stale,
        "legacy_root_doc_duplicates": sorted(root_duplicates),
        "facts_missing": facts_missing,
        "facts_stale": facts_stale,
        "project_docs_normalized": True,
        "doc_layout": "FAIL" if root_duplicates else "PASS",
        "mode": "check" if args.check else "write",
        "result": "FAIL" if missing or stale or root_duplicates or facts_missing or facts_stale else "PASS",
        "semantic_boundary": (
            "Compiler projects declared semantic/governance specs and "
            "machine-observed code facts; it does not infer missing intent."
        ),
    }

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["result"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())

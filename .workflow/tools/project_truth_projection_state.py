"""Deterministic Project Truth projection renderers.

Extracted verbatim from generate_project_docs.py; no format semantics live here.
"""

from __future__ import annotations

from project_truth_projection_common import (
    auth_lookup,
    bullets,
    cell,
    clean,
)

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
| Roadmap | ROADMAP.md |
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
4. ROADMAP.md
5. PROJECT_MANIFEST.md
6. profile-required authority / architecture / workflow docs
7. SEQUENCE_CONTRACTS.md when enabled
8. MODULE_MAP.md
9. FLOW_INDEX.md
10. SYMBOL_INDEX.md
11. TEST_ACCEPTANCE_MATRIX.md
12. DOC_SYNC_MATRIX.md
13. PROJECT_TRUTH_SYNC.md when applicable

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
    roadmap = specs["roadmap.json"]
    acceptance = specs["acceptance.json"]
    project = specs["project.json"]["project"]
    return """# CURRENT STATE

Last updated: generated from current specs
Authority verified at SHA: {accepted}
Governance profile: {profile}

## Current phase
Phase: {phase}
Status: {status}
Roadmap phase: {roadmap_phase}
ROADMAP_SYNC: {roadmap_sync}

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
        roadmap_phase=clean(roadmap.get("current_phase")),
        roadmap_sync=clean(
            acceptance.get("truth_gates", {}).get("ROADMAP_SYNC", "NOT_PROVEN")
        ),
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

def render_roadmap(specs: dict[str, dict]) -> str:
    roadmap = specs["roadmap.json"]
    state = specs["state.json"]
    acceptance = specs["acceptance.json"]
    rows = []
    for index, item in enumerate(roadmap.get("phases", []), start=1):
        rows.append(
            "| "
            + str(index)
            + " | "
            + cell(item.get("id"))
            + " | "
            + cell(item.get("title"))
            + " | "
            + cell(item.get("status"))
            + " | "
            + cell(item.get("objective"))
            + " | "
            + cell("<br>".join(clean(x) for x in item.get("exit_criteria", []) if clean(x)))
            + " |"
        )
    if not rows:
        rows.append("| | | | | | |")

    return """# ROADMAP

Current project phase: {state_phase}
Current roadmap phase: {roadmap_phase}
ROADMAP_SYNC: {roadmap_sync}

## Phase plan

| Order | Phase | Title | Roadmap status | Objective | Exit criteria |
|---:|---|---|---|---|---|
{rows}

## Synchronization contract

`.workflow/roadmap.json` is the roadmap authority. This Markdown is generated.

The roadmap is valid only when:

- `.workflow/state.json::phase` equals `.workflow/roadmap.json::current_phase`;
- exactly one roadmap phase is marked `CURRENT`;
- that `CURRENT` phase id equals `current_phase`;
- every phase id is unique.

When the project advances phase, update `.workflow/state.json` and
`.workflow/roadmap.json` in the same project-state transaction, then run:

`python .workflow/tools/sync_project_truth.py`

Missing roadmap authority or phase drift is a blocking validation failure.
""".format(
        state_phase=clean(state.get("phase")),
        roadmap_phase=clean(roadmap.get("current_phase")),
        roadmap_sync=clean(
            acceptance.get("truth_gates", {}).get("ROADMAP_SYNC", "NOT_PROVEN")
        ),
        rows="\n".join(rows),
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

"""Deterministic Project Truth projection renderers.

Extracted verbatim from generate_project_docs.py; no format semantics live here.
"""

from __future__ import annotations

from project_truth_projection_common import (
    bullets,
    cell,
    clean,
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
| Current phase/status/blockers/next action | .workflow/state.json; when phase changes update .workflow/roadmap.json in the same transaction |
| Roadmap phase plan/current phase | .workflow/roadmap.json |
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
        "ROADMAP_SYNC",
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

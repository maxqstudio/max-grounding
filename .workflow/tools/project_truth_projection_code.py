"""Deterministic Project Truth projection renderers.

Extracted verbatim from generate_project_docs.py; no format semantics live here.
"""

from __future__ import annotations

from project_truth_projection_common import (
    bullets,
    cell,
    clean,
)

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
    symbols = list(facts.get("python_symbols", []))
    grouped: dict[str, list[dict]] = {}
    for item in symbols:
        file_name = clean(item.get("file")) or "UNKNOWN"
        grouped.setdefault(file_name, []).append(item)

    summary_rows: list[str] = []
    detail_sections: list[str] = []
    for file_name in sorted(grouped):
        items = sorted(
            grouped[file_name],
            key=lambda item: (
                int(item.get("line_start") or 0),
                clean(item.get("symbol")),
                clean(item.get("kind")),
            ),
        )
        classes = sum(1 for item in items if clean(item.get("kind")) == "class")
        functions = sum(1 for item in items if clean(item.get("kind")) == "function")
        methods = sum(1 for item in items if clean(item.get("kind")) == "method")
        summary_rows.append(
            "| " + cell(file_name) + " | " + str(len(items)) + " | "
            + str(classes) + " | " + str(functions) + " | " + str(methods) + " |"
        )

        detail_rows: list[str] = []
        for item in items:
            start_line = clean(item.get("line_start"))
            end_line = clean(item.get("line_end"))
            line_range = start_line + ("-" + end_line if end_line else "")
            detail_rows.append(
                "| " + cell(item.get("symbol")) + " | " + cell(item.get("kind"))
                + " | " + cell(line_range) + " |"
            )

        label = (
            file_name.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        detail_sections.extend(
            [
                "<details>",
                "<summary><code>" + label + "</code> — " + str(len(items)) + " symbols</summary>",
                "",
                "| Symbol | Kind | Lines@SHA |",
                "|---|---|---|",
                *detail_rows,
                "",
                "</details>",
                "",
            ]
        )

    if not summary_rows:
        summary_rows.append("| None observed | 0 | 0 | 0 | 0 |")
    details = "\n".join(detail_sections).rstrip() or "_No Python symbols observed._"
    return """# SYMBOL INDEX

Authority SHA: external final acceptance evidence
Source digest: {digest}
Status: CURRENT

The default view summarizes machine-observed symbols by file. Expand a file only
when exact symbol navigation is needed. Full machine facts remain available in
`.workflow/generated/code_facts.json`; this projection does not invent semantic
responsibility, callers, or state ownership.

## File summary

| File | Symbols | Classes | Functions | Methods |
|---|---:|---:|---:|---:|
{summary}

## Detailed symbols

{details}

## Coverage

{coverage}
""".format(
        digest=facts["source_digest"],
        summary="\n".join(summary_rows),
        details=details,
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

## Roadmap synchronization evidence

Roadmap authority: .workflow/roadmap.json
ROADMAP_SYNC: {roadmap_sync}

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
        roadmap_sync=clean(
            acceptance.get("truth_gates", {}).get("ROADMAP_SYNC", "NOT_PROVEN")
        ),
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

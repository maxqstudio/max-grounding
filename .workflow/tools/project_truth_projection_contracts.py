"""Deterministic Project Truth projection renderers.

Extracted verbatim from generate_project_docs.py; no format semantics live here.
"""

from __future__ import annotations

from project_truth_projection_common import (
    cell,
    clean,
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

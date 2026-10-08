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

from schema_contract import require_json_schema_version
from extract_project_facts import extract_project_facts
from project_truth_impact import plan_project_truth_impact
from project_profile import (
    contract_settings,
    documentation_settings,
    normalized_profile,
    parse_profile,
    required_docs,
    sequence_settings,
)

from project_truth_projection_common import (
    auth_lookup,
    bullets,
    cell,
    claim_backlink_comment,
    clean,
    generated_header,
    normalize_markdown,
    wanted_doc_names,
)
from project_truth_projection_state import (
    render_architecture,
    render_authority,
    render_current_state,
    render_project_manifest,
    render_roadmap,
    render_sequence,
    render_system_overview,
    render_workflows,
)
from project_truth_projection_code import (
    render_acceptance,
    render_flows,
    render_modules,
    render_symbols,
)
from project_truth_projection_governance import (
    render_changelog,
    render_decisions,
    render_defects,
    render_doc_sync,
    render_glossary,
    render_truth,
)
from project_truth_projection_contracts import (
    render_api,
    render_data,
    render_runbook,
    render_ui,
)

SPEC_FILES = [
    "project.json",
    "authority.json",
    "state.json",
    "roadmap.json",
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


def canonical_generated_bytes(value: bytes) -> bytes:
    """Normalize materialized line endings for deterministic content comparison."""
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")










def read_specs(spec_root: Path) -> tuple[dict[str, dict], list[dict]]:
    specs: dict[str, dict] = {}
    missing: list[str] = []
    for name in SPEC_FILES:
        path = spec_root / name
        if not path.is_file():
            missing.append(name)
        else:
            item = load_json(path)
            require_json_schema_version(item, name)
            specs[name] = item
    if missing:
        raise ValueError("Missing specs: " + ", ".join(missing))

    workflows: list[dict] = []
    workflow_root = spec_root / "workflows"
    if workflow_root.is_dir():
        for path in sorted(workflow_root.glob("*.json")):
            item = load_json(path)
            require_json_schema_version(item, "workflows/" + path.name)
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

    roadmap = specs["roadmap.json"]
    roadmap_current = clean(roadmap.get("current_phase"))
    if not roadmap_current or roadmap_current == "replace-me":
        failures.append("ROADMAP_CURRENT_PHASE_MISSING")

    phases = roadmap.get("phases")
    if not isinstance(phases, list) or not phases:
        failures.append("ROADMAP_PHASES_EMPTY")
    else:
        seen_phase_ids: set[str] = set()
        current_markers: list[str] = []
        for index, item in enumerate(phases, start=1):
            if not isinstance(item, dict):
                failures.append(f"ROADMAP_PHASE_INVALID:{index}")
                continue
            phase_id = clean(item.get("id"))
            title = clean(item.get("title"))
            roadmap_status = clean(item.get("status")).upper()
            if not phase_id or phase_id == "replace-me":
                failures.append(f"ROADMAP_PHASE_ID_MISSING:{index}")
                continue
            if phase_id in seen_phase_ids:
                failures.append("ROADMAP_PHASE_ID_DUPLICATE:" + phase_id)
            seen_phase_ids.add(phase_id)
            if not title or title == "replace-me":
                failures.append("ROADMAP_PHASE_TITLE_MISSING:" + phase_id)
            if not roadmap_status or roadmap_status == "REPLACE-ME":
                failures.append("ROADMAP_PHASE_STATUS_MISSING:" + phase_id)
            if roadmap_status == "CURRENT":
                current_markers.append(phase_id)

        if roadmap_current and roadmap_current not in seen_phase_ids:
            failures.append("ROADMAP_CURRENT_PHASE_NOT_FOUND:" + roadmap_current)
        if len(current_markers) != 1:
            failures.append(
                "ROADMAP_CURRENT_MARKER_COUNT:" + str(len(current_markers))
            )
        elif roadmap_current and current_markers[0] != roadmap_current:
            failures.append(
                "ROADMAP_CURRENT_MARKER_MISMATCH:"
                + current_markers[0]
                + "!="
                + roadmap_current
            )

    state_phase = clean(state.get("phase"))
    if roadmap_current and state_phase and roadmap_current != state_phase:
        failures.append(
            "ROADMAP_STATE_PHASE_MISMATCH:"
            + roadmap_current
            + "!="
            + state_phase
        )

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






















































def render_all(
    profile: str,
    specs: dict[str, dict],
    workflows: list[dict],
    facts: dict,
    required: set[str],
    contracts: dict[str, str],
    sequence: dict[str, bool],
    digest: str,
    only: set[str] | None = None,
) -> dict[str, str]:
    renderers = {
        "SYSTEM_OVERVIEW.md": lambda: render_system_overview(specs, workflows, facts),
        "PROJECT_MANIFEST.md": lambda: render_project_manifest(profile, specs, facts),
        "CURRENT_STATE.md": lambda: render_current_state(
            profile, specs, facts, sequence.get("required", False)
        ),
        "ROADMAP.md": lambda: render_roadmap(specs),
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

    wanted = wanted_doc_names(required, contracts, sequence)
    if only is not None:
        wanted.intersection_update(only)

    result: dict[str, str] = {}
    head = generated_header(digest, facts["source_digest"])
    for name in sorted(wanted):
        renderer = renderers.get(name)
        if renderer is None:
            continue
        result[name] = normalize_markdown(
            head + renderer() + claim_backlink_comment(specs, name)
        )
    return result



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--spec-root", default="")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--incremental", action="store_true")
    parser.add_argument("--changed-path", action="append", default=[])
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
    if args.incremental and not args.changed_path:
        print("FAIL INCREMENTAL_CHANGED_PATH_REQUIRED")
        return 2

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
        failures = validate_inputs(profile, specs, workflows, contracts, sequence)
    except Exception as exc:
        print("FAIL SPEC_ERROR:" + str(exc))
        return 1
    if failures:
        for item in failures:
            print("FAIL " + item)
        print("RESULT=FAIL failures=" + str(len(failures)))
        return 1

    all_wanted = wanted_doc_names(required, contracts, sequence)
    impact_plan: dict[str, object] = {
        "affected_docs": sorted(all_wanted),
        "facts_affected": True,
        "broad": True,
        "unknown_paths": [],
        "changed_paths": [],
        "reasons": {},
        "final_acceptance_authority": False,
    }
    selected_wanted = set(all_wanted)
    facts_affected = True
    if args.incremental:
        impact_plan = plan_project_truth_impact(args.changed_path, all_wanted)
        selected_wanted = set(impact_plan["affected_docs"])
        facts_affected = bool(impact_plan["facts_affected"])

    facts_output = Path(args.facts_output)
    if not facts_output.is_absolute():
        facts_output = root / facts_output
    facts_missing = False
    facts_stale = False
    facts_written = False
    facts_recomputed = (not args.incremental) or facts_affected or not facts_output.is_file()

    if facts_recomputed:
        facts = extract_project_facts(root)
        expected_facts_text = json.dumps(facts, indent=2, sort_keys=True) + "\n"
        expected_facts_bytes = expected_facts_text.encode("utf-8")
        if args.check:
            if not facts_output.is_file():
                facts_missing = True
            elif canonical_generated_bytes(facts_output.read_bytes()) != expected_facts_bytes:
                facts_stale = True
        else:
            facts_output.parent.mkdir(parents=True, exist_ok=True)
            if not facts_output.is_file() or facts_output.read_bytes() != expected_facts_bytes:
                facts_output.write_bytes(expected_facts_bytes)
                facts_written = True
    else:
        try:
            facts = load_json(facts_output)
            if not clean(facts.get("source_digest")):
                raise ValueError("source_digest missing")
        except Exception as exc:
            print("FAIL INCREMENTAL_FACT_CACHE_INVALID:" + str(exc))
            return 1

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
        only=selected_wanted if args.incremental else None,
    )

    missing: list[str] = []
    stale: list[str] = []
    root_duplicates: list[str] = []
    written_docs: list[str] = []
    unchanged_docs: list[str] = []
    if not args.check:
        docs_root.mkdir(parents=True, exist_ok=True)

    for name, expected in docs.items():
        path = docs_root / name
        legacy_root_path = root / name
        if legacy_root_path.is_file():
            root_duplicates.append(name)
        expected_bytes = expected.encode("utf-8")
        if args.check:
            if not path.is_file():
                missing.append("docs/" + name)
            elif canonical_generated_bytes(path.read_bytes()) != expected_bytes:
                stale.append("docs/" + name)
        else:
            if path.is_file() and path.read_bytes() == expected_bytes:
                unchanged_docs.append("docs/" + name)
            else:
                path.write_bytes(expected_bytes)
                written_docs.append("docs/" + name)

    report = {
        "schema_version": 2,
        "profile": profile,
        "docs_root": str(docs_root.relative_to(root).as_posix()),
        "source_digest": facts["source_digest"],
        "input_digest": digest,
        "generated_docs": sorted("docs/" + name for name in docs),
        "all_generated_docs": sorted("docs/" + name for name in all_wanted),
        "missing_docs": missing,
        "stale_docs": stale,
        "legacy_root_doc_duplicates": sorted(root_duplicates),
        "facts_missing": facts_missing,
        "facts_stale": facts_stale,
        "facts_affected": facts_affected,
        "facts_recomputed": facts_recomputed,
        "facts_written": facts_written,
        "written_docs": sorted(written_docs),
        "unchanged_docs": sorted(unchanged_docs),
        "incremental": bool(args.incremental),
        "impact_broad": bool(impact_plan.get("broad", False)),
        "unknown_paths": list(impact_plan.get("unknown_paths", [])),
        "changed_paths": list(impact_plan.get("changed_paths", [])),
        "impact_reasons": dict(impact_plan.get("reasons", {})),
        "project_docs_normalized": True,
        "doc_layout": "FAIL" if root_duplicates else "PASS",
        "mode": "check" if args.check else "write",
        "acceptance_scope": "INTERMEDIATE_IMPACT_SUBSET" if args.incremental else "EXHAUSTIVE",
        "final_acceptance_authority": False if args.incremental else None,
        "result": "FAIL" if missing or stale or root_duplicates or facts_missing or facts_stale else "PASS",
        "semantic_boundary": (
            "Compiler projects declared semantic/governance specs and machine-observed code facts; "
            "incremental mode is intermediate-only and unknown impact broadens fail-closed."
        ),
    }

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = root / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["result"] == "FAIL" else 0

if __name__ == "__main__":
    sys.exit(main())

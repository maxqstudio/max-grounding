#!/usr/bin/env python3
"""Validate deterministic human-facing sequence projections."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from sequence_contract import compute_source_digest, git_head, load_json
from sequence_human_view import (
    POLICY_ID,
    project_human_graph,
    render_human_markdown,
    render_human_mermaid,
)


def resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def expected_human_mermaid(human: dict) -> str:
    return (
        "%% GENERATED FILE - DO NOT EDIT\n"
        f"%% SOURCE_DIGEST: {human.get('source_digest', '')}\n"
        f"%% POLICY_ID: {POLICY_ID}\n"
        "%% GENERATED_BY: sequence_human_view.py\n"
        + render_human_mermaid(human)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--session", required=True)
    args = ap.parse_args()

    start = Path(args.root).resolve()
    try:
        root = Path(
            subprocess.check_output(
                ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                text=True,
            ).strip()
        )
        head = git_head(root)
        source_digest = compute_source_digest(root)
    except Exception as exc:
        print(json.dumps({"result": "FAIL", "failures": [f"GIT_ERROR:{exc}"]}, indent=2))
        return 1

    session_path = resolve(root, args.session)
    if not session_path.is_file():
        print(json.dumps({"result": "FAIL", "failures": [f"MISSING_SESSION:{args.session}"]}, indent=2))
        return 1

    session = load_json(session_path)
    human_cfg = session.get("human_view")
    scope = str(session.get("scope", "CURRENT")).upper()
    failures: list[str] = []

    if not isinstance(human_cfg, dict) or not human_cfg:
        report = {
            "result": "NOT_APPLICABLE",
            "session": str(session_path.relative_to(root)),
            "reason": "HUMAN_VIEW_NOT_DECLARED",
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    actual_cfg = session.get("actual", {})
    actual_graph_rel = str(actual_cfg.get("graph", "")).strip()
    actual_diagram_rel = str(actual_cfg.get("diagram", "")).strip()
    human_graph_rel = str(human_cfg.get("graph", "")).strip()
    human_diagram_rel = str(human_cfg.get("diagram", "")).strip()
    human_document_rel = str(human_cfg.get("document", "")).strip()

    for key, value in (
        ("ACTUAL_GRAPH", actual_graph_rel),
        ("ACTUAL_DIAGRAM", actual_diagram_rel),
        ("HUMAN_GRAPH", human_graph_rel),
        ("HUMAN_DIAGRAM", human_diagram_rel),
        ("HUMAN_DOCUMENT", human_document_rel),
    ):
        if not value:
            failures.append(f"{key}_PATH_MISSING")

    if str(human_cfg.get("policy_id", "")).strip() != POLICY_ID:
        failures.append("HUMAN_POLICY_ID_MISMATCH")
    if str(human_cfg.get("granularity", "")).strip() != "module":
        failures.append("HUMAN_GRANULARITY_MUST_BE_MODULE")

    actual_path = resolve(root, actual_graph_rel) if actual_graph_rel else root / "__missing_actual__"
    human_path = resolve(root, human_graph_rel) if human_graph_rel else root / "__missing_human__"
    human_diagram_path = resolve(root, human_diagram_rel) if human_diagram_rel else root / "__missing_human_diagram__"
    human_document_path = resolve(root, human_document_rel) if human_document_rel else root / "__missing_human_document__"

    actual: dict = {}
    observed_human: dict = {}
    if not actual_path.is_file():
        failures.append("MISSING_ACTUAL_GRAPH:" + actual_graph_rel)
    else:
        try:
            actual = load_json(actual_path)
        except Exception as exc:
            failures.append("INVALID_ACTUAL_GRAPH:" + str(exc))

    if not human_path.is_file():
        failures.append("MISSING_HUMAN_GRAPH:" + human_graph_rel)
    else:
        try:
            observed_human = load_json(human_path)
        except Exception as exc:
            failures.append("INVALID_HUMAN_GRAPH:" + str(exc))

    expected: dict = {}
    if actual:
        expected = project_human_graph(actual)
        if expected["projection"]["unresolved_machine_edges"]:
            failures.append(
                "HUMAN_PROJECTION_UNRESOLVED_MACHINE_EDGES:"
                + str(len(expected["projection"]["unresolved_machine_edges"]))
            )

    if observed_human and expected and observed_human != expected:
        failures.append("HUMAN_GRAPH_NOT_DETERMINISTIC_PROJECTION")

    if observed_human:
        if observed_human.get("generated") is not True:
            failures.append("HUMAN_GRAPH_NOT_GENERATED")
        if observed_human.get("generated_by") != "sequence_human_view.py":
            failures.append("HUMAN_GRAPH_UNKNOWN_GENERATOR")
        if observed_human.get("policy_id") != POLICY_ID:
            failures.append("HUMAN_GRAPH_POLICY_MISMATCH")

        observed_digest = str(observed_human.get("source_digest", ""))
        recorded_digest = str(human_cfg.get("source_digest", "")).strip()
        actual_digest = str(actual.get("source_digest", "")) if actual else ""
        if not observed_digest:
            failures.append("HUMAN_SOURCE_DIGEST_MISSING")
        if recorded_digest and recorded_digest != observed_digest:
            failures.append("SESSION_HUMAN_SOURCE_DIGEST_MISMATCH")
        if actual_digest and observed_digest != actual_digest:
            failures.append("HUMAN_ACTUAL_SOURCE_DIGEST_MISMATCH")
        if scope == "CURRENT" and observed_digest != source_digest:
            failures.append("HUMAN_SOURCE_DIGEST_STALE")

        projection = observed_human.get("projection", {})
        complexity = observed_human.get("complexity", {})
        node_map = projection.get("node_map", {})
        actual_nodes = actual.get("nodes", []) if actual else []
        actual_edges = actual.get("edges", []) if actual else []
        actual_ids = {str(node.get("id", "")).strip() for node in actual_nodes if node.get("id")}
        if set(node_map) != actual_ids:
            failures.append("HUMAN_NODE_MAP_INCOMPLETE")
        if int(projection.get("machine_node_count", -1)) != len(actual_nodes):
            failures.append("HUMAN_MACHINE_NODE_COUNT_MISMATCH")
        if int(projection.get("machine_edge_count", -1)) != len(actual_edges):
            failures.append("HUMAN_MACHINE_EDGE_COUNT_MISMATCH")

        internal = int(projection.get("internal_machine_edges_collapsed", -1))
        cross = int(projection.get("cross_component_machine_edges", -1))
        if internal < 0 or cross < 0 or internal + cross != len(actual_edges):
            failures.append("HUMAN_MACHINE_EDGE_ACCOUNTING_MISMATCH")

        human_nodes = observed_human.get("nodes", [])
        human_edges = observed_human.get("edges", [])
        if int(complexity.get("participant_count", -1)) != len(human_nodes):
            failures.append("HUMAN_PARTICIPANT_COUNT_MISMATCH")
        if int(complexity.get("interaction_count", -1)) != len(human_edges):
            failures.append("HUMAN_INTERACTION_COUNT_MISMATCH")
        if int(complexity.get("participant_ceiling", -1)) != len(set(node_map.values())):
            failures.append("HUMAN_PARTICIPANT_CEILING_MISMATCH")

        directed_pairs = {(str(e.get("from", "")), str(e.get("to", ""))) for e in human_edges}
        if len(directed_pairs) != len(human_edges):
            failures.append("HUMAN_DUPLICATE_DIRECTED_COMPONENT_PAIR")
        if int(complexity.get("interaction_ceiling", -1)) != len(directed_pairs):
            failures.append("HUMAN_INTERACTION_CEILING_MISMATCH")
        if complexity.get("max_participants_per_semantic_component") != 1:
            failures.append("HUMAN_COMPONENT_PARTICIPANT_POLICY_MISMATCH")
        if complexity.get("max_interactions_per_directed_component_pair") != 1:
            failures.append("HUMAN_COMPONENT_INTERACTION_POLICY_MISMATCH")
        if complexity.get("absolute_numeric_limit", "unexpected") is not None:
            failures.append("HUMAN_ABSOLUTE_NUMERIC_LIMIT_FORBIDDEN")

        cross_sum = sum(int(edge.get("machine_edge_count", 0)) for edge in human_edges)
        if cross_sum != cross:
            failures.append("HUMAN_CROSS_EDGE_ACCOUNTING_MISMATCH")

        for node in human_nodes:
            if "::" in str(node.get("label", "")):
                failures.append("HUMAN_RAW_SYMBOL_LABEL_FORBIDDEN")
                break

        human_entries = [str(x) for x in observed_human.get("entries", [])]
        actual_entries = [str(x) for x in actual.get("entries", [])] if actual else []
        if human_entries != actual_entries:
            failures.append("HUMAN_ENTRYPOINT_MISMATCH")

    if expected:
        expected_mermaid = expected_human_mermaid(expected)
        if not human_diagram_path.is_file():
            failures.append("MISSING_HUMAN_DIAGRAM:" + human_diagram_rel)
        else:
            observed_mermaid = human_diagram_path.read_text(encoding="utf-8", errors="ignore")
            if observed_mermaid != expected_mermaid:
                failures.append("GENERATED_HUMAN_DIAGRAM_TAMPERED")

        session_id = str(session.get("session_id", "")).strip()
        expected_markdown = render_human_markdown(
            session_id=session_id,
            actual_graph_rel=actual_graph_rel,
            actual_diagram_rel=actual_diagram_rel,
            human_graph_rel=human_graph_rel,
            human=expected,
        )
        if not human_document_path.is_file():
            failures.append("MISSING_HUMAN_DOCUMENT:" + human_document_rel)
        else:
            observed_markdown = human_document_path.read_text(encoding="utf-8", errors="ignore")
            if observed_markdown != expected_markdown:
                failures.append("GENERATED_HUMAN_DOCUMENT_TAMPERED")
            if "```mermaid\n" not in observed_markdown:
                failures.append("HUMAN_DOCUMENT_MERMAID_BLOCK_MISSING")

    report = {
        "schema_version": 1,
        "session": str(session_path.relative_to(root)),
        "session_id": session.get("session_id"),
        "scope": scope,
        "final_head": head,
        "source_digest": source_digest,
        "policy_id": POLICY_ID,
        "machine_nodes": len(actual.get("nodes", [])) if actual else 0,
        "machine_edges": len(actual.get("edges", [])) if actual else 0,
        "human_participants": len(observed_human.get("nodes", [])) if observed_human else 0,
        "human_interactions": len(observed_human.get("edges", [])) if observed_human else 0,
        "failures": failures,
        "render_validation": "EXTERNAL_BLOCKING_GATE_REQUIRED",
        "result": "FAIL" if failures else "PASS",
        "evidence_boundary": (
            "This validator proves deterministic projection, complete machine-edge accounting, "
            "and structural complexity policy. Mermaid syntax/renderability is proven separately "
            "by the blocking renderer gate."
        ),
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

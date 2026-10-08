#!/usr/bin/env python3
"""Generate a bounded human-facing projection of machine sequence evidence.

The full machine graph remains authoritative evidence. This module derives a
separate module-level projection for humans without deleting or rewriting any
machine node or edge.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

from sequence_contract import load_json, write_json

POLICY_ID = "module-collapse-v1"


def _clean_label(value: str) -> str:
    return value.replace("\n", " ").replace('"', "'").strip()


def _machine_component(node: dict) -> tuple[str, str, str]:
    node_id = str(node.get("id", "")).strip()
    locator = str(node.get("locator") or node_id).strip()
    kind = str(node.get("kind", "participant")).strip().lower()

    if node_id.startswith("HTTP "):
        return f"external:{node_id}", node_id, "external"

    if "::" in locator:
        module = locator.split("::", 1)[0]
        return f"module:{module}", module, "module"

    if "::" in node_id:
        module = node_id.split("::", 1)[0]
        return f"module:{module}", module, "module"

    label = str(node.get("label") or locator or node_id).strip()
    if kind in {"external", "actor"}:
        return f"{kind}:{node_id}", label, kind
    return f"component:{node_id}", label, "component"


def project_human_graph(actual: dict) -> dict:
    nodes = actual.get("nodes", [])
    edges = actual.get("edges", [])

    components: dict[str, dict] = {}
    node_map: dict[str, str] = {}
    component_members: dict[str, list[str]] = defaultdict(list)

    for node in nodes:
        machine_id = str(node.get("id", "")).strip()
        if not machine_id:
            continue
        component_id, label, kind = _machine_component(node)
        node_map[machine_id] = component_id
        component_members[component_id].append(machine_id)
        if component_id not in components:
            components[component_id] = {
                "id": component_id,
                "label": label,
                "kind": kind,
            }

    grouped: dict[tuple[str, str], dict] = {}
    internal_edges = 0
    cross_edges = 0
    unresolved_edges: list[dict] = []

    for edge in edges:
        src_machine = str(edge.get("from", "")).strip()
        dst_machine = str(edge.get("to", "")).strip()
        src = node_map.get(src_machine)
        dst = node_map.get(dst_machine)
        if not src or not dst:
            unresolved_edges.append(edge)
            continue
        if src == dst:
            internal_edges += 1
            continue

        cross_edges += 1
        key = (src, dst)
        item = grouped.setdefault(
            key,
            {
                "from": src,
                "to": dst,
                "machine_edge_count": 0,
                "actions": set(),
                "resolvers": set(),
                "evidence": set(),
            },
        )
        item["machine_edge_count"] += 1
        for field, bucket in (("action", "actions"), ("resolver", "resolvers"), ("evidence", "evidence")):
            value = str(edge.get(field, "")).strip()
            if value:
                item[bucket].add(value)

    human_edges = []
    for key in sorted(grouped):
        item = grouped[key]
        human_edges.append(
            {
                "from": item["from"],
                "to": item["to"],
                "machine_edge_count": item["machine_edge_count"],
                "actions": sorted(item["actions"]),
                "resolvers": sorted(item["resolvers"]),
                "evidence": sorted(item["evidence"]),
            }
        )

    human_nodes = []
    for component_id in sorted(components):
        node = dict(components[component_id])
        node["machine_node_count"] = len(component_members[component_id])
        node["machine_nodes"] = sorted(component_members[component_id])
        human_nodes.append(node)

    projection = {
        "node_map": dict(sorted(node_map.items())),
        "machine_node_count": len(nodes),
        "machine_edge_count": len(edges),
        "internal_machine_edges_collapsed": internal_edges,
        "cross_component_machine_edges": cross_edges,
        "aggregated_cross_component_edges": cross_edges - len(human_edges),
        "unresolved_machine_edges": unresolved_edges,
    }

    complexity = {
        "policy_id": POLICY_ID,
        "participant_granularity": "semantic_module_or_external_boundary",
        "participant_count": len(human_nodes),
        "participant_ceiling": len(set(node_map.values())),
        "interaction_count": len(human_edges),
        "interaction_ceiling": len(grouped),
        "max_participants_per_semantic_component": 1,
        "max_interactions_per_directed_component_pair": 1,
        "absolute_numeric_limit": None,
        "rationale": (
            "Ceilings are derived from distinct semantic components and directed "
            "component pairs. No arbitrary global participant/interaction limit is "
            "used by module-collapse-v1."
        ),
    }

    return {
        "schema_version": 1,
        "generated": True,
        "generated_by": "sequence_human_view.py",
        "policy_id": POLICY_ID,
        "source_digest": str(actual.get("source_digest", "")),
        "observed_head": str(actual.get("observed_head", "")),
        "entries": [str(x) for x in actual.get("entries", [])],
        "nodes": human_nodes,
        "edges": human_edges,
        "projection": projection,
        "complexity": complexity,
        "evidence_boundary": (
            "Human view is a deterministic projection of static machine evidence. "
            "It does not prove runtime ordering and cannot replace the full machine graph."
        ),
    }


def _sanitize_alias(value: str, index: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not base or base[0].isdigit():
        base = "p_" + base
    return f"{base}_{index}"


def render_human_mermaid(human: dict) -> str:
    aliases: dict[str, str] = {}
    lines = ["sequenceDiagram"]

    for index, node in enumerate(human.get("nodes", [])):
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            continue
        alias = _sanitize_alias(node_id, index)
        aliases[node_id] = alias
        label = _clean_label(str(node.get("label") or node_id))
        kind = str(node.get("kind", "participant")).lower()
        prefix = "actor" if kind == "actor" else "participant"
        lines.append(f"    {prefix} {alias} as {label}")

    for edge in human.get("edges", []):
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        if src not in aliases or dst not in aliases:
            continue
        count = int(edge.get("machine_edge_count", 0))
        label = f"{count} static interaction" + ("" if count == 1 else "s")
        lines.append(f"    {aliases[src]}->>{aliases[dst]}: {label}")

    return "\n".join(lines) + "\n"


def render_human_markdown(
    *,
    session_id: str,
    actual_graph_rel: str,
    actual_diagram_rel: str,
    human_graph_rel: str,
    human: dict,
) -> str:
    source_digest = str(human.get("source_digest", ""))
    projection = human.get("projection", {})
    complexity = human.get("complexity", {})
    mermaid = render_human_mermaid(human)

    # Canonical human views live under docs/sequence/views/.
    def view_link(target: str) -> str:
        return Path("..", "..", "..", Path(target)).as_posix()

    return (
        "<!-- GENERATED BY SEQUENCE HUMAN VIEW - DO NOT EDIT -->\n\n"
        f"# {session_id} — Human Sequence View\n\n"
        "> This diagram is a deterministic **static structural projection**. "
        "It collapses helper-level calls into semantic modules for readability. "
        "It does **not** prove runtime ordering and does not replace the full machine evidence.\n\n"
        "## Evidence\n\n"
        f"- Machine graph: [{actual_graph_rel}]({view_link(actual_graph_rel)})\n"
        f"- Full machine Mermaid: [{actual_diagram_rel}]({view_link(actual_diagram_rel)})\n"
        f"- Human projection data: [{human_graph_rel}]({view_link(human_graph_rel)})\n"
        f"- Source digest: `{source_digest}`\n\n"
        "## Complexity\n\n"
        "| Metric | Machine | Human |\n"
        "|---|---:|---:|\n"
        f"| Participants / nodes | {projection.get('machine_node_count', 0)} | {complexity.get('participant_count', 0)} |\n"
        f"| Interactions / edges | {projection.get('machine_edge_count', 0)} | {complexity.get('interaction_count', 0)} |\n"
        f"| Internal machine edges collapsed | {projection.get('internal_machine_edges_collapsed', 0)} | — |\n"
        f"| Cross-component edges aggregated | {projection.get('aggregated_cross_component_edges', 0)} | — |\n\n"
        "Policy `module-collapse-v1`: one participant per semantic module/external boundary "
        "and one rendered interaction per directed component pair. The ceilings are derived "
        "from the graph itself; this policy does not invent a global numeric readability limit.\n\n"
        "## Diagram\n\n"
        "```mermaid\n"
        f"{mermaid}"
        "```\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--actual-json", required=True)
    ap.add_argument("--actual-mermaid", required=True)
    ap.add_argument("--output-json", required=True)
    ap.add_argument("--output-mermaid", required=True)
    ap.add_argument("--output-markdown", required=True)
    ap.add_argument("--session-id", required=True)
    args = ap.parse_args()

    root = Path(args.root).resolve()

    def resolve(value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else root / path

    actual = load_json(resolve(args.actual_json))
    human = project_human_graph(actual)

    unresolved = human["projection"]["unresolved_machine_edges"]
    if unresolved:
        print(f"FAIL HUMAN_PROJECTION_UNRESOLVED_MACHINE_EDGES={len(unresolved)}")
        return 1

    output_json = resolve(args.output_json)
    output_mermaid = resolve(args.output_mermaid)
    output_markdown = resolve(args.output_markdown)

    write_json(output_json, human)

    mermaid = (
        "%% GENERATED FILE - DO NOT EDIT\n"
        f"%% SOURCE_DIGEST: {human.get('source_digest', '')}\n"
        f"%% POLICY_ID: {POLICY_ID}\n"
        "%% GENERATED_BY: sequence_human_view.py\n"
        + render_human_mermaid(human)
    )
    output_mermaid.parent.mkdir(parents=True, exist_ok=True)
    output_mermaid.write_text(mermaid, encoding="utf-8", newline="\n")

    markdown = render_human_markdown(
        session_id=args.session_id,
        actual_graph_rel=args.actual_json,
        actual_diagram_rel=args.actual_mermaid,
        human_graph_rel=args.output_json,
        human=human,
    )
    output_markdown.parent.mkdir(parents=True, exist_ok=True)
    output_markdown.write_text(markdown, encoding="utf-8", newline="\n")

    projection = human["projection"]
    complexity = human["complexity"]
    print(f"MACHINE_NODES={projection['machine_node_count']}")
    print(f"MACHINE_EDGES={projection['machine_edge_count']}")
    print(f"HUMAN_PARTICIPANTS={complexity['participant_count']}")
    print(f"HUMAN_INTERACTIONS={complexity['interaction_count']}")
    print(f"INTERNAL_EDGES_COLLAPSED={projection['internal_machine_edges_collapsed']}")
    print(f"CROSS_EDGES_AGGREGATED={projection['aggregated_cross_component_edges']}")
    print(f"POLICY_ID={POLICY_ID}")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

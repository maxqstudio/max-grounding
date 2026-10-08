#!/usr/bin/env python3
"""Shared helpers for generated sequence contracts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from project_snapshot import (
    ProjectSnapshot,
    active_snapshot_for,
    canonical_source_bytes,
    discover_source_paths,
)

VALID_MODES = {"BEFORE", "DURING", "AFTER"}
VALID_REQUIREMENTS = {"MUST", "MAY", "MUST_NOT"}
VALID_VERIFICATION = {"SOURCE", "RUNTIME", "BOTH", "DOCUMENT"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def git_head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def _selected_snapshot(root: Path, snapshot: ProjectSnapshot | None) -> ProjectSnapshot | None:
    root = root.resolve()
    selected = snapshot or active_snapshot_for(root)
    if selected is not None and selected.root != root:
        raise ValueError(
            f"SNAPSHOT_ROOT_MISMATCH:{selected.root.as_posix()}:{root.as_posix()}"
        )
    return selected


def source_files(
    root: Path,
    snapshot: ProjectSnapshot | None = None,
) -> list[Path]:
    root = root.resolve()
    selected = _selected_snapshot(root, snapshot)
    if selected is not None:
        return selected.source_files()
    return discover_source_paths(root)


def compute_source_digest(
    root: Path,
    snapshot: ProjectSnapshot | None = None,
) -> str:
    root = root.resolve()
    selected = _selected_snapshot(root, snapshot)
    if selected is not None:
        return selected.source_digest

    digest = hashlib.sha256()
    for path in discover_source_paths(root):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(canonical_source_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "-C", str(root), "merge-base", "--is-ancestor", ancestor, descendant],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def git_tree(root: Path, commit: str) -> str:
    return git(root, "rev-parse", f"{commit}^{{tree}}")


def validate_before_implementation_lineage(
    root: Path,
    implementation_base: str,
    head: str,
    merge_provenance: object,
) -> dict:
    """Validate direct ancestry or an explicit content-identical squash bridge."""
    result = {
        "mode": "DIRECT",
        "strategy": "",
        "accepted_branch_head": "",
        "product_merge_sha": "",
        "accepted_branch_tree": "",
        "product_merge_tree": "",
        "failures": [],
    }
    if is_ancestor(root, implementation_base, head):
        return result

    result["mode"] = "MERGE_PROVENANCE"
    if not isinstance(merge_provenance, dict):
        result["failures"].append("IMPLEMENTATION_BASE_NOT_ANCESTOR_OF_HEAD")
        return result

    strategy = str(merge_provenance.get("strategy", "")).strip().upper()
    accepted_branch_head = str(merge_provenance.get("accepted_branch_head", "")).strip()
    product_merge_sha = str(merge_provenance.get("product_merge_sha", "")).strip()
    result.update({
        "strategy": strategy,
        "accepted_branch_head": accepted_branch_head,
        "product_merge_sha": product_merge_sha,
    })
    if strategy != "SQUASH":
        result["failures"].append("INVALID_MERGE_PROVENANCE_STRATEGY:" + strategy)
        return result
    if not accepted_branch_head:
        result["failures"].append("SQUASH_ACCEPTED_BRANCH_HEAD_MISSING")
    if not product_merge_sha:
        result["failures"].append("SQUASH_PRODUCT_MERGE_SHA_MISSING")
    if result["failures"]:
        return result
    if not is_ancestor(root, implementation_base, accepted_branch_head):
        result["failures"].append("SQUASH_ACCEPTED_BRANCH_NOT_DESCENDANT_OF_IMPLEMENTATION_BASE")
    if not is_ancestor(root, product_merge_sha, head):
        result["failures"].append("SQUASH_PRODUCT_MERGE_NOT_ANCESTOR_OF_HEAD")
    try:
        accepted_tree = git_tree(root, accepted_branch_head)
        result["accepted_branch_tree"] = accepted_tree
    except Exception:
        result["failures"].append("SQUASH_ACCEPTED_BRANCH_HEAD_UNRESOLVED")
        accepted_tree = ""
    try:
        product_tree = git_tree(root, product_merge_sha)
        result["product_merge_tree"] = product_tree
    except Exception:
        result["failures"].append("SQUASH_PRODUCT_MERGE_SHA_UNRESOLVED")
        product_tree = ""
    if accepted_tree and product_tree and accepted_tree != product_tree:
        result["failures"].append("SQUASH_TREE_MISMATCH")
    return result


def sanitize_alias(value: str, index: int) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not base or base[0].isdigit():
        base = "p_" + base
    return f"{base}_{index}"


def render_graph_mermaid(graph: dict) -> str:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    aliases: dict[str, str] = {}
    lines = ["sequenceDiagram"]

    for i, node in enumerate(nodes):
        node_id = str(node.get("id", "")).strip()
        if not node_id:
            continue
        alias = sanitize_alias(node_id, i)
        aliases[node_id] = alias
        label = str(node.get("label") or node.get("locator") or node_id)
        label = label.replace("\n", " ").replace('"', "'")
        kind = str(node.get("kind", "participant")).lower()
        prefix = "actor" if kind == "actor" else "participant"
        lines.append(f'    {prefix} {alias} as {label}')

    for edge in edges:
        src = str(edge.get("from", "")).strip()
        dst = str(edge.get("to", "")).strip()
        if src not in aliases or dst not in aliases:
            continue
        action = str(edge.get("action", "call")).replace("\n", " ")
        evidence = str(edge.get("evidence", "")).strip()
        requirement = str(edge.get("requirement", "")).strip()
        suffix_parts = [x for x in (requirement, evidence) if x]
        if suffix_parts:
            action += " [" + ",".join(suffix_parts) + "]"
        lines.append(f"    {aliases[src]}->>{aliases[dst]}: {action}")

    return "\n".join(lines) + "\n"


def plan_locator_map(plan: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in plan.get("nodes", []):
        node_id = str(node.get("id", "")).strip()
        locator = str(node.get("locator", "")).strip()
        if node_id and locator:
            result[node_id] = locator
    return result


def graph_edge_set(graph: dict) -> set[tuple[str, str]]:
    return {
        (str(e.get("from", "")).strip(), str(e.get("to", "")).strip())
        for e in graph.get("edges", [])
        if e.get("from") and e.get("to")
    }


def compare_plan_actual(plan: dict, actual: dict, verification: str = "SOURCE") -> dict:
    locators = plan_locator_map(plan)
    actual_edges = graph_edge_set(actual)

    missing: list[dict] = []
    forbidden: list[dict] = []
    unresolved: list[dict] = []

    for edge in plan.get("edges", []):
        requirement = str(edge.get("requirement", "MUST")).upper()
        verify = str(edge.get("verification", "SOURCE")).upper()

        if requirement not in VALID_REQUIREMENTS:
            unresolved.append({"reason": "INVALID_REQUIREMENT", "edge": edge})
            continue
        if verify not in VALID_VERIFICATION:
            unresolved.append({"reason": "INVALID_VERIFICATION", "edge": edge})
            continue
        if verify == "DOCUMENT":
            continue
        if verification == "SOURCE" and verify not in {"SOURCE", "BOTH"}:
            continue
        if verification == "RUNTIME" and verify not in {"RUNTIME", "BOTH"}:
            continue

        src_id = str(edge.get("from", ""))
        dst_id = str(edge.get("to", ""))
        src = locators.get(src_id)
        dst = locators.get(dst_id)
        if not src or not dst:
            unresolved.append({"reason": "UNRESOLVED_PLAN_LOCATOR", "edge": edge})
            continue

        present = (src, dst) in actual_edges
        if requirement == "MUST" and not present:
            missing.append(edge)
        elif requirement == "MUST_NOT" and present:
            forbidden.append(edge)

    return {
        "missing_required_edges": missing,
        "forbidden_edges_present": forbidden,
        "unresolved_bindings": unresolved,
        "match": not missing and not forbidden and not unresolved,
    }

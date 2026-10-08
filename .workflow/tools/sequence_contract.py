#!/usr/bin/env python3
"""Shared helpers for generated sequence contracts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path


VALID_MODES = {"BEFORE", "DURING", "AFTER"}
VALID_REQUIREMENTS = {"MUST", "MAY", "MUST_NOT"}
VALID_VERIFICATION = {"SOURCE", "RUNTIME", "BOTH", "DOCUMENT"}

SOURCE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".cs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs", ".go",
    ".swift", ".m", ".mm", ".php", ".rb", ".scala", ".sh", ".ps1", ".sql",
    ".proto", ".graphql", ".gql", ".xml", ".gradle",
}
SOURCE_EXCLUDED_PARTS = {
    ".git", ".workflow", ".idea", ".vscode", ".venv", "venv", "node_modules", "dist",
    "build", "coverage", "vendor", "__pycache__",
    ".runtime", ".evidence", ".local-acceptance", ".pytest_cache",
}


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


def source_files(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        rel = path.relative_to(root)
        if any(part in SOURCE_EXCLUDED_PARTS for part in rel.parts):
            continue
        result.append(path)
    return sorted(result, key=lambda p: p.relative_to(root).as_posix())


def compute_source_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(root):
        rel = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(rel)
        digest.update(b"\0")
        digest.update(path.read_bytes())
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

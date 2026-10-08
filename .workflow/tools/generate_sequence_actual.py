#!/usr/bin/env python3
"""Generate a static sequence graph from the current codebase.

Coverage:
- Python AST function/method definitions and resolvable intra-project calls.
- Python HTTP route decorators.
- JS/TS/JSX/TSX module-level fetch/axios HTTP edges.
- Cross-language HTTP path binding through generated HTTP participant nodes.

This is static structural evidence, not perfect runtime truth. Dynamic dispatch,
dependency injection, reflection, callbacks, framework magic, and unresolved
cross-language calls remain NOT_PROVEN unless stronger evidence exists.
"""

from __future__ import annotations

import argparse
import ast
import re
from collections import defaultdict, deque
from pathlib import Path

from analyzer_contract import (
    AnalyzerResult,
    DYNAMIC_BEHAVIOR_LIMITATIONS,
    coverage_records,
    generic_fallback,
    result_by_id,
    run_analyzers,
)
from project_snapshot import ProjectSnapshot
from sequence_contract import compute_source_digest, git_head, render_graph_mermaid, write_json

EXCLUDED = {
    ".git", ".workflow", ".idea", ".vscode", ".venv", "venv", "node_modules",
    "dist", "build", "coverage", "vendor", "__pycache__",
}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


def allowed(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return not any(part in EXCLUDED for part in rel.parts)


def call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        left = call_name(node.value)
        return f"{left}.{node.attr}" if left else ""
    return ""


def literal_string(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


class PyCollector(ast.NodeVisitor):
    def __init__(self, path: Path, root: Path) -> None:
        self.path = path
        self.root = root
        self.rel = path.relative_to(root).as_posix()
        self.stack: list[str] = []
        self.current: list[str] = []
        self.nodes: list[dict] = []
        self.calls: dict[str, list[str]] = defaultdict(list)
        self.routes: list[tuple[str, str, str]] = []

    def symbol_id(self, name: str) -> str:
        qual = ".".join(self.stack + [name]) if self.stack else name
        return f"{self.rel}::{qual}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol = self.symbol_id(node.name)
        self.nodes.append({
            "id": symbol,
            "label": symbol,
            "locator": symbol,
            "kind": "class",
            "language": "Python",
            "line_start": getattr(node, "lineno", None),
            "line_end": getattr(node, "end_lineno", None),
        })
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _visit_function(self, node: ast.AST, name: str, kind: str) -> None:
        symbol = self.symbol_id(name)
        self.nodes.append({
            "id": symbol,
            "label": symbol,
            "locator": symbol,
            "kind": kind,
            "language": "Python",
            "line_start": getattr(node, "lineno", None),
            "line_end": getattr(node, "end_lineno", None),
        })

        decorators = getattr(node, "decorator_list", [])
        for deco in decorators:
            if not isinstance(deco, ast.Call):
                continue
            target = call_name(deco.func)
            method = target.rsplit(".", 1)[-1].lower() if target else ""
            if method not in HTTP_METHODS or not deco.args:
                continue
            route = literal_string(deco.args[0])
            if route:
                self.routes.append((route, method.upper(), symbol))

        self.stack.append(name)
        self.current.append(symbol)
        for child in getattr(node, "body", []):
            self.visit(child)
        self.current.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, node.name, "method" if self.stack else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node, node.name, "async_method" if self.stack else "async_function")

    def visit_Call(self, node: ast.Call) -> None:
        if self.current:
            name = call_name(node.func)
            if name:
                self.calls[self.current[-1]].append(name)
        self.generic_visit(node)


def collect_python(root: Path) -> tuple[list[dict], list[dict], list[str]]:
    nodes: list[dict] = []
    raw_calls: dict[str, list[str]] = defaultdict(list)
    routes: list[tuple[str, str, str]] = []
    parse_failures: list[str] = []

    for path in sorted(root.rglob("*.py")):
        if not allowed(path, root):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            parse_failures.append(f"{path.relative_to(root).as_posix()}:{type(exc).__name__}")
            continue
        collector = PyCollector(path, root)
        collector.visit(tree)
        nodes.extend(collector.nodes)
        for key, values in collector.calls.items():
            raw_calls[key].extend(values)
        routes.extend(collector.routes)

    ids = {n["id"] for n in nodes}
    by_file_name: dict[tuple[str, str], list[str]] = defaultdict(list)
    by_leaf: dict[str, list[str]] = defaultdict(list)
    for symbol in ids:
        file_part, qual = symbol.split("::", 1)
        leaf = qual.split(".")[-1]
        by_file_name[(file_part, leaf)].append(symbol)
        by_leaf[leaf].append(symbol)

    edges: list[dict] = []
    for caller, calls in raw_calls.items():
        caller_file = caller.split("::", 1)[0]
        for token in calls:
            leaf = token.split(".")[-1]
            candidates = by_file_name.get((caller_file, leaf), [])
            if len(candidates) != 1:
                candidates = by_leaf.get(leaf, [])
            if len(candidates) == 1 and candidates[0] != caller:
                edges.append({
                    "from": caller,
                    "to": candidates[0],
                    "action": token,
                    "evidence": "STATIC",
                    "resolver": "python_ast",
                })

    existing = {n["id"] for n in nodes}
    for route, method, handler in routes:
        http_id = f"HTTP {route}"
        if http_id not in existing:
            nodes.append({
                "id": http_id,
                "label": http_id,
                "locator": http_id,
                "kind": "external",
                "language": "HTTP",
            })
            existing.add(http_id)
        edges.append({
            "from": http_id,
            "to": handler,
            "action": f"{method} route",
            "evidence": "STATIC",
            "resolver": "python_route_decorator",
        })

    return nodes, edges, parse_failures


FETCH_RE = re.compile(r"""fetch\s*\(\s*(["'])([^"']+)\1""")
AXIOS_RE = re.compile(
    r"""axios\s*\.\s*(get|post|put|patch|delete|head|options)\s*\(\s*(["'])([^"']+)\2""",
    re.IGNORECASE,
)


def collect_js_http(root: Path) -> tuple[list[dict], list[dict]]:
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[str] = set()

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in JS_EXTS or not allowed(path, root):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        module_id = f"{rel}::<module>"
        matches: list[tuple[str, str]] = []
        for match in FETCH_RE.finditer(text):
            matches.append(("FETCH", match.group(2)))
        for match in AXIOS_RE.finditer(text):
            matches.append((match.group(1).upper(), match.group(3)))

        if not matches:
            continue

        if module_id not in seen_nodes:
            nodes.append({
                "id": module_id,
                "label": module_id,
                "locator": module_id,
                "kind": "module",
                "language": "JavaScript/TypeScript",
            })
            seen_nodes.add(module_id)

        for method, url in matches:
            route = url.split("?", 1)[0]
            http_id = f"HTTP {route}"
            if http_id not in seen_nodes:
                nodes.append({
                    "id": http_id,
                    "label": http_id,
                    "locator": http_id,
                    "kind": "external",
                    "language": "HTTP",
                })
                seen_nodes.add(http_id)
            edges.append({
                "from": module_id,
                "to": http_id,
                "action": f"{method} request",
                "evidence": "STATIC",
                "resolver": "js_http_scan",
            })

    return nodes, edges


class PythonSequenceAnalyzer:
    analyzer_id = "python_sequence"
    claimed_extensions = frozenset({".py"})

    def analyze(self, root: Path, snapshot: ProjectSnapshot) -> AnalyzerResult:
        nodes, edges, parse_failures = collect_python(root)
        return AnalyzerResult(
            analyzer_id=self.analyzer_id,
            languages=("Python",),
            claimed_extensions=tuple(sorted(self.claimed_extensions)),
            semantic_level="python_ast_static",
            sequence_nodes=nodes,
            sequence_edges=edges,
            parse_failures=parse_failures,
            limitations=[*DYNAMIC_BEHAVIOR_LIMITATIONS],
        )


class JsTsHttpSequenceAnalyzer:
    analyzer_id = "js_ts_http_sequence"
    claimed_extensions = frozenset(JS_EXTS)

    def analyze(self, root: Path, snapshot: ProjectSnapshot) -> AnalyzerResult:
        nodes, edges = collect_js_http(root)
        return AnalyzerResult(
            analyzer_id=self.analyzer_id,
            languages=("JavaScript", "TypeScript"),
            claimed_extensions=tuple(sorted(self.claimed_extensions)),
            semantic_level="http_module_static",
            sequence_nodes=nodes,
            sequence_edges=edges,
            limitations=[
                *DYNAMIC_BEHAVIOR_LIMITATIONS,
                "JS/TS function-level call ownership is not fully resolved",
            ],
        )


def dedupe_nodes(nodes: list[dict]) -> list[dict]:
    out: dict[str, dict] = {}
    for node in nodes:
        node_id = str(node.get("id", ""))
        if node_id and node_id not in out:
            out[node_id] = node
    return sorted(out.values(), key=lambda n: n["id"])


def dedupe_edges(edges: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for edge in edges:
        key = (
            edge.get("from"),
            edge.get("to"),
            edge.get("action"),
            edge.get("resolver"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return sorted(out, key=lambda e: (str(e.get("from")), str(e.get("to")), str(e.get("action"))))


def filter_reachable(nodes: list[dict], edges: list[dict], entries: list[str], depth: int) -> tuple[list[dict], list[dict]]:
    if not entries:
        return nodes, edges

    adjacency: dict[str, list[str]] = defaultdict(list)
    for edge in edges:
        adjacency[str(edge["from"])].append(str(edge["to"]))

    keep = set(entries)
    queue = deque((entry, 0) for entry in entries)
    while queue:
        node, current_depth = queue.popleft()
        if current_depth >= depth:
            continue
        for nxt in adjacency.get(node, []):
            if nxt not in keep:
                keep.add(nxt)
                queue.append((nxt, current_depth + 1))

    changed = True
    while changed:
        changed = False
        for edge in edges:
            source = str(edge.get("from", ""))
            target = str(edge.get("to", ""))
            if target in keep and source.startswith("HTTP ") and source not in keep:
                keep.add(source)
                changed = True

    return (
        [node for node in nodes if node["id"] in keep],
        [edge for edge in edges if edge["from"] in keep and edge["to"] in keep],
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--output-mermaid", required=True)
    parser.add_argument("--entry", action="append", default=[])
    parser.add_argument("--max-depth", type=int, default=12)
    args = parser.parse_args()

    root = Path(args.root).resolve()
    head = git_head(root)
    source_digest = compute_source_digest(root)

    analyzers = (PythonSequenceAnalyzer(), JsTsHttpSequenceAnalyzer())
    snapshot, analyzer_results = run_analyzers(root, analyzers)
    claimed_extensions = frozenset(
        extension
        for analyzer in analyzers
        for extension in analyzer.claimed_extensions
    )
    fallback = generic_fallback(root, snapshot, claimed_extensions)
    analyzer_results.append(fallback)

    python_result = result_by_id(analyzer_results, "python_sequence")
    nodes = dedupe_nodes([
        node
        for result in analyzer_results
        for node in result.sequence_nodes
    ])
    edges = dedupe_edges([
        edge
        for result in analyzer_results
        for edge in result.sequence_edges
    ])

    known_ids = {node["id"] for node in nodes}
    missing_entries = [entry for entry in args.entry if entry not in known_ids]
    if missing_entries:
        for entry in missing_entries:
            print(f"FAIL ENTRY_NOT_RESOLVED:{entry}")
        return 1

    nodes, edges = filter_reachable(nodes, edges, args.entry, args.max_depth)

    graph = {
        "schema_version": 1,
        "generated": True,
        "generated_by": "generate_sequence_actual.py",
        "observed_head": head,
        "source_digest": source_digest,
        "entries": args.entry,
        "nodes": nodes,
        "edges": edges,
        "coverage": {
            "python_ast": True,
            "python_route_decorators": True,
            "js_ts_http_module_scan": True,
            "runtime_trace": False,
            "dynamic_behavior": "NOT_PROVEN",
            "limitations": [
                *DYNAMIC_BEHAVIOR_LIMITATIONS,
                "JS/TS function-level call ownership is not fully resolved",
            ],
            "python_parse_failures": python_result.parse_failures,
            "analyzers": coverage_records(analyzer_results),
            "generic_fallback": fallback.coverage_record(),
        },
    }

    output_json = Path(args.output_json)
    if not output_json.is_absolute():
        output_json = root / output_json
    output_mermaid = Path(args.output_mermaid)
    if not output_mermaid.is_absolute():
        output_mermaid = root / output_mermaid

    write_json(output_json, graph)
    mermaid = (
        "%% GENERATED FILE - DO NOT EDIT\n"
        f"%% SOURCE_DIGEST: {source_digest}\n"
        f"%% OBSERVED_HEAD: {head}\n"
        "%% GENERATED_BY: generate_sequence_actual.py\n"
        + render_graph_mermaid(graph)
    )
    output_mermaid.parent.mkdir(parents=True, exist_ok=True)
    output_mermaid.write_text(mermaid, encoding="utf-8", newline="\n")

    print(f"OBSERVED_HEAD={head}")
    print(f"SOURCE_DIGEST={source_digest}")
    print(f"NODES={len(nodes)}")
    print(f"EDGES={len(edges)}")
    print(f"PYTHON_PARSE_FAILURES={len(python_result.parse_failures)}")
    print(f"GENERIC_UNSUPPORTED_FILES={len(fallback.unsupported_files)}")
    print(f"ACTUAL_JSON={output_json}")
    print(f"ACTUAL_MERMAID={output_mermaid}")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

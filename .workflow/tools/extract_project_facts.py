#!/usr/bin/env python3
"""Deterministically extract project facts from a codebase.

This module extracts machine-observable facts only. It does not infer project
purpose, business authority, intended workflow semantics, or design rationale.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path

from analyzer_contract import (
    AnalyzerResult,
    DYNAMIC_BEHAVIOR_LIMITATIONS,
    coverage_records,
    generic_fallback,
    validate_result,
)
from project_snapshot import ProjectSnapshot, resolve_snapshot

LANGUAGE_BY_EXT = {
    ".py": "Python", ".pyi": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript/React",
    ".ts": "TypeScript", ".tsx": "TypeScript/React",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin",
    ".cs": "C#", ".c": "C", ".h": "C/C++ Header",
    ".cc": "C++", ".cpp": "C++", ".cxx": "C++",
    ".hh": "C/C++ Header", ".hpp": "C/C++ Header",
    ".rs": "Rust", ".go": "Go", ".swift": "Swift",
    ".m": "Objective-C", ".mm": "Objective-C++",
    ".php": "PHP", ".rb": "Ruby", ".scala": "Scala",
    ".sh": "Shell", ".ps1": "PowerShell", ".sql": "SQL",
    ".proto": "Protocol Buffers", ".graphql": "GraphQL",
    ".gql": "GraphQL", ".xml": "XML", ".gradle": "Gradle",
}

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
PYTHON_FACT_EXTENSIONS = frozenset({".py"})


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


class PythonFacts(ast.NodeVisitor):
    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.stack: list[str] = []
        self.symbols: list[dict] = []
        self.routes: list[dict] = []
        self.calls: list[dict] = []
        self.current: list[str] = []

    def locator(self, name: str) -> str:
        qual = ".".join(self.stack + [name]) if self.stack else name
        return f"{self.rel}::{qual}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        loc = self.locator(node.name)
        self.symbols.append({
            "file": self.rel,
            "symbol": loc.split("::", 1)[1],
            "locator": loc,
            "kind": "class",
            "line_start": getattr(node, "lineno", None),
            "line_end": getattr(node, "end_lineno", None),
        })
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    def _function(self, node: ast.AST, name: str, kind: str) -> None:
        loc = self.locator(name)
        self.symbols.append({
            "file": self.rel,
            "symbol": loc.split("::", 1)[1],
            "locator": loc,
            "kind": kind,
            "line_start": getattr(node, "lineno", None),
            "line_end": getattr(node, "end_lineno", None),
        })
        for deco in getattr(node, "decorator_list", []):
            if not isinstance(deco, ast.Call):
                continue
            target = call_name(deco.func)
            method = target.rsplit(".", 1)[-1].lower() if target else ""
            if method in HTTP_METHODS and deco.args:
                route = literal_string(deco.args[0])
                if route:
                    self.routes.append({
                        "method": method.upper(),
                        "route": route,
                        "handler": loc,
                    })
        self.stack.append(name)
        self.current.append(loc)
        for child in getattr(node, "body", []):
            self.visit(child)
        self.current.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, node.name, "method" if self.stack else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(
            node,
            node.name,
            "async_method" if self.stack else "async_function",
        )

    def visit_Call(self, node: ast.Call) -> None:
        if self.current:
            target = call_name(node.func)
            if target:
                self.calls.append({
                    "caller": self.current[-1],
                    "target_token": target,
                })
        self.generic_visit(node)


def is_test_file(rel: str) -> bool:
    lower = rel.lower()
    name = Path(rel).name.lower()
    return (
        "/tests/" in f"/{lower}"
        or lower.startswith("tests/")
        or name.startswith("test_")
        or name.endswith("_test.py")
        or ".test." in name
        or ".spec." in name
    )


def _extract_from_snapshot(root: Path, snapshot: ProjectSnapshot) -> dict:
    language_counts: Counter[str] = Counter()
    modules: list[dict] = []
    tests: list[str] = []
    symbols: list[dict] = []
    routes: list[dict] = []
    python_calls: list[dict] = []
    parse_failures: list[str] = []

    for path in snapshot.source_files():
        rel = path.relative_to(root).as_posix()
        language = LANGUAGE_BY_EXT.get(
            path.suffix.lower(),
            path.suffix.lower().lstrip(".") or "text",
        )
        try:
            source_text = snapshot.read_text(path, errors="ignore")
            line_count = len(source_text.splitlines())
        except Exception:
            source_text = ""
            line_count = 0

        language_counts[language] += 1
        modules.append({
            "file": rel,
            "language": language,
            "lines": line_count,
            "directory": path.relative_to(root).parent.as_posix(),
            "is_test": is_test_file(rel),
        })
        if is_test_file(rel):
            tests.append(rel)

        if path.suffix.lower() == ".py":
            try:
                tree = ast.parse(snapshot.read_text(path))
            except Exception as exc:
                parse_failures.append(f"{rel}:{type(exc).__name__}")
                continue
            visitor = PythonFacts(rel)
            visitor.visit(tree)
            symbols.extend(visitor.symbols)
            routes.extend(visitor.routes)
            python_calls.extend(visitor.calls)

    modules.sort(key=lambda x: x["file"])
    symbols.sort(key=lambda x: (x["file"], x.get("line_start") or 0, x["symbol"]))
    routes.sort(key=lambda x: (x["route"], x["method"], x["handler"]))
    python_calls.sort(key=lambda x: (x["caller"], x["target_token"]))
    tests = sorted(set(tests))

    python_result = validate_result(AnalyzerResult(
        analyzer_id="python_facts",
        languages=("Python",),
        claimed_extensions=tuple(sorted(PYTHON_FACT_EXTENSIONS)),
        semantic_level="python_ast_static",
        facts={
            "symbols": symbols,
            "routes": routes,
            "calls": python_calls,
        },
        parse_failures=sorted(parse_failures),
        limitations=[*DYNAMIC_BEHAVIOR_LIMITATIONS],
    ))
    fallback = generic_fallback(root, snapshot, PYTHON_FACT_EXTENSIONS)
    analyzer_results = [python_result, fallback]

    return {
        "schema_version": 1,
        "source_digest": snapshot.source_digest,
        "source_summary": {
            "files": len(modules),
            "lines": sum(x["lines"] for x in modules),
            "languages": dict(sorted(language_counts.items())),
            "test_files": len(tests),
        },
        "modules": modules,
        "python_symbols": python_result.facts["symbols"],
        "python_routes": python_result.facts["routes"],
        "python_calls": python_result.facts["calls"],
        "tests": tests,
        "coverage": {
            "module_inventory": "multi-language by extension",
            "symbol_inventory": "Python AST only",
            "route_inventory": "Python decorator routes only",
            "call_inventory": "Python AST token calls only",
            "limitations": [
                "non-Python symbol extraction requires a stronger language analyzer",
                "dynamic dispatch/dependency injection/reflection are not resolved",
                "JS/TS function-level semantics are not inferred here",
            ],
            "dynamic_behavior": "NOT_PROVEN",
            "python_parse_failures": python_result.parse_failures,
            "analyzers": coverage_records(analyzer_results),
            "generic_fallback": fallback.coverage_record(),
        },
    }


def extract_project_facts(
    root: Path,
    snapshot: ProjectSnapshot | None = None,
) -> dict:
    root = root.resolve()
    snap = resolve_snapshot(root, snapshot)
    return snap.memoized(
        "project_facts:v2",
        lambda: _extract_from_snapshot(root, snap),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--output", default=".workflow/generated/code_facts.json")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    facts = extract_project_facts(root)
    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(facts, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(f"SOURCE_DIGEST={facts['source_digest']}")
    print(f"SOURCE_FILES={facts['source_summary']['files']}")
    print(f"SOURCE_LINES={facts['source_summary']['lines']}")
    print(f"TEST_FILES={facts['source_summary']['test_files']}")
    print(f"OUTPUT={output}")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Generate machine-derived symbol facts for a project.

Strategy:
1. Prefer Universal Ctags when available for broad language coverage.
2. Fall back to Python AST for .py files when Ctags is unavailable.
3. Never invent semantic responsibility. Output structural facts only.

The generated file is not the canonical semantic SYMBOL_INDEX.md. It is a
machine-derived input used to refresh/verify the canonical index.
"""

from __future__ import annotations

import argparse
import ast
import json
import shutil
import subprocess
from pathlib import Path

EXCLUDED = {
    ".git", ".idea", ".vscode", ".venv", "venv", "node_modules",
    "dist", "build", "coverage", "vendor", "__pycache__",
    ".runtime", ".evidence", ".local-acceptance", ".pytest_cache",
}


def git_head(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "UNKNOWN"


def allowed(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return not any(part in EXCLUDED for part in rel.parts)


def python_symbols(root: Path) -> list[dict]:
    result: list[dict] = []

    for path in sorted(root.rglob("*.py")):
        if not allowed(path, root):
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            continue

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def _emit(self, node: ast.AST, name: str, kind: str) -> None:
                qualified = ".".join(self.stack + [name]) if self.stack else name
                result.append({
                    "file": path.relative_to(root).as_posix(),
                    "symbol": qualified,
                    "kind": kind,
                    "line_start": getattr(node, "lineno", None),
                    "line_end": getattr(node, "end_lineno", None),
                    "language": "Python",
                    "source": "python_ast",
                })

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                self._emit(node, node.name, "class")
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self._emit(node, node.name, "method" if self.stack else "function")
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self._emit(node, node.name, "async_method" if self.stack else "async_function")
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

        Visitor().visit(tree)

    return result


def ctags_symbols(root: Path) -> list[dict]:
    ctags = shutil.which("ctags")
    if not ctags:
        return []

    cmd = [
        ctags,
        "-R",
        "--output-format=json",
        "--fields=+nK",
        "--extras=-F",
        "-f",
        "-",
        ".",
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except Exception:
        return []

    if proc.returncode != 0:
        return []

    result: list[dict] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        path = item.get("path")
        name = item.get("name")
        if not path or not name:
            continue

        p = root / path
        if not p.exists() or not allowed(p, root):
            continue

        result.append({
            "file": Path(path).as_posix(),
            "symbol": str(name),
            "kind": str(item.get("kind", "symbol")),
            "line_start": item.get("line"),
            "line_end": item.get("end"),
            "language": item.get("language", ""),
            "source": "universal_ctags",
        })

    return result


def dedupe(symbols: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in symbols:
        key = (item["file"], item["symbol"], item["kind"], item.get("line_start"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return sorted(result, key=lambda x: (
        x["file"],
        x.get("line_start") or 0,
        x["symbol"],
    ))


def write_markdown(path: Path, symbols: list[dict], head: str, engine: str) -> None:
    lines = [
        "# GENERATED SYMBOL INDEX",
        "",
        f"Generated from HEAD: {head}",
        f"Engine: {engine}",
        "",
        "This file contains machine-derived structural facts only.",
        "It does not define semantic responsibility or project authority.",
        "",
        "| File | Symbol | Kind | Lines@SHA | Language |",
        "|---|---|---|---|---|",
    ]
    for item in symbols:
        start = item.get("line_start")
        end = item.get("line_end")
        if start and end:
            line_range = f"{start}-{end}"
        elif start:
            line_range = str(start)
        else:
            line_range = ""
        lines.append(
            f"| {item['file']} | {item['symbol']} | {item['kind']} | "
            f"{line_range} | {item.get('language','')} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--output", default="artifacts/SYMBOL_INDEX.generated.md")
    ap.add_argument("--json", dest="json_output", default="")
    ap.add_argument("--python-ast-only", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    head = git_head(root)

    symbols: list[dict] = []
    engine = "python_ast"

    if not args.python_ast_only:
        symbols = ctags_symbols(root)
        if symbols:
            engine = "universal_ctags"

    if not symbols:
        symbols = python_symbols(root)
        engine = "python_ast"

    symbols = dedupe(symbols)

    output = Path(args.output)
    if not output.is_absolute():
        output = root / output
    write_markdown(output, symbols, head, engine)

    if args.json_output:
        json_path = Path(args.json_output)
        if not json_path.is_absolute():
            json_path = root / json_path
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps({
                "generated_from_head": head,
                "engine": engine,
                "symbols": symbols,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"HEAD={head}")
    print(f"ENGINE={engine}")
    print(f"SYMBOLS={len(symbols)}")
    print(f"OUTPUT={output}")
    if engine == "python_ast":
        print(
            "NOTE=Universal Ctags unavailable or disabled; only Python symbols "
            "are generated by the stdlib fallback."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

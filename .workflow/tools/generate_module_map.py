#!/usr/bin/env python3
"""Generate machine-derived module/file facts.

This intentionally does not guess module responsibility. It produces facts that
an agent can merge into MODULE_MAP.md with semantic ownership notes.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

EXCLUDED = {
    ".git", ".idea", ".vscode", ".venv", "venv", "node_modules",
    "dist", "build", "coverage", "vendor", "__pycache__",
    ".runtime", ".evidence", ".local-acceptance", ".pytest_cache",
}

SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".cs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs", ".go",
    ".swift", ".m", ".mm", ".php", ".rb", ".scala", ".sh", ".ps1", ".bat",
    ".cmd", ".sql", ".proto", ".graphql", ".gql", ".xml", ".gradle",
}


def head(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "UNKNOWN"


def language(path: Path) -> str:
    mapping = {
        ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript/React",
        ".js": "JavaScript", ".jsx": "JavaScript/React", ".java": "Java",
        ".kt": "Kotlin", ".kts": "Kotlin", ".cs": "C#", ".rs": "Rust",
        ".go": "Go", ".swift": "Swift", ".sql": "SQL", ".ps1": "PowerShell",
        ".sh": "Shell",
    }
    return mapping.get(path.suffix.lower(), path.suffix.lower().lstrip(".") or "text")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--output", default="artifacts/MODULE_MAP.generated.md")
    ap.add_argument("--json", dest="json_output", default="")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    repo_head = head(root)
    rows = []

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTS:
            continue
        rel = path.relative_to(root)
        if any(part in EXCLUDED for part in rel.parts):
            continue
        try:
            lines = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        except Exception:
            lines = 0
        rows.append({
            "file": rel.as_posix(),
            "language": language(path),
            "lines": lines,
            "directory": rel.parent.as_posix(),
        })

    out = Path(args.output)
    if not out.is_absolute():
        out = root / out
    out.parent.mkdir(parents=True, exist_ok=True)

    md = [
        "# GENERATED MODULE MAP",
        "",
        f"Generated from HEAD: {repo_head}",
        "",
        "Machine-derived file facts only. Responsibility/authority remain semantic.",
        "",
        "| File | Language | Lines | Directory |",
        "|---|---|---:|---|",
    ]
    for row in rows:
        md.append(
            f"| {row['file']} | {row['language']} | {row['lines']} | "
            f"{row['directory']} |"
        )
    out.write_text("\n".join(md) + "\n", encoding="utf-8")

    if args.json_output:
        jp = Path(args.json_output)
        if not jp.is_absolute():
            jp = root / jp
        jp.parent.mkdir(parents=True, exist_ok=True)
        jp.write_text(
            json.dumps({
                "generated_from_head": repo_head,
                "modules": rows,
            }, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"HEAD={repo_head}")
    print(f"MODULE_FILES={len(rows)}")
    print(f"OUTPUT={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

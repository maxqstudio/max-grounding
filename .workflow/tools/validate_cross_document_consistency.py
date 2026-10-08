#!/usr/bin/env python3
"""Validate consistency across all project Markdown documents.

Machine-verifiable checks:
- broken local references and path::symbol references
- stable TRUTH claim registration, backlinks, and conflicting claim rows
- selected authority mismatches across core documents
- explicit STALE markers
- documentation freshness against a declared Git base SHA

Semantic truth still requires source/test/runtime audit.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from project_profile import (
    CONTRACT_DOCS,
    PROFILE_FILE,
    contract_settings,
    documentation_settings,
    normalized_profile,
    parse_profile,
    required_docs,
    sequence_settings,
    validate_profile,
)

EXCLUDED = {
    ".git", ".workflow", ".idea", ".vscode", ".venv", "venv", "node_modules",
    "dist", "build", "coverage", "vendor", "__pycache__",
    ".runtime", ".evidence", ".local-acceptance", ".pytest_cache",
}

SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".kts",
    ".cs", ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs", ".go",
    ".swift", ".m", ".mm", ".php", ".rb", ".scala", ".sh", ".ps1", ".bat",
    ".cmd", ".sql", ".proto", ".graphql", ".gql", ".xml", ".gradle",
}

PATH_EXTS = SOURCE_EXTS | {".md", ".json", ".yaml", ".yml", ".toml", ".ini"}

LOCAL_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(chr(96) + r"([^" + chr(96) + r"\n]+)" + chr(96))
PATH_SYMBOL_RE = re.compile(
    r"(?P<path>[A-Za-z0-9_.\-/\\]+\.[A-Za-z0-9_]+)::"
    r"(?P<symbol>[A-Za-z_][A-Za-z0-9_.$<>:-]*)"
)
CLAIM_ID_RE = re.compile(r"\bTRUTH-[A-Z0-9_-]+\b")
STATUS_RE = re.compile(r"\b(PASS|FAIL|NOT_PROVEN|NOT_APPLICABLE|BLOCKED)\b")
STALE_RE = re.compile(r"^\s*Status\s*:\s*STALE\s*$", re.I | re.M)


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args],
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def git_root(start: Path) -> Path:
    return Path(git(start, "rev-parse", "--show-toplevel"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def source_has_symbol(path: Path, symbol: str) -> bool:
    """Resolve a declared path::symbol reference without guessing across files."""
    content = read(path)
    if symbol in content:
        return True
    if path.suffix.lower() != ".py":
        return False
    parts = symbol.split(".")
    if not parts or any(not part.isidentifier() for part in parts):
        return False
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False

    nodes = list(tree.body)
    for index, part in enumerate(parts):
        match = next(
            (
                node
                for node in nodes
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == part
            ),
            None,
        )
        if match is None:
            return False
        if index == len(parts) - 1:
            return True
        if not isinstance(match, ast.ClassDef):
            return False
        nodes = list(match.body)
    return False


def strip_fences(text: str) -> str:
    out: list[str] = []
    inside = False
    marker = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        if not inside and (stripped.startswith("```") or stripped.startswith("~~~")):
            inside = True
            marker = stripped[:3]
            continue
        if inside and stripped.startswith(marker):
            inside = False
            marker = ""
            continue
        if not inside:
            out.append(line)
    return "\n".join(out)


def all_docs(root: Path) -> list[Path]:
    result: list[Path] = []
    for path in root.rglob("*.md"):
        if any(part in EXCLUDED for part in path.relative_to(root).parts):
            continue
        result.append(path)
    return sorted(result)


def parse_table(text: str, heading: str) -> list[list[str]]:
    pos = text.find(heading)
    if pos < 0:
        return []
    chunk = text[pos:].split("\n## ", 1)[0]
    rows: list[list[str]] = []
    for line in chunk.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if cols and not all(set(c) <= {"-", ":"} for c in cols):
            rows.append(cols)
    return rows


def scalar_fields(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z0-9 /_-]{1,80})\s*:\s*(.*?)\s*$", line)
        if not m:
            continue
        key = re.sub(r"\s+", " ", m.group(1).strip().lower())
        value = m.group(2).strip().strip(chr(96))
        if value:
            result[key] = value
    return result


def normalize_repo(value: str) -> str:
    value = value.strip().rstrip("/")
    value = re.sub(r"^https?://github\.com/", "", value, flags=re.I)
    value = re.sub(r"\.git$", "", value, flags=re.I)
    return value.lower()


def normalize_branch(value: str) -> str:
    return value.strip().strip(chr(96)).lower()


def normalize_sha(value: str) -> str:
    m = re.search(r"\b[0-9a-fA-F]{7,40}\b", value)
    return m.group(0).lower() if m else value.strip().lower()


def likely_path(token: str) -> bool:
    token = token.strip().strip("<>").split("#", 1)[0].split("?", 1)[0]
    if not token or " " in token:
        return False
    if token.startswith(("http://", "https://", "mailto:", "#", "app://")):
        return False
    normalized = token.replace("\\", "/")
    return Path(normalized).suffix.lower() in PATH_EXTS


def resolve_ref(root: Path, doc: Path, target: str) -> Path | None:
    target = target.strip().strip("<>")
    if target.startswith(("http://", "https://", "mailto:", "#", "app://")):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None
    p = Path(target.replace("\\", "/"))
    for candidate in (root / p, doc.parent / p):
        if candidate.exists():
            return candidate
    return Path("__MISSING__")


def truth_claims(
    root: Path, docs_root: Path
) -> tuple[dict[str, dict[str, str]], list[str]]:
    failures: list[str] = []
    ledger = docs_root / "PROJECT_TRUTH_SYNC.md"
    if not ledger.is_file():
        return {}, ["MISSING_TRUTH_LEDGER"]
    rows = parse_table(read(ledger), "## Critical claim traceability")
    claims: dict[str, dict[str, str]] = {}
    for row in rows[1:]:
        if len(row) < 7:
            continue
        claim_id = row[0].strip()
        if not claim_id or claim_id == "Claim ID":
            continue
        if not CLAIM_ID_RE.fullmatch(claim_id):
            failures.append(f"INVALID_CLAIM_ID:{claim_id}")
            continue
        payload = {
            "claim": row[1].strip(),
            "documents": row[2].strip(),
            "sources": row[3].strip(),
            "tests": row[4].strip(),
            "runtime": row[5].strip(),
            "status": row[6].strip(),
        }
        if claim_id in claims and claims[claim_id] != payload:
            failures.append(f"DUPLICATE_CANONICAL_CLAIM_CONFLICT:{claim_id}")
        claims[claim_id] = payload
    return claims, failures


def claim_rows(text: str) -> list[tuple[str, str, str]]:
    """Return observed claim rows while excluding the dedicated relation table."""
    result: list[tuple[str, str, str]] = []
    section = ""
    for line in strip_fences(text).splitlines():
        line = line.strip()
        if line.startswith("## "):
            section = line[3:].strip().casefold()
            continue
        if section == "claim relations":
            continue
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if not cols or not CLAIM_ID_RE.fullmatch(cols[0]):
            continue
        claim = cols[1] if len(cols) > 1 else ""
        status = ""
        for col in reversed(cols):
            match = STATUS_RE.search(col)
            if match:
                status = match.group(1)
                break
        result.append((cols[0], claim, status))
    return result


def split_refs(value: str) -> list[str]:
    return [item.strip().strip(chr(96)) for item in value.split(";") if item.strip()]


def resolve_declared_doc_ref(root: Path, docs_root: Path, ref: str) -> Path:
    p = Path(ref.replace("\\", "/"))
    if p.is_absolute():
        return p
    if p.parts and p.parts[0] == "docs":
        return root / p
    if len(p.parts) == 1 and p.suffix.lower() == ".md":
        return docs_root / p
    return root / p


def diff_changes(root: Path, base: str) -> list[tuple[str, str]]:
    raw = git(root, "diff", "--name-status", base + "...HEAD")
    result: list[tuple[str, str]] = []
    for line in raw.splitlines() if raw else []:
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            result.extend([("D", parts[1]), ("A", parts[2])])
        elif len(parts) >= 2:
            result.append((status[:1], parts[1]))
    return result


def is_source(path: str) -> bool:
    p = Path(path)
    if any(part in EXCLUDED for part in p.parts):
        return False
    return p.suffix.lower() in SOURCE_EXTS or p.name.lower() in {"dockerfile", "makefile"}


def required_docs_for_diff(
    changes: list[tuple[str, str]],
    profile_required: set[str],
    present_docs: set[str],
    settings: dict[str, str],
    sequence_required: bool,
) -> set[str]:
    required: set[str] = set()
    source_paths = [path for _, path in changes if is_source(path)]
    if not source_paths:
        return required

    for name in ("CURRENT_STATE.md", "TEST_ACCEPTANCE_MATRIX.md", "PROJECT_TRUTH_SYNC.md", "SYMBOL_INDEX.md"):
        if name in profile_required or name in present_docs:
            required.add(name)

    if any(status in {"A", "D"} and is_source(path) for status, path in changes):
        if "MODULE_MAP.md" in profile_required or "MODULE_MAP.md" in present_docs:
            required.add("MODULE_MAP.md")
        if "SYSTEM_OVERVIEW.md" in profile_required or "SYSTEM_OVERVIEW.md" in present_docs:
            required.add("SYSTEM_OVERVIEW.md")

    paths = [p.lower().replace("\\", "/") for p in source_paths]

    sequence_sensitive = any(
        any(k in p for k in (
            "/api/", "route", "router", "controller", "endpoint",
            "/ui/", "/frontend/", "/screens/", "/pages/", "/components/",
            "workflow", "lifecycle", "state_machine", "state-machine",
            "promotion", "service", "store", "repository",
        ))
        for p in paths
    )
    if sequence_required and (
        sequence_sensitive
        or any(status in {"A", "D"} and is_source(path) for status, path in changes)
    ):
        required.add("SEQUENCE_CONTRACTS.md")

    if any(any(k in p for k in ("/api/", "route", "router", "controller", "endpoint")) for p in paths):
        if "SYSTEM_OVERVIEW.md" in profile_required or "SYSTEM_OVERVIEW.md" in present_docs:
            required.add("SYSTEM_OVERVIEW.md")
        if settings.get("api_contracts") == "required" or "API_CONTRACTS.md" in present_docs:
            required.add("API_CONTRACTS.md")
        if "FLOW_INDEX.md" in profile_required or "FLOW_INDEX.md" in present_docs:
            required.add("FLOW_INDEX.md")

    if any(any(k in p for k in ("/ui/", "/frontend/", "/screens/", "/pages/", "/components/")) for p in paths):
        if "SYSTEM_OVERVIEW.md" in profile_required or "SYSTEM_OVERVIEW.md" in present_docs:
            required.add("SYSTEM_OVERVIEW.md")
        if settings.get("ui_information_architecture") == "required" or "UI_INFORMATION_ARCHITECTURE.md" in present_docs:
            required.add("UI_INFORMATION_ARCHITECTURE.md")

    if any(any(k in p for k in ("migration", "schema", "/db/", "/database/", "/models/", "/data/")) for p in paths):
        if "SYSTEM_OVERVIEW.md" in profile_required or "SYSTEM_OVERVIEW.md" in present_docs:
            required.add("SYSTEM_OVERVIEW.md")
        if settings.get("data_contracts") == "required" or "DATA_CONTRACTS.md" in present_docs:
            required.add("DATA_CONTRACTS.md")

    if any(any(k in p for k in ("workflow", "lifecycle", "state_machine", "state-machine", "promotion")) for p in paths):
        if "SYSTEM_OVERVIEW.md" in profile_required or "SYSTEM_OVERVIEW.md" in present_docs:
            required.add("SYSTEM_OVERVIEW.md")
        for name in ("WORKFLOW_STATE_MACHINE.md", "FLOW_INDEX.md"):
            if name in profile_required or name in present_docs:
                required.add(name)

    if any(any(k in p for k in ("deploy", "docker", "run_", "start_", "build.", "/scripts/")) for p in paths):
        if settings.get("runbook") == "required" or "RUNBOOK.md" in present_docs:
            required.add("RUNBOOK.md")

    return required


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default="")
    ap.add_argument("--require-base", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--allow-dirty", action="store_true")
    args = ap.parse_args()

    try:
        root = git_root(Path(args.root).resolve())
        head = git(root, "rev-parse", "HEAD")
        dirty = bool(git(root, "status", "--porcelain"))
    except Exception as exc:
        print("FAIL GIT_ERROR " + str(exc))
        return 1

    failures: list[str] = []
    warnings: list[str] = []
    refs_checked = 0
    rows_checked = 0

    if dirty and not args.allow_dirty:
        failures.append("WORKTREE_NOT_CLEAN")

    profile_path = root / PROFILE_FILE
    if not profile_path.is_file():
        failures.append("MISSING_PROJECT_PROFILE")
        profile_data = {"profile": "standard", "contracts": {}}
    else:
        try:
            profile_data = parse_profile(profile_path)
            failures.extend(validate_profile(profile_data))
        except Exception as exc:
            failures.append("PROJECT_PROFILE_INVALID:" + str(exc))
            profile_data = {"profile": "standard", "contracts": {}}

    try:
        profile_name = normalized_profile(profile_data)
        profile_required = required_docs(profile_data)
        settings = contract_settings(profile_data)
        doc_settings = documentation_settings(profile_data)
        seq_settings = sequence_settings(profile_data)
    except Exception as exc:
        failures.append("PROJECT_PROFILE_RESOLUTION_FAILED:" + str(exc))
        profile_name = "standard"
        profile_required = set()
        settings = {}
        doc_settings = {
            "generated": False,
            "spec_root": ".workflow",
            "docs_root": "docs",
        }
        seq_settings = {"required": False, "runtime_trace_required": False}

    docs = all_docs(root)
    docs_root = root / str(doc_settings.get("docs_root", "docs"))
    canonical_docs = {
        path.name: path
        for path in docs
        if path.parent == docs_root
    }
    present_docs = set(canonical_docs)

    canonical_names = {
        "SYSTEM_OVERVIEW.md",
        "PROJECT_MANIFEST.md",
        "CURRENT_STATE.md",
        "SOURCE_AUTHORITY_MAP.md",
        "ARCHITECTURE.md",
        "WORKFLOW_STATE_MACHINE.md",
        "SEQUENCE_CONTRACTS.md",
        "MODULE_MAP.md",
        "SYMBOL_INDEX.md",
        "FLOW_INDEX.md",
        "TEST_ACCEPTANCE_MATRIX.md",
        "DOC_SYNC_MATRIX.md",
        "PROJECT_TRUTH_SYNC.md",
        "API_CONTRACTS.md",
        "DATA_CONTRACTS.md",
        "UI_INFORMATION_ARCHITECTURE.md",
        "RUNBOOK.md",
        "DECISIONS.md",
        "KNOWN_DEFECTS.md",
        "GLOSSARY.md",
        "CHANGELOG.md",
    }

    if doc_settings.get("generated", False):
        for name in sorted(canonical_names):
            if (root / name).is_file():
                failures.append(
                    "DUPLICATE_CANONICAL_DOC_AT_REPO_ROOT:" + name
                )
        for name, path in canonical_docs.items():
            if name in canonical_names:
                if "GENERATED BY PROJECT TRUTH COMPILER - DO NOT EDIT" not in read(path):
                    failures.append("GENERATED_DOC_MARKER_MISSING:" + name)

    for key, value in settings.items():
        contract_doc = CONTRACT_DOCS[key]
        if value == "not_applicable" and (docs_root / contract_doc).is_file():
            failures.append("NOT_APPLICABLE_DOC_PRESENT:" + contract_doc)

    for required in sorted(profile_required):
        if required == PROFILE_FILE:
            if not profile_path.is_file():
                failures.append("MISSING_CORE_DOC:" + required)
            continue
        if not (docs_root / required).is_file():
            failures.append("MISSING_CORE_DOC:docs/" + required)

    truth_required = "PROJECT_TRUTH_SYNC.md" in profile_required
    truth_path = docs_root / "PROJECT_TRUTH_SYNC.md"
    if truth_required or truth_path.is_file():
        canonical, ledger_failures = truth_claims(root, docs_root)
        failures.extend(ledger_failures)
    else:
        canonical, ledger_failures = {}, []
    observed: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    relation_rows = (
        parse_table(read(truth_path), "## Claim relations")
        if truth_path.is_file()
        else []
    )
    relations_checked = 0
    for row in relation_rows[1:]:
        if len(row) < 3:
            continue
        left, relation, right = row[0].strip(), row[1].strip().upper(), row[2].strip()
        if not left or left == "Claim ID":
            continue
        relations_checked += 1
        if left not in canonical:
            failures.append("RELATION_UNKNOWN_LEFT_CLAIM:" + left)
            continue
        if right not in canonical:
            failures.append("RELATION_UNKNOWN_RIGHT_CLAIM:" + right)
            continue
        if relation not in {"CONFLICTS_WITH", "REQUIRES", "SAME_AS", "SUPERSEDES"}:
            failures.append("INVALID_CLAIM_RELATION:" + left + ":" + relation + ":" + right)
            continue
        left_status = canonical[left]["status"]
        right_status = canonical[right]["status"]
        if relation == "CONFLICTS_WITH" and left_status == "PASS" and right_status == "PASS":
            failures.append("CONFLICTING_CLAIMS_BOTH_PASS:" + left + ":" + right)
        elif relation == "REQUIRES" and left_status == "PASS" and right_status != "PASS":
            failures.append("CLAIM_REQUIREMENT_UNSATISFIED:" + left + ":" + right + ":" + right_status)
        elif relation == "SAME_AS" and left_status in {"PASS", "FAIL"} and right_status in {"PASS", "FAIL"} and left_status != right_status:
            failures.append("SAME_AS_STATUS_CONFLICT:" + left + ":" + right)
        elif relation == "SUPERSEDES" and left_status == "PASS" and right_status == "PASS":
            failures.append("SUPERSEDED_CLAIM_STILL_PASS:" + left + ":" + right)

    for doc in docs:
        text = read(doc)
        relative = doc.relative_to(root).as_posix()

        if STALE_RE.search(text):
            failures.append("EXPLICIT_STALE_DOCUMENT:" + relative)

        unfenced = strip_fences(text)

        for target in LOCAL_LINK_RE.findall(unfenced):
            resolved = resolve_ref(root, doc, target)
            if resolved is None:
                continue
            refs_checked += 1
            if resolved.name == "__MISSING__":
                failures.append("BROKEN_MARKDOWN_LINK:" + relative + ":" + target)

        for token in INLINE_CODE_RE.findall(unfenced):
            if not likely_path(token):
                continue
            resolved = resolve_ref(root, doc, token)
            if resolved is None:
                continue
            refs_checked += 1
            if resolved.name == "__MISSING__":
                warnings.append("UNRESOLVED_INLINE_PATH:" + relative + ":" + token)

        for match in PATH_SYMBOL_RE.finditer(unfenced):
            path_ref = match.group("path").replace("\\", "/")
            symbol = match.group("symbol")
            refs_checked += 1
            source = root / path_ref
            if not source.is_file():
                failures.append("BROKEN_PATH_SYMBOL_FILE:" + relative + ":" + path_ref + "::" + symbol)
            elif not source_has_symbol(source, symbol):
                failures.append("UNRESOLVED_PATH_SYMBOL:" + relative + ":" + path_ref + "::" + symbol)

        ids = set(CLAIM_ID_RE.findall(unfenced))
        if doc.name not in {"PROJECT_TRUTH_SYNC.md", "README.md", "SKILL.md"}:
            for claim_id in sorted(ids):
                if claim_id not in canonical:
                    failures.append("UNKNOWN_CLAIM_ID:" + relative + ":" + claim_id)

        for row in claim_rows(text):
            observed[row[0]].append((relative, row[1], row[2]))
            rows_checked += 1

    for claim_id, payload in canonical.items():
        for doc_ref in split_refs(payload["documents"]):
            if doc_ref in {"NOT_APPLICABLE", "NOT_PROVEN"}:
                continue
            target = resolve_declared_doc_ref(root, docs_root, doc_ref)
            if not target.is_file():
                failures.append("CLAIM_DECLARED_DOC_MISSING:" + claim_id + ":" + doc_ref)
                continue
            if claim_id not in CLAIM_ID_RE.findall(strip_fences(read(target))):
                failures.append("CLAIM_BACKLINK_MISSING:" + claim_id + ":" + doc_ref)

    for claim_id, rows in observed.items():
        if claim_id not in canonical:
            continue
        canonical_text = re.sub(r"\s+", " ", canonical[claim_id]["claim"]).strip().lower()
        texts: set[str] = set()
        statuses: set[str] = set()
        for doc_ref, claim_text, status in rows:
            normalized = re.sub(r"\s+", " ", claim_text).strip().lower()
            if normalized:
                texts.add(normalized)
                if canonical_text and normalized != canonical_text:
                    failures.append("CLAIM_TEXT_CONFLICT:" + claim_id + ":" + doc_ref)
            if status:
                statuses.add(status)
        if len(texts) > 1:
            failures.append("MULTI_DOCUMENT_CLAIM_TEXT_CONFLICT:" + claim_id)
        if "PASS" in statuses and statuses.intersection({"FAIL", "NOT_PROVEN", "BLOCKED"}):
            failures.append("CLAIM_STATUS_CONFLICT:" + claim_id + ":" + ",".join(sorted(statuses)))

    manifest = docs_root / "PROJECT_MANIFEST.md"
    current = docs_root / "CURRENT_STATE.md"
    if manifest.is_file():
        mf_only = scalar_fields(read(manifest))
        manifest_profile = mf_only.get("governance profile")
        if not manifest_profile:
            failures.append("PROJECT_MANIFEST_GOVERNANCE_PROFILE_MISSING")
        elif manifest_profile.strip().lower() != profile_name:
            failures.append(
                "PROJECT_MANIFEST_PROFILE_CONFLICT:"
                + manifest_profile
                + "!="
                + profile_name
            )

    if current.is_file():
        cf_only = scalar_fields(read(current))
        current_profile = cf_only.get("governance profile")
        if not current_profile:
            failures.append("CURRENT_STATE_GOVERNANCE_PROFILE_MISSING")
        elif current_profile.strip().lower() != profile_name:
            failures.append(
                "CURRENT_STATE_PROFILE_CONFLICT:"
                + current_profile
                + "!="
                + profile_name
            )

    if manifest.is_file() and current.is_file():
        mf = scalar_fields(read(manifest))
        cf = scalar_fields(read(current))
        comparisons = [
            ("repository", mf.get("repository"), cf.get("repository"), normalize_repo),
            ("branch", mf.get("active branch"), cf.get("branch"), normalize_branch),
            ("authoritative_sha", mf.get("current authoritative sha"), cf.get("authoritative sha"), normalize_sha),
        ]
        for label, left, right, normalizer in comparisons:
            if left and right and normalizer(left) != normalizer(right):
                failures.append("CORE_AUTHORITY_CONFLICT:" + label + ":" + left + "!=" + right)

    base = args.base.strip()
    if not base:
        if args.require_base:
            failures.append("BASE_SHA_REQUIRED_FOR_STALENESS_CHECK")
        else:
            try:
                base = git(root, "rev-parse", "HEAD^")
                warnings.append("BASE_SHA_INFERRED:" + base)
            except Exception:
                warnings.append("STALENESS_CHECK_SKIPPED_NO_BASE")

    changes: list[tuple[str, str]] = []
    changed_docs: set[str] = set()
    required_due_to_diff: set[str] = set()

    if base:
        try:
            git(root, "rev-parse", "--verify", base + "^{commit}")
            changes = diff_changes(root, base)
            changed_docs = {
                Path(path).name for _, path in changes if Path(path).suffix.lower() == ".md"
            }
            required_due_to_diff = required_docs_for_diff(
                changes,
                profile_required=profile_required,
                present_docs=present_docs,
                settings=settings,
                sequence_required=seq_settings.get("required", False),
            )
            if not doc_settings.get("generated", False):
                for required in sorted(required_due_to_diff):
                    if required not in changed_docs:
                        failures.append("STALE_DOC_NOT_UPDATED:" + required + ":base=" + base)
            elif required_due_to_diff:
                warnings.append(
                    "GENERATED_DOC_DIFF_FRESHNESS_DELEGATED_TO_PROJECT_DOCS_COMPILER:"
                    + ",".join(sorted(required_due_to_diff))
                )
        except subprocess.CalledProcessError:
            failures.append("INVALID_BASE_SHA:" + base)

    report = {
        "repo_sha": head,
        "governance_profile": profile_name,
        "generated_documentation": bool(doc_settings.get("generated", False)),
        "base_sha": base or None,
        "worktree_clean": not dirty,
        "docs_scanned": len(docs),
        "references_checked": refs_checked,
        "claim_rows_checked": rows_checked,
        "canonical_claims": len(canonical),
        "claim_relations_checked": relations_checked,
        "changed_files": len(changes),
        "changed_docs": sorted(changed_docs),
        "required_docs_from_diff": sorted(required_due_to_diff),
        "failures": failures,
        "warnings": warnings,
        "machine_scope": (
            "Cross-document references across all Markdown, docs/-canonical project "
            "contracts, stable claims, selected authority fields, explicit stale "
            "markers, and git-diff freshness for manual-doc mode. Generated-doc "
            "freshness is delegated to the deterministic Project Truth Compiler. Semantic truth "
            "still requires source/test/runtime audit."
        ),
        "result": "FAIL" if failures else "PASS",
    }

    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)

    if args.report:
        output = Path(args.report)
        if not output.is_absolute():
            output = root / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

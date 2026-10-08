#!/usr/bin/env python3
"""Deterministic tracked-document inventory and freshness validation.

SW2-20 discovers documentation from the Git index rather than a complete-file
allowlist. Every discovered documentation surface receives exactly one role.
Source-authored documentation is then checked for a bounded set of dynamic
claims whose authority already exists elsewhere in the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Iterable

REPORT_PATH = ".workflow/generated/documentation_coverage.json"
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".adoc", ".mmd"}
SOURCE_AUTHORED_ROLES = {
    "root_public_entry",
    "root_agent_contract",
    "root_skill_contract",
    "root_community_policy",
    "root_source_document",
    "skill_reference",
    "docs_entry",
    "handbook_source",
    "docs_source_document",
}
HISTORICAL_HINTS = (
    "historical",
    "previous",
    "formerly",
    "legacy",
    "past release",
    "example",
    "fixture",
)
SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
STABLE_RELEASE_RE = re.compile(
    r"(?i)(?:stable[ -]release|accepted stable|current stable)[^\n]{0,120}?`?(v\d+\.\d+\.\d+)`?"
)
CURRENT_PHASE_RE = re.compile(r"(?i)\bcurrent(?:ly)?\b[^\n]{0,120}\b(SW2-\d+)\b")
LICENSE_NEGATIVE_RE = re.compile(
    r"(?i)(?:(?:this\s+repository\s+)?does\s+not\s+currently\s+declare\s+an?\s+owner-approved\s+(?:public\s+)?license|"
    r"no\s+(?:owner-approved\s+)?(?:public\s+)?license|"
    r"(?:public\s+)?license\s+(?:has\s+)?not\s+(?:yet\s+)?been\s+(?:selected|approved)|"
    r"license\s+choice\s+(?:is\s+)?(?:pending|not\s+approved))"
)
PRE_RELEASE_RE = re.compile(
    r"(?i)(?:until\s+(?:a\s+)?(?:versioned\s+)?stable\s+release\s+exists|"
    r"no\s+(?:versioned\s+)?stable\s+release\s+exists|"
    r"stable[^\n]{0,60}publication[^\n]{0,60}(?:remains\s+)?(?:blocked|pending|not\s+yet))"
)
ENFORCEMENT_POSITIVE_RE = re.compile(
    r"(?i)(?:default\s+branch|\bmain\b)[^\n]{0,100}"
    r"(?:governed\s+against|protected\s+(?:from|against)|blocks?|enforces?)"
    r"[^\n]{0,100}(?:deletion|non-fast-forward|required\s+(?:status\s+)?checks?)"
)
ENFORCEMENT_DENIAL_RE = re.compile(
    r"(?i)(?:not\s+automatically|does\s+not\s+automatically|without\s+automatic|"
    r"no\s+(?:github\s+)?ruleset|not\s+enforced|non-blocking)"
)


PROVENANCE_HINT_ONLY_RE = re.compile(
    r"(?i)\bproducer\s+repository\s+and\s+(?:commit|sha)\s+fields?\s+are\s+(?:only\s+)?provenance\s+hints\b"
)


def _git(root: Path, *args: str, binary: bool = False):
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    if binary:
        return completed.stdout
    return completed.stdout.decode("utf-8", errors="strict").strip()


def tracked_paths(root: Path) -> tuple[str, ...]:
    payload = _git(root, "ls-files", "-z", binary=True)
    return tuple(
        sorted(
            item.decode("utf-8", errors="strict").replace("\\", "/")
            for item in payload.split(b"\0")
            if item
        )
    )


def is_documentation_candidate(path: str) -> bool:
    if path == "LICENSE":
        return True
    return Path(path).suffix.lower() in DOC_SUFFIXES


def classify_document(path: str) -> str | None:
    """Return exactly one path-derived role, or None for unsupported surfaces."""
    suffix = Path(path).suffix.lower()
    if path == "LICENSE":
        return "root_legal"
    if path == "README.md":
        return "root_public_entry"
    if path == "AGENTS.md":
        return "root_agent_contract"
    if path == "SKILL.md":
        return "root_skill_contract"
    if path in {"CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md"}:
        return "root_community_policy"
    if "/" not in path and suffix in DOC_SUFFIXES:
        return "root_source_document"
    if path.startswith("references/") and suffix in DOC_SUFFIXES:
        return "skill_reference"
    if path.startswith("templates/") and suffix in DOC_SUFFIXES:
        return "template_documentation"
    if path == "docs/README.md":
        return "docs_entry"
    if path.startswith("docs/handbook/") and suffix in DOC_SUFFIXES:
        return "handbook_source"
    if path.startswith("docs/sequence/views/") and suffix == ".md":
        return "sequence_human_view"
    if path.startswith("docs/sequence/generated/") and suffix == ".mmd":
        return "sequence_generated_diagram"
    if path.startswith("docs/") and path.count("/") == 1 and suffix == ".md":
        return "project_truth_projection"
    if path.startswith("docs/") and suffix in DOC_SUFFIXES:
        return "docs_source_document"
    return None


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def latest_stable_tag(root: Path) -> str:
    try:
        raw = _git(root, "tag", "--list")
    except Exception:
        return ""
    candidates: list[tuple[tuple[int, int, int], str]] = []
    for line in str(raw).splitlines():
        tag = line.strip()
        match = SEMVER_RE.fullmatch(tag)
        if match:
            candidates.append((tuple(int(part) for part in match.groups()), tag))
    return max(candidates)[1] if candidates else ""


def current_phase(root: Path) -> str:
    roadmap = _load_json(root / ".workflow" / "roadmap.json")
    return str(roadmap.get("current_phase", "")).strip()


def license_present(root: Path) -> bool:
    return (root / "LICENSE").is_file()


def no_ruleset_boundary(root: Path) -> bool:
    claims = _load_json(root / ".workflow" / "claims.json").get("claims", [])
    if not isinstance(claims, list):
        return False
    for item in claims:
        if not isinstance(item, dict) or item.get("id") != "TRUTH-SW2-MERGE-ENFORCEMENT":
            continue
        claim = str(item.get("claim", "")).lower()
        return item.get("status") == "PASS" and "no github ruleset" in claim
    return False


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _historical_context(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in HISTORICAL_HINTS)


def _finding(path: str, text: str, match: re.Match[str], kind: str, observed: str, expected: str) -> dict:
    start = max(0, text.rfind("\n", 0, match.start()) + 1)
    end = text.find("\n", match.end())
    if end < 0:
        end = len(text)
    excerpt = text[start:end].strip()
    return {
        "kind": kind,
        "path": path,
        "line": _line_number(text, match.start()),
        "observed": observed,
        "expected": expected,
        "excerpt": excerpt,
    }


def freshness_findings(root: Path, path: str, role: str, authority: dict[str, object]) -> list[dict]:
    if role not in SOURCE_AUTHORED_ROLES:
        return []
    target = root / path
    try:
        text = target.read_text(encoding="utf-8", errors="strict")
    except Exception as exc:
        return [{
            "kind": "DOCUMENT_READ_FAILURE",
            "path": path,
            "line": 0,
            "observed": str(exc),
            "expected": "UTF-8 readable source-authored documentation",
            "excerpt": "",
        }]

    findings: list[dict] = []
    stable = str(authority.get("stable_release", ""))
    phase = str(authority.get("current_phase", ""))

    if stable:
        for match in STABLE_RELEASE_RE.finditer(text):
            observed = match.group(1)
            line = text[text.rfind("\n", 0, match.start()) + 1 : text.find("\n", match.end()) if text.find("\n", match.end()) >= 0 else len(text)]
            if observed != stable and not _historical_context(line):
                findings.append(_finding(path, text, match, "STALE_STABLE_RELEASE", observed, stable))
        for match in PRE_RELEASE_RE.finditer(text):
            line = text[text.rfind("\n", 0, match.start()) + 1 : text.find("\n", match.end()) if text.find("\n", match.end()) >= 0 else len(text)]
            if not _historical_context(line):
                findings.append(_finding(path, text, match, "STALE_PRE_RELEASE_STATE", match.group(0), "stable release exists: " + stable))

    if phase:
        for match in CURRENT_PHASE_RE.finditer(text):
            observed = match.group(1)
            line = text[text.rfind("\n", 0, match.start()) + 1 : text.find("\n", match.end()) if text.find("\n", match.end()) >= 0 else len(text)]
            if observed != phase and not _historical_context(line):
                findings.append(_finding(path, text, match, "STALE_CURRENT_PHASE", observed, phase))

    if bool(authority.get("license_present")):
        for match in LICENSE_NEGATIVE_RE.finditer(text):
            findings.append(_finding(path, text, match, "STALE_LICENSE_STATE", match.group(0), "repository license is present"))

    if bool(authority.get("no_ruleset_boundary")):
        for match in ENFORCEMENT_POSITIVE_RE.finditer(text):
            start = max(0, text.rfind("\n", 0, match.start()) + 1)
            end = text.find("\n", match.end())
            if end < 0:
                end = len(text)
            line = text[start:end]
            if not ENFORCEMENT_DENIAL_RE.search(line):
                findings.append(_finding(path, text, match, "STALE_MERGE_ENFORCEMENT", match.group(0), "no automatic GitHub ruleset enforcement is claimed"))

    for match in PROVENANCE_HINT_ONLY_RE.finditer(text):
        findings.append(_finding(path, text, match, "STALE_TOOLCHAIN_PROVENANCE",
            match.group(0), "Exact source-content digest is mandatory; Git provenance is binding only when verifiable, and missing Git fields are NOT_PROVEN"))

    return findings


def build_report(root: Path) -> dict:
    paths = tracked_paths(root)
    candidates = [path for path in paths if is_documentation_candidate(path)]
    items: list[dict] = []
    failures: list[dict] = []
    role_counts: dict[str, int] = {}
    authority: dict[str, object] = {
        "current_phase": current_phase(root),
        "stable_release": latest_stable_tag(root),
        "license_present": license_present(root),
        "no_ruleset_boundary": no_ruleset_boundary(root),
    }

    for path in candidates:
        role = classify_document(path)
        if role is None:
            failures.append({
                "kind": "UNCLASSIFIED_DOCUMENTATION",
                "path": path,
                "line": 0,
                "observed": "unclassified",
                "expected": "exactly one deterministic documentation role",
                "excerpt": "",
            })
            items.append({"path": path, "role": "UNCLASSIFIED", "freshness_mode": "FAIL"})
            continue
        role_counts[role] = role_counts.get(role, 0) + 1
        mode = "authority-bound" if role in SOURCE_AUTHORED_ROLES else "generated-or-static"
        items.append({"path": path, "role": role, "freshness_mode": mode})
        failures.extend(freshness_findings(root, path, role, authority))

    inventory_payload = json.dumps(items, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return {
        "schema_version": 1,
        "authority": authority,
        "documentation_candidates": len(candidates),
        "inventory_digest": hashlib.sha256(inventory_payload).hexdigest(),
        "role_counts": dict(sorted(role_counts.items())),
        "items": items,
        "freshness_failures": failures,
        "result": "PASS" if not failures else "FAIL",
    }


def report_text(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--report", default=REPORT_PATH)
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    report = build_report(root)
    expected = report_text(report)
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = root / report_path

    if args.write_report:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if not report_path.is_file() or report_path.read_text(encoding="utf-8") != expected:
            report_path.write_text(expected, encoding="utf-8", newline="\n")
    else:
        if not report_path.is_file():
            print("FAIL DOCUMENTATION_COVERAGE_REPORT_MISSING:" + report_path.relative_to(root).as_posix())
            return 1
        actual = report_path.read_text(encoding="utf-8")
        if actual != expected:
            print("FAIL DOCUMENTATION_COVERAGE_REPORT_STALE:" + report_path.relative_to(root).as_posix())
            return 1

    print("DOCUMENTATION_CANDIDATES=" + str(report["documentation_candidates"]))
    print("DOCUMENTATION_INVENTORY_DIGEST=" + str(report["inventory_digest"]))
    print("DOCUMENTATION_ROLE_COUNTS=" + json.dumps(report["role_counts"], sort_keys=True, separators=(",", ":")))
    for failure in report["freshness_failures"]:
        print("FAIL " + str(failure["kind"]) + ":" + str(failure["path"]) + ":" + str(failure["line"]) + ":" + str(failure["observed"]) + ":expected=" + str(failure["expected"]))
    print("DOCUMENTATION_COVERAGE=" + str(report["result"]))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

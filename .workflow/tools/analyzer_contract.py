#!/usr/bin/env python3
"""Language-independent structural analyzer contract.

Analyzers return normalized machine-observable facts. Unsupported language
semantics remain NOT_PROVEN; inventory-only fallback must never fabricate
symbols, routes, calls, or sequence edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from project_snapshot import ProjectSnapshot, resolve_snapshot

DYNAMIC_BEHAVIOR_LIMITATIONS = (
    "dynamic dispatch",
    "dependency injection",
    "reflection",
    "callbacks/events",
    "framework magic not visible statically",
)

VALID_PROOF_STATUS = {
    "PROVEN_STATIC_WITH_LIMITATIONS",
    "NOT_PROVEN",
    "NOT_APPLICABLE",
}


class AnalyzerContractError(RuntimeError):
    pass


@dataclass
class AnalyzerResult:
    analyzer_id: str
    languages: tuple[str, ...]
    claimed_extensions: tuple[str, ...]
    semantic_level: str
    proof_status: str = "PROVEN_STATIC_WITH_LIMITATIONS"
    facts: dict[str, list[dict]] = field(default_factory=dict)
    sequence_nodes: list[dict] = field(default_factory=list)
    sequence_edges: list[dict] = field(default_factory=list)
    parse_failures: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    unsupported_files: list[str] = field(default_factory=list)
    dynamic_behavior_status: str = "NOT_PROVEN"

    def coverage_record(self) -> dict:
        return {
            "analyzer_id": self.analyzer_id,
            "languages": list(self.languages),
            "claimed_extensions": list(self.claimed_extensions),
            "semantic_level": self.semantic_level,
            "proof_status": self.proof_status,
            "dynamic_behavior": self.dynamic_behavior_status,
            "parse_failures": list(self.parse_failures),
            "limitations": list(self.limitations),
            "unsupported_files": list(self.unsupported_files),
        }


class StructuralAnalyzer(Protocol):
    analyzer_id: str
    claimed_extensions: frozenset[str]

    def analyze(self, root: Path, snapshot: ProjectSnapshot) -> AnalyzerResult:
        ...


def validate_result(result: AnalyzerResult) -> AnalyzerResult:
    analyzer_id = result.analyzer_id.strip()
    if not analyzer_id:
        raise AnalyzerContractError("ANALYZER_ID_REQUIRED")
    if result.proof_status not in VALID_PROOF_STATUS:
        raise AnalyzerContractError(
            f"ANALYZER_PROOF_STATUS_INVALID:{analyzer_id}:{result.proof_status}"
        )
    if result.dynamic_behavior_status != "NOT_PROVEN":
        raise AnalyzerContractError(
            f"ANALYZER_DYNAMIC_BEHAVIOR_MUST_REMAIN_NOT_PROVEN:{analyzer_id}"
        )
    for extension in result.claimed_extensions:
        if not extension.startswith(".") or extension != extension.lower():
            raise AnalyzerContractError(
                f"ANALYZER_EXTENSION_INVALID:{analyzer_id}:{extension}"
            )
    if result.semantic_level == "inventory_only":
        semantic_facts = any(bool(values) for values in result.facts.values())
        if semantic_facts or result.sequence_nodes or result.sequence_edges:
            raise AnalyzerContractError(
                f"INVENTORY_ONLY_SEMANTIC_EVIDENCE_FORBIDDEN:{analyzer_id}"
            )
        if result.proof_status not in {"NOT_PROVEN", "NOT_APPLICABLE"}:
            raise AnalyzerContractError(
                f"INVENTORY_ONLY_PROOF_STATUS_INVALID:{analyzer_id}:{result.proof_status}"
            )
    return result


def run_analyzers(
    root: Path,
    analyzers: tuple[StructuralAnalyzer, ...],
    snapshot: ProjectSnapshot | None = None,
) -> tuple[ProjectSnapshot, list[AnalyzerResult]]:
    root = root.resolve()
    snap = resolve_snapshot(root, snapshot)
    results: list[AnalyzerResult] = []
    seen_ids: set[str] = set()
    for analyzer in analyzers:
        analyzer_id = str(analyzer.analyzer_id).strip()
        if not analyzer_id or analyzer_id in seen_ids:
            raise AnalyzerContractError(f"ANALYZER_ID_NOT_UNIQUE:{analyzer_id}")
        seen_ids.add(analyzer_id)
        result = validate_result(analyzer.analyze(root, snap))
        if result.analyzer_id != analyzer_id:
            raise AnalyzerContractError(
                f"ANALYZER_ID_MISMATCH:{analyzer_id}:{result.analyzer_id}"
            )
        results.append(result)
    return snap, results


def generic_fallback(
    root: Path,
    snapshot: ProjectSnapshot,
    claimed_extensions: frozenset[str],
) -> AnalyzerResult:
    root = root.resolve()
    unsupported = sorted(
        path.relative_to(root).as_posix()
        for path in snapshot.source_files()
        if path.suffix.lower() not in claimed_extensions
    )
    result = AnalyzerResult(
        analyzer_id="generic_inventory",
        languages=(),
        claimed_extensions=(),
        semantic_level="inventory_only",
        proof_status="NOT_PROVEN" if unsupported else "NOT_APPLICABLE",
        limitations=[
            "semantic symbols, routes, calls, and runtime ordering are NOT_PROVEN for unsupported languages",
            *DYNAMIC_BEHAVIOR_LIMITATIONS,
        ],
        unsupported_files=unsupported,
    )
    return validate_result(result)


def coverage_records(results: list[AnalyzerResult]) -> list[dict]:
    return [validate_result(result).coverage_record() for result in results]


def result_by_id(results: list[AnalyzerResult], analyzer_id: str) -> AnalyzerResult:
    matches = [result for result in results if result.analyzer_id == analyzer_id]
    if len(matches) != 1:
        raise AnalyzerContractError(
            f"ANALYZER_RESULT_ID_NOT_UNIQUE:{analyzer_id}:{len(matches)}"
        )
    return matches[0]

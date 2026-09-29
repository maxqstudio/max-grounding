"""Deterministic bounded corroboration, contradiction, and evidence graphs."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence

from .errors import EvidenceGraphError
from .models import (
    EvidenceAssertion,
    EvidenceCluster,
    EvidenceGraph,
    EvidenceQualityScore,
    EvidenceRelation,
    EvidenceRelationType,
)

MAX_GRAPH_ASSERTIONS = 8
MAX_ASSERTION_ID_CHARS = 128
MAX_CLAIM_KEY_CHARS = 256
MAX_ASSERTION_VALUE_CHARS = 1024


def _bounded_assertions(value: object) -> tuple[EvidenceAssertion, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise EvidenceGraphError("assertions must be a bounded sequence")
    try:
        count = len(value)
    except Exception as exc:
        raise EvidenceGraphError("assertions must expose a bounded length") from exc
    if count > MAX_GRAPH_ASSERTIONS:
        raise EvidenceGraphError(
            f"assertions must not exceed {MAX_GRAPH_ASSERTIONS} items"
        )
    items = tuple(value)
    if len(items) != count:
        raise EvidenceGraphError("assertions changed length during validation")
    return items


def _normalize_text(value: object, *, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise EvidenceGraphError(f"{label} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    if not normalized:
        raise EvidenceGraphError(f"{label} must be non-empty")
    if len(normalized) > maximum:
        raise EvidenceGraphError(f"{label} exceeds the accepted length")
    return normalized


def _validated_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceGraphError(f"{label} must be a finite number in [0,1]")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise EvidenceGraphError(f"{label} must be a finite number in [0,1]")
    return number


def _validate_quality(quality: object) -> None:
    if not isinstance(quality, EvidenceQualityScore):
        raise EvidenceGraphError("evidence must be an EvidenceQualityScore")
    excerpt = quality.excerpt
    metadata = quality.metadata
    if excerpt.source_url != metadata.source_url or excerpt.chunk_id != metadata.chunk_id:
        raise EvidenceGraphError("evidence quality provenance must match exactly")
    if not excerpt.source_url or not excerpt.chunk_id:
        raise EvidenceGraphError("evidence provenance must be non-empty")

    authority = _validated_number(quality.authority_score, label="authority_score")
    freshness = _validated_number(quality.freshness_score, label="freshness_score")
    validity = _validated_number(
        quality.temporal_validity,
        label="temporal_validity",
    )
    score = _validated_number(quality.score, label="quality score")
    expected = authority * freshness * validity
    if not math.isclose(score, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise EvidenceGraphError("quality score must match Phase 8 components")
    if (
        isinstance(quality.rank, bool)
        or not isinstance(quality.rank, int)
        or not 1 <= quality.rank <= MAX_GRAPH_ASSERTIONS
    ):
        raise EvidenceGraphError("quality rank is outside the accepted range")


def _normalize_and_validate(
    assertions: Sequence[EvidenceAssertion],
) -> tuple[EvidenceAssertion, ...]:
    raw = _bounded_assertions(assertions)
    normalized: list[EvidenceAssertion] = []
    assertion_ids: set[str] = set()
    evidence_ids: set[tuple[str, str]] = set()
    quality_ranks: set[int] = set()
    claim_exclusivity: dict[str, bool] = {}

    for item in raw:
        if not isinstance(item, EvidenceAssertion):
            raise EvidenceGraphError("assertion items must be EvidenceAssertion")
        assertion_id = _normalize_text(
            item.assertion_id,
            label="assertion_id",
            maximum=MAX_ASSERTION_ID_CHARS,
        )
        claim_key = _normalize_text(
            item.claim_key,
            label="claim_key",
            maximum=MAX_CLAIM_KEY_CHARS,
        )
        claim_value = _normalize_text(
            item.value,
            label="value",
            maximum=MAX_ASSERTION_VALUE_CHARS,
        )
        if not isinstance(item.exclusive, bool):
            raise EvidenceGraphError("exclusive must be boolean")
        _validate_quality(item.evidence)

        if assertion_id in assertion_ids:
            raise EvidenceGraphError("assertion_id must be unique")
        assertion_ids.add(assertion_id)

        evidence_id = (
            item.evidence.excerpt.source_url,
            item.evidence.excerpt.chunk_id,
        )
        if evidence_id in evidence_ids:
            raise EvidenceGraphError("one evidence chunk cannot back multiple assertions")
        evidence_ids.add(evidence_id)

        if item.evidence.rank in quality_ranks:
            raise EvidenceGraphError("Phase 8 quality ranks must be unique")
        quality_ranks.add(item.evidence.rank)

        previous = claim_exclusivity.get(claim_key)
        if previous is not None and previous is not item.exclusive:
            raise EvidenceGraphError(
                "all assertions for one claim_key must agree on exclusivity"
            )
        claim_exclusivity[claim_key] = item.exclusive

        normalized.append(
            EvidenceAssertion(
                assertion_id=assertion_id,
                claim_key=claim_key,
                value=claim_value,
                exclusive=item.exclusive,
                evidence=item.evidence,
            )
        )

    normalized.sort(key=lambda item: item.assertion_id)
    return tuple(normalized)


def build_relations(
    assertions: Sequence[EvidenceAssertion],
) -> tuple[EvidenceRelation, ...]:
    """Derive exact structured corroboration and explicit exclusive conflicts."""
    items = _normalize_and_validate(assertions)
    relations: list[EvidenceRelation] = []
    for index, left in enumerate(items):
        for right in items[index + 1 :]:
            if left.claim_key != right.claim_key:
                continue
            if left.value == right.value:
                relation = EvidenceRelationType.CORROBORATES
            elif left.exclusive:
                relation = EvidenceRelationType.CONTRADICTS
            else:
                continue
            relations.append(
                EvidenceRelation(
                    left_assertion_id=left.assertion_id,
                    right_assertion_id=right.assertion_id,
                    relation=relation,
                )
            )
    return tuple(relations)


def build_clusters(
    assertions: Sequence[EvidenceAssertion],
) -> tuple[EvidenceCluster, ...]:
    """Group equivalent values and count independent source URLs once."""
    items = _normalize_and_validate(assertions)
    grouped: dict[tuple[str, str], list[EvidenceAssertion]] = {}
    for item in items:
        grouped.setdefault((item.claim_key, item.value), []).append(item)

    clusters: list[EvidenceCluster] = []
    for (claim_key, value), members in sorted(grouped.items()):
        source_max: dict[str, float] = {}
        for item in members:
            source = item.evidence.excerpt.source_url
            source_max[source] = max(source_max.get(source, 0.0), item.evidence.score)
        clusters.append(
            EvidenceCluster(
                claim_key=claim_key,
                value=value,
                assertion_ids=tuple(
                    sorted(item.assertion_id for item in members)
                ),
                distinct_source_count=len(source_max),
                quality_weight_sum=sum(source_max.values()),
            )
        )
    return tuple(clusters)


def build_evidence_graph(
    assertions: Sequence[EvidenceAssertion],
) -> EvidenceGraph:
    """Build a bounded graph without selecting a winning or true assertion."""
    items = _normalize_and_validate(assertions)
    return EvidenceGraph(
        assertions=items,
        clusters=build_clusters(items),
        relations=build_relations(items),
    )

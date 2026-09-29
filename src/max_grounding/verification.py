"""Deterministic claim verification, citations, and fail-closed synthesis."""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import replace

from .errors import ClaimVerificationError, EvidenceGraphError
from .evidence_graph import build_evidence_graph
from .models import (
    AnswerClaim,
    ClaimCitation,
    ClaimVerification,
    ClaimVerificationStatus,
    EvidenceCluster,
    EvidenceGraph,
    EvidenceRelationType,
    SynthesisPacket,
)

MAX_ANSWER_CLAIMS = 16
MAX_REQUIRED_SOURCES = 3
MAX_CITATIONS_PER_CLAIM = 8
MAX_CLAIM_ID_CHARS = 128
MAX_CLAIM_TEXT_CHARS = 2048
MAX_CLAIM_KEY_CHARS = 256
MAX_CLAIM_VALUE_CHARS = 1024


def _bounded_claims(value: object) -> tuple[AnswerClaim, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ClaimVerificationError("claims must be a bounded sequence")
    try:
        count = len(value)
    except Exception as exc:
        raise ClaimVerificationError("claims must expose a bounded length") from exc
    if count > MAX_ANSWER_CLAIMS:
        raise ClaimVerificationError(
            f"claims must not exceed {MAX_ANSWER_CLAIMS} items"
        )
    items = tuple(value)
    if len(items) != count:
        raise ClaimVerificationError("claims changed length during validation")
    return items


def _normalize_text(
    value: object,
    *,
    label: str,
    maximum: int,
    casefold: bool,
) -> str:
    if not isinstance(value, str):
        raise ClaimVerificationError(f"{label} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if casefold:
        normalized = normalized.casefold()
    if not normalized:
        raise ClaimVerificationError(f"{label} must be non-empty")
    if len(normalized) > maximum:
        raise ClaimVerificationError(f"{label} exceeds the accepted length")
    return normalized


def _normalize_claims(value: object) -> tuple[AnswerClaim, ...]:
    raw = _bounded_claims(value)
    normalized: list[AnswerClaim] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, AnswerClaim):
            raise ClaimVerificationError("claim items must be AnswerClaim")
        claim_id = _normalize_text(
            item.claim_id,
            label="claim_id",
            maximum=MAX_CLAIM_ID_CHARS,
            casefold=True,
        )
        if claim_id in seen:
            raise ClaimVerificationError("claim_id must be unique")
        seen.add(claim_id)
        normalized.append(
            AnswerClaim(
                claim_id=claim_id,
                text=_normalize_text(
                    item.text,
                    label="claim text",
                    maximum=MAX_CLAIM_TEXT_CHARS,
                    casefold=False,
                ),
                claim_key=_normalize_text(
                    item.claim_key,
                    label="claim_key",
                    maximum=MAX_CLAIM_KEY_CHARS,
                    casefold=True,
                ),
                value=_normalize_text(
                    item.value,
                    label="claim value",
                    maximum=MAX_CLAIM_VALUE_CHARS,
                    casefold=True,
                ),
            )
        )
    return tuple(normalized)


def _validate_required_sources(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ClaimVerificationError("required_sources must be an integer")
    if not 1 <= value <= MAX_REQUIRED_SOURCES:
        raise ClaimVerificationError(
            f"required_sources must be between 1 and {MAX_REQUIRED_SOURCES}"
        )
    return value


def _validated_graph(value: object) -> EvidenceGraph:
    if not isinstance(value, EvidenceGraph):
        raise ClaimVerificationError("graph must be an EvidenceGraph")
    try:
        rebuilt = build_evidence_graph(value.assertions)
    except EvidenceGraphError as exc:
        raise ClaimVerificationError("graph assertions are invalid") from exc
    if rebuilt != value:
        raise ClaimVerificationError(
            "graph clusters or relations do not match validated assertions"
        )
    return value


def _cluster_map(graph: EvidenceGraph) -> dict[tuple[str, str], EvidenceCluster]:
    return {
        (cluster.claim_key, cluster.value): cluster
        for cluster in graph.clusters
    }


def _has_conflict(graph: EvidenceGraph, cluster: EvidenceCluster) -> bool:
    supporting = set(cluster.assertion_ids)
    for relation in graph.relations:
        if relation.relation is not EvidenceRelationType.CONTRADICTS:
            continue
        if (
            relation.left_assertion_id in supporting
            or relation.right_assertion_id in supporting
        ):
            return True
    return False


def _confidence_index(
    cluster: EvidenceCluster,
    *,
    required_sources: int,
) -> float:
    if cluster.distinct_source_count <= 0:
        raise ClaimVerificationError("support cluster has no distinct source")
    if (
        isinstance(cluster.quality_weight_sum, bool)
        or not isinstance(cluster.quality_weight_sum, (int, float))
        or not math.isfinite(float(cluster.quality_weight_sum))
    ):
        raise ClaimVerificationError("support cluster has invalid quality weight")
    mean_quality = float(cluster.quality_weight_sum) / cluster.distinct_source_count
    coverage = min(cluster.distinct_source_count / required_sources, 1.0)
    confidence = mean_quality * coverage
    if not 0.0 <= confidence <= 1.0:
        raise ClaimVerificationError("derived confidence is outside [0,1]")
    return confidence


def verify_claims(
    claims: Sequence[AnswerClaim],
    graph: EvidenceGraph,
    *,
    required_sources: int = 1,
) -> tuple[ClaimVerification, ...]:
    """Classify explicit claims using exact structured evidence only."""
    items = _normalize_claims(claims)
    validated_graph = _validated_graph(graph)
    minimum_sources = _validate_required_sources(required_sources)
    clusters = _cluster_map(validated_graph)

    results: list[ClaimVerification] = []
    for claim in items:
        cluster = clusters.get((claim.claim_key, claim.value))
        if cluster is None:
            status = ClaimVerificationStatus.UNSUPPORTED
            confidence = 0.0
            source_count = 0
        else:
            source_count = cluster.distinct_source_count
            if _has_conflict(validated_graph, cluster):
                status = ClaimVerificationStatus.CONFLICTED
                confidence = 0.0
            else:
                confidence = _confidence_index(
                    cluster,
                    required_sources=minimum_sources,
                )
                if source_count >= minimum_sources:
                    status = ClaimVerificationStatus.SUPPORTED
                else:
                    status = ClaimVerificationStatus.PARTIALLY_SUPPORTED

        results.append(
            ClaimVerification(
                claim=claim,
                status=status,
                confidence=confidence,
                supporting_source_count=source_count,
                required_sources=minimum_sources,
            )
        )
    return tuple(results)


def build_claim_citations(
    claims: Sequence[AnswerClaim],
    graph: EvidenceGraph,
) -> tuple[ClaimCitation, ...]:
    """Bind exact supporting assertion excerpts to their answer claims."""
    items = _normalize_claims(claims)
    validated_graph = _validated_graph(graph)
    clusters = _cluster_map(validated_graph)
    assertions = {
        assertion.assertion_id: assertion
        for assertion in validated_graph.assertions
    }

    citations: list[ClaimCitation] = []
    for claim in items:
        cluster = clusters.get((claim.claim_key, claim.value))
        if cluster is None:
            continue
        if len(cluster.assertion_ids) > MAX_CITATIONS_PER_CLAIM:
            raise ClaimVerificationError("claim citation count exceeds hard cap")
        for assertion_id in sorted(cluster.assertion_ids):
            assertion = assertions.get(assertion_id)
            if assertion is None:
                raise ClaimVerificationError(
                    "citation references an unknown graph assertion"
                )
            excerpt = assertion.evidence.excerpt
            citations.append(
                ClaimCitation(
                    claim_id=claim.claim_id,
                    assertion_id=assertion.assertion_id,
                    source_url=excerpt.source_url,
                    chunk_id=excerpt.chunk_id,
                    text=excerpt.text,
                )
            )
    return tuple(citations)


def build_synthesis_packet(
    claims: Sequence[AnswerClaim],
    graph: EvidenceGraph,
    *,
    required_sources: int = 1,
) -> SynthesisPacket:
    """Build a structured packet that excludes non-supported claims from synthesis."""
    assessments = verify_claims(
        claims,
        graph,
        required_sources=required_sources,
    )
    citations = build_claim_citations(
        tuple(item.claim for item in assessments),
        graph,
    )

    by_claim: dict[str, list[ClaimCitation]] = {
        item.claim.claim_id: []
        for item in assessments
    }
    for citation in citations:
        bucket = by_claim.get(citation.claim_id)
        if bucket is None:
            raise ClaimVerificationError("citation references an unknown answer claim")
        bucket.append(citation)

    verifications = tuple(
        replace(
            item,
            citations=tuple(by_claim[item.claim.claim_id]),
        )
        for item in assessments
    )
    synthesis_claims = tuple(
        item
        for item in verifications
        if item.status is ClaimVerificationStatus.SUPPORTED
    )
    blocked_claims = tuple(
        item
        for item in verifications
        if item.status is not ClaimVerificationStatus.SUPPORTED
    )
    return SynthesisPacket(
        verifications=verifications,
        synthesis_claims=synthesis_claims,
        blocked_claims=blocked_claims,
    )

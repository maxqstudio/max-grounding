"""Deterministic freshness, authority, and temporal evidence scoring."""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from .errors import AuthorityProviderError, TemporalScoringError
from .models import (
    AuthoritySubject,
    EvidenceExcerpt,
    EvidenceMetadata,
    EvidenceQualityScore,
    TemporalComponents,
)

MAX_TEMPORAL_EVIDENCE = 8
MIN_FRESHNESS_HORIZON_SECONDS = 60
MAX_FRESHNESS_HORIZON_SECONDS = 365 * 24 * 60 * 60
DEFAULT_FRESHNESS_HORIZON_SECONDS = 7 * 24 * 60 * 60


class AuthorityProvider(Protocol):
    """Injected domain policy for bounded source-authority scoring."""

    def score(
        self,
        subjects: tuple[AuthoritySubject, ...],
    ) -> Sequence[float]:
        """Return exactly one authority score in [0,1] per source subject."""


def _validate_utc(value: object, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TemporalScoringError(f"{label} must be a timezone-aware UTC datetime")
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise TemporalScoringError(f"{label} must be a timezone-aware UTC datetime")
    return value


def _validate_horizon(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TemporalScoringError("freshness_horizon_seconds must be an integer")
    if not MIN_FRESHNESS_HORIZON_SECONDS <= value <= MAX_FRESHNESS_HORIZON_SECONDS:
        raise TemporalScoringError(
            "freshness_horizon_seconds is outside the accepted range"
        )
    return value


def _validate_basic_metadata(items: tuple[EvidenceMetadata, ...]) -> None:
    if len(items) > MAX_TEMPORAL_EVIDENCE:
        raise TemporalScoringError(
            f"evidence metadata must not exceed {MAX_TEMPORAL_EVIDENCE} items"
        )
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, EvidenceMetadata):
            raise TemporalScoringError("metadata items must be EvidenceMetadata")
        if not isinstance(item.source_url, str) or not item.source_url:
            raise TemporalScoringError("metadata source_url must be non-empty")
        if not isinstance(item.chunk_id, str) or not item.chunk_id:
            raise TemporalScoringError("metadata chunk_id must be non-empty")
        if item.chunk_id in seen:
            raise TemporalScoringError("metadata contains a duplicate chunk_id")
        seen.add(item.chunk_id)
        for value, label in (
            (item.source_type, "source_type"),
            (item.geography, "geography"),
        ):
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise TemporalScoringError(
                    f"metadata {label} must be non-empty when provided"
                )


def _validate_temporal_inputs(
    items: tuple[EvidenceMetadata, ...],
    *,
    now: datetime,
    freshness_horizon_seconds: int,
) -> tuple[datetime, int]:
    _validate_basic_metadata(items)
    evaluated_at = _validate_utc(now, label="now")
    horizon = _validate_horizon(freshness_horizon_seconds)

    for item in items:
        retrieved_at = _validate_utc(item.retrieved_at, label="retrieved_at")
        if retrieved_at > evaluated_at:
            raise TemporalScoringError("retrieved_at must not be later than now")

        published_at = None
        if item.published_at is not None:
            published_at = _validate_utc(item.published_at, label="published_at")
            if published_at > evaluated_at:
                raise TemporalScoringError("published_at must not be later than now")
            if published_at > retrieved_at:
                raise TemporalScoringError(
                    "published_at must not be later than retrieved_at"
                )

        valid_from = None
        if item.valid_from is not None:
            valid_from = _validate_utc(item.valid_from, label="valid_from")

        valid_until = None
        if item.valid_until is not None:
            valid_until = _validate_utc(item.valid_until, label="valid_until")

        if (
            valid_from is not None
            and valid_until is not None
            and valid_from > valid_until
        ):
            raise TemporalScoringError("valid_from must not be later than valid_until")

    return evaluated_at, horizon


def score_authority(
    metadata: Sequence[EvidenceMetadata],
    provider: AuthorityProvider,
) -> tuple[float, ...]:
    """Obtain one bounded authority-score sequence without hardcoded opinions."""
    items = tuple(metadata)
    _validate_basic_metadata(items)
    if not items:
        return ()

    subjects = tuple(
        AuthoritySubject(
            source_url=item.source_url,
            source_type=item.source_type,
            geography=item.geography,
        )
        for item in items
    )
    try:
        raw_scores = provider.score(subjects)
    except Exception as exc:
        raise AuthorityProviderError("authority provider call failed") from exc

    if isinstance(raw_scores, (str, bytes)) or not isinstance(raw_scores, Sequence):
        raise AuthorityProviderError(
            "authority provider output must be a bounded score sequence"
        )

    returned = tuple(raw_scores)
    if len(returned) != len(items):
        raise AuthorityProviderError(
            "authority provider returned the wrong score count"
        )

    scores: list[float] = []
    for raw in returned:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise AuthorityProviderError(
                "authority scores must be finite real numbers in [0,1]"
            )
        score = float(raw)
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise AuthorityProviderError(
                "authority scores must be finite real numbers in [0,1]"
            )
        scores.append(score)
    return tuple(scores)


def score_temporal_components(
    metadata: Sequence[EvidenceMetadata],
    *,
    now: datetime,
    freshness_horizon_seconds: int = DEFAULT_FRESHNESS_HORIZON_SECONDS,
) -> tuple[TemporalComponents, ...]:
    """Score explicit freshness and point-in-time validity deterministically."""
    items = tuple(metadata)
    evaluated_at, horizon = _validate_temporal_inputs(
        items,
        now=now,
        freshness_horizon_seconds=freshness_horizon_seconds,
    )

    result: list[TemporalComponents] = []
    for item in items:
        reference = item.published_at if item.published_at is not None else item.retrieved_at
        age_seconds = (evaluated_at - reference).total_seconds()
        freshness = max(0.0, 1.0 - age_seconds / horizon)

        valid_from_ok = item.valid_from is None or evaluated_at >= item.valid_from
        valid_until_ok = item.valid_until is None or evaluated_at <= item.valid_until
        validity = 1.0 if valid_from_ok and valid_until_ok else 0.0

        result.append(
            TemporalComponents(
                freshness_score=freshness,
                temporal_validity=validity,
            )
        )
    return tuple(result)


def _validate_excerpt_metadata_pairs(
    excerpts: tuple[EvidenceExcerpt, ...],
    metadata: tuple[EvidenceMetadata, ...],
) -> None:
    if len(excerpts) > MAX_TEMPORAL_EVIDENCE:
        raise TemporalScoringError(
            f"evidence excerpts must not exceed {MAX_TEMPORAL_EVIDENCE} items"
        )
    if len(excerpts) != len(metadata):
        raise TemporalScoringError(
            "evidence excerpts and metadata must have identical counts"
        )

    seen: set[str] = set()
    for item, meta in zip(excerpts, metadata):
        if not isinstance(item, EvidenceExcerpt):
            raise TemporalScoringError("evidence items must be EvidenceExcerpt")
        if not item.chunk_id or item.chunk_id in seen:
            raise TemporalScoringError(
                "evidence excerpts contain invalid or duplicate chunk identity"
            )
        seen.add(item.chunk_id)
        if item.source_url != meta.source_url or item.chunk_id != meta.chunk_id:
            raise TemporalScoringError(
                "evidence excerpt and metadata provenance must match exactly"
            )


def score_evidence_quality(
    excerpts: Sequence[EvidenceExcerpt],
    metadata: Sequence[EvidenceMetadata],
    provider: AuthorityProvider,
    *,
    now: datetime,
    freshness_horizon_seconds: int = DEFAULT_FRESHNESS_HORIZON_SECONDS,
) -> tuple[EvidenceQualityScore, ...]:
    """Combine injected authority with deterministic freshness and validity."""
    evidence = tuple(excerpts)
    metadata_items = tuple(metadata)
    _validate_excerpt_metadata_pairs(evidence, metadata_items)

    # Validate all temporal inputs before any external policy call.
    _validate_temporal_inputs(
        metadata_items,
        now=now,
        freshness_horizon_seconds=freshness_horizon_seconds,
    )
    if not evidence:
        return ()

    authority_scores = score_authority(metadata_items, provider)
    temporal_scores = score_temporal_components(
        metadata_items,
        now=now,
        freshness_horizon_seconds=freshness_horizon_seconds,
    )

    scored = []
    for item, meta, authority, temporal in zip(
        evidence,
        metadata_items,
        authority_scores,
        temporal_scores,
    ):
        combined = authority * temporal.freshness_score * temporal.temporal_validity
        scored.append((combined, item, meta, authority, temporal))

    scored.sort(
        key=lambda entry: (
            -entry[0],
            entry[1].rerank_rank,
            entry[1].source_url,
            entry[1].chunk_index,
            entry[1].chunk_id,
        )
    )

    return tuple(
        EvidenceQualityScore(
            excerpt=item,
            metadata=meta,
            authority_score=authority,
            freshness_score=temporal.freshness_score,
            temporal_validity=temporal.temporal_validity,
            score=combined,
            rank=rank,
        )
        for rank, (combined, item, meta, authority, temporal) in enumerate(
            scored,
            start=1,
        )
    )

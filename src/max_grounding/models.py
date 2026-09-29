"""Immutable contracts used by the grounding core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EvidenceStatus(str, Enum):
    """Whether retrieved evidence is safe to treat as grounded."""

    GROUNDED = "grounded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_ERROR = "provider_error"


@dataclass(frozen=True, slots=True)
class GroundingRequest:
    """Caller intent and bounded retrieval policy for one grounding attempt."""

    query: str
    language: str = "en"
    country: str | None = None
    freshness: str | None = None
    max_search_rounds: int = 2
    results_per_call: int = 5
    min_evidence_sources: int = 1


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Normalized provider-facing query for one budgeted search call."""

    query: str
    language: str
    country: str | None
    freshness: str | None
    limit: int
    round_index: int


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """Untrusted candidate returned by a search provider."""

    url: str
    title: str
    snippet: str
    provider: str
    rank: int


@dataclass(frozen=True, slots=True)
class EvidenceSource:
    """Normalized source retained for downstream grounding."""

    canonical_url: str
    url: str
    title: str
    snippet: str
    provider: str
    rank: int


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """Fail-closed output of the grounding core."""

    query: str
    status: EvidenceStatus
    sources: tuple[EvidenceSource, ...]
    search_calls_used: int
    reason: str


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    """Bounded text extracted from one securely fetched result page."""

    url: str
    fetched_from_ip: str
    media_type: str
    charset: str
    byte_length: int
    text: str


@dataclass(frozen=True, slots=True)
class TextChunk:
    """Deterministic bounded text slice retaining source provenance."""

    chunk_id: str
    source_url: str
    chunk_index: int
    text: str
    token_count: int


@dataclass(frozen=True, slots=True)
class LexicalHit:
    """One immutable positive-score lexical retrieval result."""

    chunk: TextChunk
    score: float
    rank: int

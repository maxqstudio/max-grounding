"""Immutable contracts used by the grounding core."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class EvidenceStatus(str, Enum):
    """Whether retrieved evidence is safe to treat as grounded."""

    GROUNDED = "grounded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_ERROR = "provider_error"


class EvidenceRelationType(str, Enum):
    """Deterministic relationship between two structured evidence assertions."""

    CORROBORATES = "corroborates"
    CONTRADICTS = "contradicts"


class ClaimVerificationStatus(str, Enum):
    """Fail-closed verification state for one explicit answer claim."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    CONFLICTED = "conflicted"
    UNSUPPORTED = "unsupported"


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
    evidence_ref: str | None = None


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


@dataclass(frozen=True, slots=True)
class SemanticHit:
    """One immutable positive-score semantic retrieval result."""

    chunk: TextChunk
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class HybridHit:
    """One immutable reciprocal-rank-fused retrieval result."""

    chunk: TextChunk
    score: float
    rank: int
    lexical_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True, slots=True)
class RerankedHit:
    """One immutable provider-reranked hybrid result."""

    chunk: TextChunk
    score: float
    rank: int
    hybrid_rank: int
    lexical_rank: int | None
    semantic_rank: int | None


@dataclass(frozen=True, slots=True)
class EvidenceExcerpt:
    """One exact extractive excerpt retaining source and rerank provenance."""

    source_url: str
    chunk_id: str
    chunk_index: int
    rerank_rank: int
    text: str
    char_count: int


@dataclass(frozen=True, slots=True)
class EvidenceMetadata:
    """Explicit temporal and source context bound to one evidence excerpt."""

    source_url: str
    chunk_id: str
    retrieved_at: datetime
    published_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source_type: str | None = None
    geography: str | None = None


@dataclass(frozen=True, slots=True)
class AuthoritySubject:
    """Source context exposed to an injected authority policy."""

    source_url: str
    source_type: str | None
    geography: str | None


@dataclass(frozen=True, slots=True)
class TemporalComponents:
    """Deterministic freshness and validity components for one evidence item."""

    freshness_score: float
    temporal_validity: float


@dataclass(frozen=True, slots=True)
class EvidenceQualityScore:
    """One temporally scored evidence excerpt retaining complete provenance."""

    excerpt: EvidenceExcerpt
    metadata: EvidenceMetadata
    authority_score: float
    freshness_score: float
    temporal_validity: float
    score: float
    rank: int


@dataclass(frozen=True, slots=True)
class EvidenceAssertion:
    """One explicit structured assertion backed by scored evidence."""

    assertion_id: str
    claim_key: str
    value: str
    exclusive: bool
    evidence: EvidenceQualityScore


@dataclass(frozen=True, slots=True)
class EvidenceRelation:
    """One deterministic corroboration or contradiction edge."""

    left_assertion_id: str
    right_assertion_id: str
    relation: EvidenceRelationType


@dataclass(frozen=True, slots=True)
class EvidenceCluster:
    """Equivalent normalized assertions grouped without declaring truth."""

    claim_key: str
    value: str
    assertion_ids: tuple[str, ...]
    distinct_source_count: int
    quality_weight_sum: float


@dataclass(frozen=True, slots=True)
class EvidenceGraph:
    """Bounded structured evidence graph with no truth-winner field."""

    assertions: tuple[EvidenceAssertion, ...]
    clusters: tuple[EvidenceCluster, ...]
    relations: tuple[EvidenceRelation, ...]


@dataclass(frozen=True, slots=True)
class AnswerClaim:
    """One explicit structured answer claim proposed for verification."""

    claim_id: str
    text: str
    claim_key: str
    value: str


@dataclass(frozen=True, slots=True)
class ClaimCitation:
    """Exact supporting assertion provenance bound to one answer claim."""

    claim_id: str
    assertion_id: str
    source_url: str
    chunk_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ClaimVerification:
    """One deterministic answer-claim verification result."""

    claim: AnswerClaim
    status: ClaimVerificationStatus
    confidence: float
    supporting_source_count: int
    required_sources: int
    citations: tuple[ClaimCitation, ...] = ()


@dataclass(frozen=True, slots=True)
class SynthesisPacket:
    """Structured fail-closed handoff containing only synthesis-safe claims."""

    verifications: tuple[ClaimVerification, ...]
    synthesis_claims: tuple[ClaimVerification, ...]
    blocked_claims: tuple[ClaimVerification, ...]


@dataclass(frozen=True, slots=True)
class PersistentVectorHit:
    """One validated persistent vector-store match retaining chunk provenance."""

    point_id: str
    chunk: TextChunk
    score: float
    embedding_model: str
    embedding_dimension: int
    schema_version: int


@dataclass(frozen=True, slots=True)
class PersistentIndexResult:
    """Summary of one completed persistent indexing request."""

    chunks_indexed: int
    vectors_indexed: int
    embedding_model: str
    embedding_dimension: int
    schema_version: int
    collection_name: str

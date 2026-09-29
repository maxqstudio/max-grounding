"""Bounded provider-agnostic reranking and extractive context compression."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Protocol

from .errors import RerankingError, RerankProviderError
from .models import EvidenceExcerpt, HybridHit, RerankedHit
from .retrieval import MAX_QUERY_CHARS, MAX_RESULTS, tokenize_text

MAX_RERANK_CANDIDATES = MAX_RESULTS
MAX_CONTEXT_EXCERPTS = 8
MAX_EXCERPT_CHARS = 1200
MAX_CONTEXT_CHARS = 6000

DEFAULT_RERANK_LIMIT = 8
DEFAULT_CONTEXT_EXCERPTS = 5
DEFAULT_EXCERPT_CHARS = 800
DEFAULT_CONTEXT_CHARS = 4000

_SENTENCE_RE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")


class RerankProvider(Protocol):
    """Injected scoring boundary for already-bounded hybrid candidates."""

    def score(
        self,
        query: str,
        documents: tuple[str, ...],
    ) -> Sequence[float]:
        """Return exactly one numeric relevance score per document."""


def _validated_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise RerankingError("query must be non-empty")
    if len(query) > MAX_QUERY_CHARS:
        raise RerankingError(
            f"query must not exceed {MAX_QUERY_CHARS} characters"
        )
    return query.strip()


def _validated_finite_number(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RerankingError(f"{label} must be a finite real number")
    number = float(value)
    if not math.isfinite(number):
        raise RerankingError(f"{label} must be a finite real number")
    return number


def _validate_optional_rank(value: int | None, *, label: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int):
        raise RerankingError(f"{label} must be a positive integer or None")
    if not 1 <= value <= MAX_RERANK_CANDIDATES:
        raise RerankingError(f"{label} is outside the accepted rank range")


def _validate_hybrid_hits(hits: tuple[HybridHit, ...]) -> None:
    if len(hits) > MAX_RERANK_CANDIDATES:
        raise RerankingError(
            f"hybrid candidates must not exceed {MAX_RERANK_CANDIDATES} items"
        )

    seen: set[str] = set()
    for expected_rank, hit in enumerate(hits, start=1):
        if isinstance(hit.rank, bool) or hit.rank != expected_rank:
            raise RerankingError(
                "hybrid ranks must be unique and contiguous from 1"
            )
        if not hit.chunk.chunk_id:
            raise RerankingError("chunk_id must be non-empty")
        if hit.chunk.chunk_id in seen:
            raise RerankingError("hybrid candidates contain a duplicate chunk_id")
        seen.add(hit.chunk.chunk_id)

        score = _validated_finite_number(hit.score, label="hybrid score")
        if score <= 0.0:
            raise RerankingError("hybrid score must be positive")
        _validate_optional_rank(hit.lexical_rank, label="lexical_rank")
        _validate_optional_rank(hit.semantic_rank, label="semantic_rank")


def _validate_reranked_hits(hits: tuple[RerankedHit, ...]) -> None:
    if len(hits) > MAX_RERANK_CANDIDATES:
        raise RerankingError(
            f"reranked candidates must not exceed {MAX_RERANK_CANDIDATES} items"
        )
    seen: set[str] = set()
    for expected_rank, hit in enumerate(hits, start=1):
        if isinstance(hit.rank, bool) or hit.rank != expected_rank:
            raise RerankingError(
                "reranked ranks must be unique and contiguous from 1"
            )
        if hit.chunk.chunk_id in seen or not hit.chunk.chunk_id:
            raise RerankingError("reranked candidates contain invalid chunk identity")
        seen.add(hit.chunk.chunk_id)
        _validated_finite_number(hit.score, label="rerank score")
        if (
            isinstance(hit.hybrid_rank, bool)
            or not isinstance(hit.hybrid_rank, int)
            or not 1 <= hit.hybrid_rank <= MAX_RERANK_CANDIDATES
        ):
            raise RerankingError("hybrid_rank is outside the accepted rank range")
        _validate_optional_rank(hit.lexical_rank, label="lexical_rank")
        _validate_optional_rank(hit.semantic_rank, label="semantic_rank")


def rerank_hybrid(
    query: str,
    hits: Sequence[HybridHit],
    provider: RerankProvider,
    *,
    limit: int = DEFAULT_RERANK_LIMIT,
) -> tuple[RerankedHit, ...]:
    """Rerank bounded hybrid hits in one provider call with fail-closed scores."""
    normalized_query = _validated_query(query)
    if not 1 <= limit <= MAX_RERANK_CANDIDATES:
        raise RerankingError(
            f"limit must be between 1 and {MAX_RERANK_CANDIDATES}"
        )

    candidates = tuple(hits)
    _validate_hybrid_hits(candidates)
    if not candidates:
        return ()

    documents = tuple(hit.chunk.text for hit in candidates)
    try:
        raw_returned = provider.score(normalized_query, documents)
    except Exception as exc:
        raise RerankProviderError("rerank provider call failed") from exc

    if isinstance(raw_returned, (str, bytes)) or not isinstance(
        raw_returned,
        Sequence,
    ):
        raise RerankProviderError(
            "rerank provider output must be a bounded score sequence"
        )
    returned = tuple(raw_returned)
    if len(returned) != len(candidates):
        raise RerankProviderError(
            "rerank provider returned the wrong score count"
        )

    scored: list[tuple[float, HybridHit]] = []
    for raw_score, hit in zip(returned, candidates):
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            raise RerankProviderError(
                "rerank provider scores must be finite real numbers"
            )
        score = float(raw_score)
        if not math.isfinite(score):
            raise RerankProviderError(
                "rerank provider scores must be finite real numbers"
            )
        scored.append((score, hit))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].rank,
            item[1].chunk.source_url,
            item[1].chunk.chunk_index,
            item[1].chunk.chunk_id,
        )
    )

    return tuple(
        RerankedHit(
            chunk=hit.chunk,
            score=score,
            rank=rank,
            hybrid_rank=hit.rank,
            lexical_rank=hit.lexical_rank,
            semantic_rank=hit.semantic_rank,
        )
        for rank, (score, hit) in enumerate(scored[:limit], start=1)
    )


def _sentences(text: str) -> tuple[str, ...]:
    parts = tuple(
        match.group(0).strip()
        for match in _SENTENCE_RE.finditer(text)
        if match.group(0).strip()
    )
    if parts:
        return parts
    stripped = text.strip()
    return (stripped,) if stripped else ()


def _best_extractive_sentence(query: str, text: str) -> str:
    sentences = _sentences(text)
    if not sentences:
        return ""
    query_tokens = set(tokenize_text(query))
    best_index = 0
    best_overlap = -1
    for index, sentence in enumerate(sentences):
        sentence_tokens = set(tokenize_text(sentence))
        overlap = len(query_tokens.intersection(sentence_tokens))
        if overlap > best_overlap:
            best_overlap = overlap
            best_index = index
    return sentences[best_index]


def compress_context(
    query: str,
    hits: Sequence[RerankedHit],
    *,
    max_excerpts: int = DEFAULT_CONTEXT_EXCERPTS,
    max_excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    max_total_chars: int = DEFAULT_CONTEXT_CHARS,
) -> tuple[EvidenceExcerpt, ...]:
    """Select exact source excerpts under hard deterministic character budgets."""
    normalized_query = _validated_query(query)
    if not 1 <= max_excerpts <= MAX_CONTEXT_EXCERPTS:
        raise RerankingError(
            f"max_excerpts must be between 1 and {MAX_CONTEXT_EXCERPTS}"
        )
    if not 1 <= max_excerpt_chars <= MAX_EXCERPT_CHARS:
        raise RerankingError(
            f"max_excerpt_chars must be between 1 and {MAX_EXCERPT_CHARS}"
        )
    if not 1 <= max_total_chars <= MAX_CONTEXT_CHARS:
        raise RerankingError(
            f"max_total_chars must be between 1 and {MAX_CONTEXT_CHARS}"
        )

    candidates = tuple(hits)
    _validate_reranked_hits(candidates)
    excerpts: list[EvidenceExcerpt] = []
    used_chars = 0

    for hit in candidates:
        if len(excerpts) >= max_excerpts or used_chars >= max_total_chars:
            break
        selected = _best_extractive_sentence(normalized_query, hit.chunk.text)
        if not selected:
            continue
        remaining = max_total_chars - used_chars
        allowed = min(max_excerpt_chars, remaining)
        excerpt = selected[:allowed].rstrip()
        if not excerpt:
            break
        excerpts.append(
            EvidenceExcerpt(
                source_url=hit.chunk.source_url,
                chunk_id=hit.chunk.chunk_id,
                chunk_index=hit.chunk.chunk_index,
                rerank_rank=hit.rank,
                text=excerpt,
                char_count=len(excerpt),
            )
        )
        used_chars += len(excerpt)

    return tuple(excerpts)


def build_grounded_context(
    query: str,
    hits: Sequence[HybridHit],
    provider: RerankProvider,
    *,
    rerank_limit: int = DEFAULT_RERANK_LIMIT,
    max_excerpts: int = DEFAULT_CONTEXT_EXCERPTS,
    max_excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    max_total_chars: int = DEFAULT_CONTEXT_CHARS,
) -> tuple[EvidenceExcerpt, ...]:
    """Rerank bounded hybrid evidence then compress it without generation."""
    reranked = rerank_hybrid(
        query,
        hits,
        provider,
        limit=rerank_limit,
    )
    return compress_context(
        query,
        reranked,
        max_excerpts=max_excerpts,
        max_excerpt_chars=max_excerpt_chars,
        max_total_chars=max_total_chars,
    )

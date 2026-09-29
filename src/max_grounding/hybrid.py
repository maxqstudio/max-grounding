"""Deterministic bounded fusion of lexical and semantic retrieval hits."""

from __future__ import annotations

from collections.abc import Sequence

from .errors import RetrievalError
from .models import FetchedDocument, HybridHit, LexicalHit, SemanticHit, TextChunk
from .retrieval import (
    DEFAULT_CHUNK_WORDS,
    DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    DEFAULT_OVERLAP_WORDS,
    DEFAULT_RESULT_LIMIT,
    MAX_RESULTS,
    retrieve_lexical,
)
from .semantic import DEFAULT_EMBEDDING_BATCH, EmbeddingProvider, retrieve_semantic

RRF_K = 60


def _validate_ranked_hits(
    hits: Sequence[LexicalHit] | Sequence[SemanticHit],
    *,
    label: str,
) -> None:
    if len(hits) > MAX_RESULTS:
        raise RetrievalError(f"{label} hits must not exceed {MAX_RESULTS} items")

    seen_chunks: set[str] = set()
    for expected_rank, hit in enumerate(hits, start=1):
        if isinstance(hit.rank, bool) or hit.rank != expected_rank:
            raise RetrievalError(
                f"{label} hit ranks must be contiguous from 1 in result order"
            )
        chunk_id = hit.chunk.chunk_id
        if not chunk_id:
            raise RetrievalError(f"{label} chunk_id must be non-empty")
        if chunk_id in seen_chunks:
            raise RetrievalError(f"{label} hits contain a duplicate chunk_id")
        seen_chunks.add(chunk_id)


def fuse_hybrid(
    lexical_hits: Sequence[LexicalHit],
    semantic_hits: Sequence[SemanticHit],
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[HybridHit, ...]:
    """Fuse two bounded ranked lists with fixed equal-weight reciprocal ranks."""
    if not 1 <= limit <= MAX_RESULTS:
        raise RetrievalError(f"limit must be between 1 and {MAX_RESULTS}")

    lexical = tuple(lexical_hits)
    semantic = tuple(semantic_hits)
    _validate_ranked_hits(lexical, label="lexical")
    _validate_ranked_hits(semantic, label="semantic")

    chunks: dict[str, TextChunk] = {}
    lexical_ranks: dict[str, int] = {}
    semantic_ranks: dict[str, int] = {}

    for hit in lexical:
        chunks[hit.chunk.chunk_id] = hit.chunk
        lexical_ranks[hit.chunk.chunk_id] = hit.rank

    for hit in semantic:
        chunk_id = hit.chunk.chunk_id
        existing = chunks.get(chunk_id)
        if existing is not None and existing != hit.chunk:
            raise RetrievalError(
                "the same chunk_id has conflicting provenance across modalities"
            )
        chunks[chunk_id] = hit.chunk
        semantic_ranks[chunk_id] = hit.rank

    fused: list[tuple[float, TextChunk, int | None, int | None]] = []
    for chunk_id, item in chunks.items():
        lexical_rank = lexical_ranks.get(chunk_id)
        semantic_rank = semantic_ranks.get(chunk_id)
        score = 0.0
        if lexical_rank is not None:
            score += 1.0 / (RRF_K + lexical_rank)
        if semantic_rank is not None:
            score += 1.0 / (RRF_K + semantic_rank)
        fused.append((score, item, lexical_rank, semantic_rank))

    fused.sort(
        key=lambda item: (
            -item[0],
            item[1].source_url,
            item[1].chunk_index,
            item[1].chunk_id,
        )
    )

    return tuple(
        HybridHit(
            chunk=chunk,
            score=score,
            rank=rank,
            lexical_rank=lexical_rank,
            semantic_rank=semantic_rank,
        )
        for rank, (score, chunk, lexical_rank, semantic_rank) in enumerate(
            fused[:limit],
            start=1,
        )
    )


def retrieve_hybrid(
    documents: Sequence[FetchedDocument],
    query: str,
    provider: EmbeddingProvider,
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    max_chunks_per_document: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    limit: int = DEFAULT_RESULT_LIMIT,
    batch_size: int = DEFAULT_EMBEDDING_BATCH,
) -> tuple[HybridHit, ...]:
    """Run both bounded retrievers then fuse their top candidates deterministically."""
    if not 1 <= limit <= MAX_RESULTS:
        raise RetrievalError(f"limit must be between 1 and {MAX_RESULTS}")

    lexical_hits = retrieve_lexical(
        documents,
        query,
        max_words=max_words,
        overlap_words=overlap_words,
        max_chunks_per_document=max_chunks_per_document,
        limit=MAX_RESULTS,
    )
    semantic_hits = retrieve_semantic(
        documents,
        query,
        provider,
        max_words=max_words,
        overlap_words=overlap_words,
        max_chunks_per_document=max_chunks_per_document,
        limit=MAX_RESULTS,
        batch_size=batch_size,
    )
    return fuse_hybrid(
        lexical_hits,
        semantic_hits,
        limit=limit,
    )

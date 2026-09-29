"""Bounded provider-agnostic dense semantic retrieval."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from .errors import EmbeddingProviderError, RetrievalError
from .models import FetchedDocument, SemanticHit, TextChunk
from .retrieval import (
    DEFAULT_CHUNK_WORDS,
    DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    DEFAULT_OVERLAP_WORDS,
    DEFAULT_RESULT_LIMIT,
    MAX_DOCUMENTS,
    MAX_QUERY_CHARS,
    MAX_RESULTS,
    chunk_document,
)

MAX_SEMANTIC_CHUNKS = 256
MAX_EMBEDDING_BATCH = 64
MAX_EMBEDDING_DIMENSION = 4096
MAX_EMBEDDING_CALLS = 9
MAX_EMBEDDING_TEXTS = MAX_SEMANTIC_CHUNKS + 1

DEFAULT_EMBEDDING_BATCH = 32


class EmbeddingProvider(Protocol):
    """Provider boundary for model-specific embedding implementations."""

    def embed(
        self,
        texts: tuple[str, ...],
    ) -> Sequence[Sequence[float]]:
        """Return one dense vector per input text in the same order."""


def _validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise RetrievalError("query must be non-empty")
    if len(query) > MAX_QUERY_CHARS:
        raise RetrievalError(
            f"query must not exceed {MAX_QUERY_CHARS} characters"
        )
    return query.strip()


def _validate_batch_size(batch_size: int) -> None:
    if not 1 <= batch_size <= MAX_EMBEDDING_BATCH:
        raise RetrievalError(
            f"batch_size must be between 1 and {MAX_EMBEDDING_BATCH}"
        )


def _validated_vector(
    value: object,
    *,
    expected_dimension: int | None = None,
) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise EmbeddingProviderError("embedding vector must be a numeric sequence")
    try:
        raw = tuple(value)  # type: ignore[arg-type]
    except TypeError as exc:
        raise EmbeddingProviderError(
            "embedding vector must be a numeric sequence"
        ) from exc

    dimension = len(raw)
    if not 1 <= dimension <= MAX_EMBEDDING_DIMENSION:
        raise EmbeddingProviderError(
            f"embedding dimension must be between 1 and {MAX_EMBEDDING_DIMENSION}"
        )
    if expected_dimension is not None and dimension != expected_dimension:
        raise EmbeddingProviderError("embedding dimensions are inconsistent")

    vector: list[float] = []
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise EmbeddingProviderError(
                "embedding values must be finite real numbers"
            )
        number = float(item)
        if not math.isfinite(number):
            raise EmbeddingProviderError(
                "embedding values must be finite real numbers"
            )
        vector.append(number)

    norm_squared = math.fsum(number * number for number in vector)
    if not math.isfinite(norm_squared) or norm_squared <= 0.0:
        raise EmbeddingProviderError("embedding vectors must have non-zero finite norm")
    return tuple(vector)


def embed_bounded(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    batch_size: int = DEFAULT_EMBEDDING_BATCH,
) -> tuple[tuple[float, ...], ...]:
    """Embed bounded text batches and fail closed on malformed provider output."""
    _validate_batch_size(batch_size)
    values = tuple(texts)
    if len(values) > MAX_EMBEDDING_TEXTS:
        raise RetrievalError(
            f"embedding texts must not exceed {MAX_EMBEDDING_TEXTS} items"
        )
    if not values:
        return ()

    calls_required = math.ceil(len(values) / batch_size)
    if calls_required > MAX_EMBEDDING_CALLS:
        raise RetrievalError(
            f"embedding calls must not exceed {MAX_EMBEDDING_CALLS}"
        )

    vectors: list[tuple[float, ...]] = []
    dimension: int | None = None
    for start in range(0, len(values), batch_size):
        batch = values[start : start + batch_size]
        try:
            raw_batch = provider.embed(batch)
            returned = tuple(raw_batch)
        except Exception as exc:
            raise EmbeddingProviderError("embedding provider call failed") from exc

        if len(returned) != len(batch):
            raise EmbeddingProviderError(
                "embedding provider returned the wrong vector count"
            )

        for raw_vector in returned:
            vector = _validated_vector(
                raw_vector,
                expected_dimension=dimension,
            )
            if dimension is None:
                dimension = len(vector)
            vectors.append(vector)

    return tuple(vectors)


def build_semantic_chunks(
    documents: Sequence[FetchedDocument],
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    max_chunks_per_document: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
) -> tuple[TextChunk, ...]:
    """Build deterministic Phase 4 chunks under a Phase 5 total-work cap."""
    if len(documents) > MAX_DOCUMENTS:
        raise RetrievalError(
            f"documents must not exceed {MAX_DOCUMENTS} items"
        )

    chunks: list[TextChunk] = []
    for document in documents:
        chunks.extend(
            chunk_document(
                document,
                max_words=max_words,
                overlap_words=overlap_words,
                max_chunks=max_chunks_per_document,
            )
        )
        if len(chunks) > MAX_SEMANTIC_CHUNKS:
            raise RetrievalError(
                f"semantic chunks must not exceed {MAX_SEMANTIC_CHUNKS} items"
            )
    return tuple(chunks)


def cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    """Return cosine similarity for two validated non-zero dense vectors."""
    left_vector = _validated_vector(left)
    right_vector = _validated_vector(
        right,
        expected_dimension=len(left_vector),
    )
    dot = math.fsum(a * b for a, b in zip(left_vector, right_vector))
    left_norm = math.sqrt(math.fsum(a * a for a in left_vector))
    right_norm = math.sqrt(math.fsum(b * b for b in right_vector))
    score = dot / (left_norm * right_norm)
    if not math.isfinite(score):
        raise EmbeddingProviderError("cosine similarity is not finite")
    return score


def rank_semantic(
    query_vector: Sequence[float],
    chunks: Sequence[TextChunk],
    chunk_vectors: Sequence[Sequence[float]],
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[SemanticHit, ...]:
    """Rank chunks by positive cosine similarity with deterministic tie order."""
    if not 1 <= limit <= MAX_RESULTS:
        raise RetrievalError(f"limit must be between 1 and {MAX_RESULTS}")
    if len(chunks) != len(chunk_vectors):
        raise EmbeddingProviderError(
            "semantic chunk and vector counts do not match"
        )

    validated_query = _validated_vector(query_vector)
    dimension = len(validated_query)
    scored: list[tuple[float, TextChunk]] = []
    for chunk, raw_vector in zip(chunks, chunk_vectors):
        vector = _validated_vector(
            raw_vector,
            expected_dimension=dimension,
        )
        score = cosine_similarity(validated_query, vector)
        if score > 0.0:
            scored.append((score, chunk))

    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].source_url,
            item[1].chunk_index,
            item[1].chunk_id,
        )
    )
    return tuple(
        SemanticHit(chunk=chunk, score=score, rank=rank)
        for rank, (score, chunk) in enumerate(scored[:limit], start=1)
    )


def retrieve_semantic(
    documents: Sequence[FetchedDocument],
    query: str,
    provider: EmbeddingProvider,
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    max_chunks_per_document: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    limit: int = DEFAULT_RESULT_LIMIT,
    batch_size: int = DEFAULT_EMBEDDING_BATCH,
) -> tuple[SemanticHit, ...]:
    """Return bounded dense semantic hits over deterministic fetched-document chunks."""
    normalized_query = _validate_query(query)
    _validate_batch_size(batch_size)
    if not 1 <= limit <= MAX_RESULTS:
        raise RetrievalError(f"limit must be between 1 and {MAX_RESULTS}")

    chunks = build_semantic_chunks(
        documents,
        max_words=max_words,
        overlap_words=overlap_words,
        max_chunks_per_document=max_chunks_per_document,
    )
    if not chunks:
        return ()

    texts = (normalized_query,) + tuple(chunk.text for chunk in chunks)
    vectors = embed_bounded(
        provider,
        texts,
        batch_size=batch_size,
    )
    return rank_semantic(
        vectors[0],
        chunks,
        vectors[1:],
        limit=limit,
    )

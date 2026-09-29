"""Concrete Phase 11 persistent semantic indexing and retrieval."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Protocol

from .errors import (
    EmbeddingProviderError,
    PersistentIndexError,
    VectorStoreError,
)
from .models import (
    FetchedDocument,
    PersistentIndexResult,
    PersistentVectorHit,
    SemanticHit,
    TextChunk,
)
from .providers.ollama_embedding import (
    OLLAMA_EMBEDDING_DIMENSION,
    OLLAMA_MODEL,
)
from .providers.qdrant import (
    MAX_UPSERT_POINTS,
    QDRANT_SCHEMA_VERSION,
)
from .retrieval import (
    DEFAULT_CHUNK_WORDS,
    DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    DEFAULT_OVERLAP_WORDS,
    DEFAULT_RESULT_LIMIT,
    MAX_QUERY_CHARS,
    MAX_RESULTS,
)
from .semantic import (
    DEFAULT_EMBEDDING_BATCH,
    MAX_EMBEDDING_BATCH,
    MAX_EMBEDDING_DOCUMENTS,
    _validated_vector,
    build_semantic_chunks,
)

EMBEDDING_MODEL = OLLAMA_MODEL
EMBEDDING_DIMENSION = OLLAMA_EMBEDDING_DIMENSION
PERSISTENT_SCHEMA_VERSION = QDRANT_SCHEMA_VERSION


class ConcreteEmbeddingProvider(Protocol):
    model_name: str
    embedding_dimension: int

    def embed_query(self, text: str):
        ...

    def embed_documents(self, texts: tuple[str, ...]):
        ...


class PersistentVectorStore(Protocol):
    model_name: str
    embedding_dimension: int
    schema_version: int
    collection_name: str
    ensure_collection: Callable[[], None]
    upsert_chunks: Callable[[object, object], None]
    query_chunks: Callable[..., object]


def _provider_contract(provider: object) -> None:
    if getattr(provider, "model_name", None) != EMBEDDING_MODEL:
        raise PersistentIndexError("embedding provider model mismatch")
    if getattr(provider, "embedding_dimension", None) != EMBEDDING_DIMENSION:
        raise PersistentIndexError("embedding provider dimension mismatch")


def _store_contract(store: object) -> None:
    if getattr(store, "model_name", None) != EMBEDDING_MODEL:
        raise PersistentIndexError("vector store model mismatch")
    if getattr(store, "embedding_dimension", None) != EMBEDDING_DIMENSION:
        raise PersistentIndexError("vector store dimension mismatch")
    if getattr(store, "schema_version", None) != PERSISTENT_SCHEMA_VERSION:
        raise PersistentIndexError("vector store schema version mismatch")
    collection = getattr(store, "collection_name", None)
    if not isinstance(collection, str) or not collection:
        raise PersistentIndexError("vector store collection identity is invalid")


def _vector(value: object) -> tuple[float, ...]:
    try:
        return _validated_vector(
            value,
            expected_dimension=EMBEDDING_DIMENSION,
        )
    except EmbeddingProviderError as exc:
        raise PersistentIndexError(
            "concrete embedding vector is invalid"
        ) from exc


def embed_documents_concrete(
    provider: ConcreteEmbeddingProvider,
    texts: Sequence[str],
    *,
    batch_size: int = DEFAULT_EMBEDDING_BATCH,
) -> tuple[tuple[float, ...], ...]:
    """Embed bounded documents without fabricating a query embedding call."""
    _provider_contract(provider)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_EMBEDDING_BATCH:
        raise PersistentIndexError(
            f"batch_size must be between 1 and {MAX_EMBEDDING_BATCH}"
        )
    if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
        raise PersistentIndexError("document texts must be a bounded sequence")
    documents = tuple(texts)
    if len(documents) > MAX_EMBEDDING_DOCUMENTS:
        raise PersistentIndexError(
            f"document embeddings must not exceed {MAX_EMBEDDING_DOCUMENTS} items"
        )
    for text in documents:
        if not isinstance(text, str) or not text.strip():
            raise PersistentIndexError("document text must be non-empty")

    vectors: list[tuple[float, ...]] = []
    for start in range(0, len(documents), batch_size):
        batch = documents[start : start + batch_size]
        try:
            raw_returned = provider.embed_documents(batch)
        except Exception as exc:
            raise PersistentIndexError(
                "concrete document embedding provider failed"
            ) from exc
        if isinstance(raw_returned, (str, bytes)) or not isinstance(
            raw_returned,
            Sequence,
        ):
            raise PersistentIndexError(
                "document embedding provider must return a bounded sequence"
            )
        returned = tuple(raw_returned)
        if len(returned) != len(batch):
            raise PersistentIndexError(
                "document embedding provider returned the wrong vector count"
            )
        vectors.extend(_vector(item) for item in returned)
    return tuple(vectors)


def embed_query_concrete(
    provider: ConcreteEmbeddingProvider,
    query: str,
) -> tuple[float, ...]:
    """Embed one bounded query through the concrete provider role."""
    _provider_contract(provider)
    if not isinstance(query, str) or not query.strip():
        raise PersistentIndexError("query must be non-empty")
    if len(query) > MAX_QUERY_CHARS:
        raise PersistentIndexError(
            f"query must not exceed {MAX_QUERY_CHARS} characters"
        )
    try:
        raw = provider.embed_query(query.strip())
    except Exception as exc:
        raise PersistentIndexError(
            "concrete query embedding provider failed"
        ) from exc
    return _vector(raw)


def index_documents(
    documents: Sequence[FetchedDocument],
    provider: ConcreteEmbeddingProvider,
    store: PersistentVectorStore,
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    max_chunks_per_document: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    batch_size: int = DEFAULT_EMBEDDING_BATCH,
) -> PersistentIndexResult:
    """Persist deterministic chunks and concrete embeddings idempotently."""
    _provider_contract(provider)
    _store_contract(store)

    try:
        chunks = build_semantic_chunks(
            documents,
            max_words=max_words,
            overlap_words=overlap_words,
            max_chunks_per_document=max_chunks_per_document,
        )
    except Exception as exc:
        if isinstance(exc, PersistentIndexError):
            raise
        raise PersistentIndexError(
            "persistent chunk construction failed"
        ) from exc

    if not chunks:
        return PersistentIndexResult(
            chunks_indexed=0,
            vectors_indexed=0,
            embedding_model=EMBEDDING_MODEL,
            embedding_dimension=EMBEDDING_DIMENSION,
            schema_version=PERSISTENT_SCHEMA_VERSION,
            collection_name=store.collection_name,
        )

    vectors = embed_documents_concrete(
        provider,
        tuple(chunk.text for chunk in chunks),
        batch_size=batch_size,
    )
    try:
        store.ensure_collection()
        for start in range(0, len(chunks), MAX_UPSERT_POINTS):
            store.upsert_chunks(
                chunks[start : start + MAX_UPSERT_POINTS],
                vectors[start : start + MAX_UPSERT_POINTS],
            )
    except VectorStoreError as exc:
        raise PersistentIndexError("persistent vector write failed") from exc
    except Exception as exc:
        raise PersistentIndexError("persistent vector write failed") from exc

    return PersistentIndexResult(
        chunks_indexed=len(chunks),
        vectors_indexed=len(vectors),
        embedding_model=EMBEDDING_MODEL,
        embedding_dimension=EMBEDDING_DIMENSION,
        schema_version=PERSISTENT_SCHEMA_VERSION,
        collection_name=store.collection_name,
    )


def build_semantic_hits(
    matches: Sequence[PersistentVectorHit],
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[SemanticHit, ...]:
    """Validate persistent matches and reconstruct accepted SemanticHit values."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise PersistentIndexError(
            f"limit must be between 1 and {MAX_RESULTS}"
        )
    if isinstance(matches, (str, bytes)) or not isinstance(matches, Sequence):
        raise PersistentIndexError("persistent matches must be a bounded sequence")
    values = tuple(matches)
    if len(values) > MAX_RESULTS:
        raise PersistentIndexError(
            f"persistent matches must not exceed {MAX_RESULTS} items"
        )

    scored: list[tuple[float, TextChunk]] = []
    seen: set[tuple[str, str]] = set()
    for item in values:
        if not isinstance(item, PersistentVectorHit):
            raise PersistentIndexError(
                "persistent match must be PersistentVectorHit"
            )
        if (
            item.embedding_model != EMBEDDING_MODEL
            or item.embedding_dimension != EMBEDDING_DIMENSION
            or item.schema_version != PERSISTENT_SCHEMA_VERSION
        ):
            raise PersistentIndexError(
                "persistent match runtime schema mismatch"
            )
        if isinstance(item.score, bool) or not isinstance(item.score, (int, float)):
            raise PersistentIndexError("persistent similarity score must be finite")
        score = float(item.score)
        if not math.isfinite(score):
            raise PersistentIndexError("persistent similarity score must be finite")
        identity = (item.point_id, item.chunk.chunk_id)
        if identity in seen:
            raise PersistentIndexError("persistent result contains duplicate identity")
        seen.add(identity)
        if score > 0.0:
            scored.append((score, item.chunk))

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


def retrieve_persistent_semantic(
    query: str,
    provider: ConcreteEmbeddingProvider,
    store: PersistentVectorStore,
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[SemanticHit, ...]:
    """Query the concrete persistent semantic index without bypassing validation."""
    _provider_contract(provider)
    _store_contract(store)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise PersistentIndexError(
            f"limit must be between 1 and {MAX_RESULTS}"
        )
    query_vector = embed_query_concrete(provider, query)
    try:
        store.ensure_collection()
        matches = store.query_chunks(query_vector, limit=limit)
    except VectorStoreError as exc:
        raise PersistentIndexError("persistent vector query failed") from exc
    except Exception as exc:
        raise PersistentIndexError("persistent vector query failed") from exc
    return build_semantic_hits(matches, limit=limit)

"""Deterministic bounded in-memory lexical retrieval."""

from __future__ import annotations

import hashlib
import math
import unicodedata
from collections import Counter
from collections.abc import Sequence

from .errors import RetrievalError
from .models import FetchedDocument, LexicalHit, TextChunk

MAX_DOCUMENTS = 20
MAX_QUERY_CHARS = 4096
MAX_CHUNK_WORDS = 512
MAX_CHUNKS_PER_DOCUMENT = 128
MAX_RESULTS = 20

DEFAULT_CHUNK_WORDS = 160
DEFAULT_OVERLAP_WORDS = 32
DEFAULT_MAX_CHUNKS_PER_DOCUMENT = 64
DEFAULT_RESULT_LIMIT = 5

_BM25_K1 = 1.5
_BM25_B = 0.75


def tokenize_text(text: str) -> tuple[str, ...]:
    """Return deterministic Unicode alphanumeric lexical tokens."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    tokens: list[str] = []
    current: list[str] = []
    for character in normalized:
        if character.isalnum():
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current.clear()
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _validate_chunk_bounds(
    *,
    max_words: int,
    overlap_words: int,
    max_chunks: int,
) -> None:
    if not 1 <= max_words <= MAX_CHUNK_WORDS:
        raise RetrievalError(
            f"max_words must be between 1 and {MAX_CHUNK_WORDS}"
        )
    if not 0 <= overlap_words < max_words:
        raise RetrievalError("overlap_words must be at least 0 and less than max_words")
    if not 1 <= max_chunks <= MAX_CHUNKS_PER_DOCUMENT:
        raise RetrievalError(
            f"max_chunks must be between 1 and {MAX_CHUNKS_PER_DOCUMENT}"
        )


def _chunk_id(source_url: str, chunk_index: int, text: str) -> str:
    payload = f"{source_url}\0{chunk_index}\0{text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def chunk_document(
    document: FetchedDocument,
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    max_chunks: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
) -> tuple[TextChunk, ...]:
    """Split one fetched document into deterministic bounded overlapping chunks."""
    _validate_chunk_bounds(
        max_words=max_words,
        overlap_words=overlap_words,
        max_chunks=max_chunks,
    )
    words = document.text.split()
    if not words:
        return ()

    chunks: list[TextChunk] = []
    step = max_words - overlap_words
    start = 0
    while start < len(words) and len(chunks) < max_chunks:
        selected = words[start : start + max_words]
        text = " ".join(selected)
        index = len(chunks)
        chunks.append(
            TextChunk(
                chunk_id=_chunk_id(document.url, index, text),
                source_url=document.url,
                chunk_index=index,
                text=text,
                token_count=len(tokenize_text(text)),
            )
        )
        if start + max_words >= len(words):
            break
        start += step

    return tuple(chunks)


def _validate_query(query: str) -> tuple[str, ...]:
    if not isinstance(query, str) or not query.strip():
        raise RetrievalError("query must be non-empty")
    if len(query) > MAX_QUERY_CHARS:
        raise RetrievalError(
            f"query must not exceed {MAX_QUERY_CHARS} characters"
        )
    tokens = tokenize_text(query)
    if not tokens:
        raise RetrievalError("query must contain lexical tokens")
    return tuple(dict.fromkeys(tokens))


def rank_chunks(
    query: str,
    chunks: Sequence[TextChunk],
    *,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[LexicalHit, ...]:
    """Rank chunks with deterministic BM25 and return positive-score hits only."""
    query_terms = _validate_query(query)
    if not 1 <= limit <= MAX_RESULTS:
        raise RetrievalError(f"limit must be between 1 and {MAX_RESULTS}")
    if not chunks:
        return ()

    tokenized: list[tuple[TextChunk, tuple[str, ...]]] = [
        (chunk, tokenize_text(chunk.text)) for chunk in chunks
    ]
    total_length = sum(len(tokens) for _, tokens in tokenized)
    if total_length == 0:
        return ()

    document_count = len(tokenized)
    average_length = total_length / document_count
    document_frequency = {
        term: sum(1 for _, tokens in tokenized if term in tokens)
        for term in query_terms
    }

    scored: list[tuple[float, TextChunk]] = []
    for chunk, tokens in tokenized:
        frequencies = Counter(tokens)
        length = len(tokens)
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if frequency == 0:
                continue
            df = document_frequency[term]
            idf = math.log(
                1.0
                + (document_count - df + 0.5) / (df + 0.5)
            )
            denominator = frequency + _BM25_K1 * (
                1.0 - _BM25_B + _BM25_B * length / average_length
            )
            score += idf * frequency * (_BM25_K1 + 1.0) / denominator
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
        LexicalHit(chunk=chunk, score=score, rank=rank)
        for rank, (score, chunk) in enumerate(scored[:limit], start=1)
    )


def retrieve_lexical(
    documents: Sequence[FetchedDocument],
    query: str,
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    overlap_words: int = DEFAULT_OVERLAP_WORDS,
    max_chunks_per_document: int = DEFAULT_MAX_CHUNKS_PER_DOCUMENT,
    limit: int = DEFAULT_RESULT_LIMIT,
) -> tuple[LexicalHit, ...]:
    """Chunk fetched documents and return bounded deterministic lexical hits."""
    _validate_query(query)
    _validate_chunk_bounds(
        max_words=max_words,
        overlap_words=overlap_words,
        max_chunks=max_chunks_per_document,
    )
    if not 1 <= limit <= MAX_RESULTS:
        raise RetrievalError(f"limit must be between 1 and {MAX_RESULTS}")
    if len(documents) > MAX_DOCUMENTS:
        raise RetrievalError(
            f"documents must not exceed {MAX_DOCUMENTS} items"
        )
    if not documents:
        return ()

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
    return rank_chunks(query, chunks, limit=limit)

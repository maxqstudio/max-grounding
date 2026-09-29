from __future__ import annotations

import unittest

from max_grounding.errors import PersistentIndexError
from max_grounding.models import (
    FetchedDocument,
    PersistentVectorHit,
    TextChunk,
)
from max_grounding.persistent import (
    EMBEDDING_DIMENSION,
    EMBEDDING_MODEL,
    build_semantic_hits,
    embed_documents_concrete,
    embed_query_concrete,
    index_documents,
    retrieve_persistent_semantic,
)


def vector(axis: int = 0) -> tuple[float, ...]:
    values = [0.0] * EMBEDDING_DIMENSION
    values[axis] = 1.0
    return tuple(values)


def document(url: str, text: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


class _Provider:
    model_name = EMBEDDING_MODEL
    embedding_dimension = EMBEDDING_DIMENSION

    def __init__(self) -> None:
        self.query_calls: list[str] = []
        self.document_calls: list[tuple[str, ...]] = []

    def embed_query(self, text: str):
        self.query_calls.append(text)
        return vector(0)

    def embed_documents(self, texts: tuple[str, ...]):
        self.document_calls.append(texts)
        return tuple(vector(index % 2) for index, _ in enumerate(texts))


class _Store:
    model_name = EMBEDDING_MODEL
    embedding_dimension = EMBEDDING_DIMENSION
    schema_version = 1
    collection_name = "test_collection"

    def __init__(self) -> None:
        self.ensure_calls = 0
        self.upserts = []
        self.queries = []
        self.matches: tuple[PersistentVectorHit, ...] = ()

    def ensure_collection(self) -> None:
        self.ensure_calls += 1

    def upsert_chunks(self, chunks, vectors) -> None:
        self.upserts.append((tuple(chunks), tuple(vectors)))

    def query_chunks(self, query_vector, *, limit):
        self.queries.append((tuple(query_vector), limit))
        return self.matches


class PersistentSemanticTests(unittest.TestCase):
    def test_document_embedding_path_never_calls_query_embedding(self) -> None:
        provider = _Provider()
        result = embed_documents_concrete(
            provider,
            ("alpha", "beta"),
            batch_size=1,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual(provider.query_calls, [])
        self.assertEqual(provider.document_calls, [("alpha",), ("beta",)])

    def test_query_embedding_path_never_calls_document_embedding(self) -> None:
        provider = _Provider()
        result = embed_query_concrete(provider, "gold")

        self.assertEqual(len(result), EMBEDDING_DIMENSION)
        self.assertEqual(provider.query_calls, ["gold"])
        self.assertEqual(provider.document_calls, [])

    def test_index_documents_builds_chunks_embeds_then_persists_provenance(self) -> None:
        provider = _Provider()
        store = _Store()
        docs = (
            document(
                "https://example.com/gold",
                "central bank gold reserves increase while inflation stays elevated",
            ),
        )

        result = index_documents(
            docs,
            provider,
            store,
            max_words=4,
            overlap_words=1,
            max_chunks_per_document=4,
            batch_size=2,
        )

        self.assertGreater(result.chunks_indexed, 0)
        self.assertEqual(result.chunks_indexed, result.vectors_indexed)
        self.assertEqual(result.embedding_model, EMBEDDING_MODEL)
        self.assertEqual(result.embedding_dimension, EMBEDDING_DIMENSION)
        self.assertEqual(result.collection_name, store.collection_name)
        self.assertEqual(provider.query_calls, [])
        self.assertEqual(store.ensure_calls, 1)
        self.assertEqual(len(store.upserts), 1)
        chunks, vectors = store.upserts[0]
        self.assertEqual(len(chunks), len(vectors))
        self.assertTrue(all(chunk.source_url == docs[0].url for chunk in chunks))

    def test_empty_index_is_noop_without_remote_calls(self) -> None:
        provider = _Provider()
        store = _Store()
        result = index_documents((), provider, store)

        self.assertEqual(result.chunks_indexed, 0)
        self.assertEqual(provider.document_calls, [])
        self.assertEqual(store.ensure_calls, 0)
        self.assertEqual(store.upserts, [])

    def test_retrieve_persistent_semantic_uses_query_embedding_and_store(self) -> None:
        provider = _Provider()
        store = _Store()
        chunk = TextChunk(
            chunk_id="a" * 64,
            source_url="https://example.com/gold",
            chunk_index=0,
            text="central bank gold reserves",
            token_count=4,
        )
        store.matches = (
            PersistentVectorHit(
                point_id="point-a",
                chunk=chunk,
                score=0.91,
                embedding_model=EMBEDDING_MODEL,
                embedding_dimension=EMBEDDING_DIMENSION,
                schema_version=1,
            ),
        )

        hits = retrieve_persistent_semantic(
            "central bank gold",
            provider,
            store,
            limit=5,
        )

        self.assertEqual(provider.query_calls, ["central bank gold"])
        self.assertEqual(provider.document_calls, [])
        self.assertEqual(store.ensure_calls, 1)
        self.assertEqual(store.queries, [(vector(0), 5)])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk, chunk)
        self.assertEqual(hits[0].rank, 1)
        self.assertAlmostEqual(hits[0].score, 0.91)

    def test_build_semantic_hits_filters_nonpositive_and_stabilizes_ties(self) -> None:
        a = TextChunk(
            chunk_id="a" * 64,
            source_url="https://example.com/a",
            chunk_index=0,
            text="a",
            token_count=1,
        )
        b = TextChunk(
            chunk_id="b" * 64,
            source_url="https://example.com/b",
            chunk_index=0,
            text="b",
            token_count=1,
        )
        matches = (
            PersistentVectorHit(
                point_id="b",
                chunk=b,
                score=0.5,
                embedding_model=EMBEDDING_MODEL,
                embedding_dimension=EMBEDDING_DIMENSION,
                schema_version=1,
            ),
            PersistentVectorHit(
                point_id="a",
                chunk=a,
                score=0.5,
                embedding_model=EMBEDDING_MODEL,
                embedding_dimension=EMBEDDING_DIMENSION,
                schema_version=1,
            ),
            PersistentVectorHit(
                point_id="zero",
                chunk=a,
                score=0.0,
                embedding_model=EMBEDDING_MODEL,
                embedding_dimension=EMBEDDING_DIMENSION,
                schema_version=1,
            ),
        )

        hits = build_semantic_hits(matches, limit=2)

        self.assertEqual(
            tuple(hit.chunk.source_url for hit in hits),
            ("https://example.com/a", "https://example.com/b"),
        )
        self.assertEqual(tuple(hit.rank for hit in hits), (1, 2))

    def test_provider_store_model_dimension_and_schema_mismatch_fail_closed(self) -> None:
        provider = _Provider()
        store = _Store()
        docs = (document("https://example.com/a", "alpha beta gamma"),)

        for attr, value in (
            ("model_name", "other-model"),
            ("embedding_dimension", 512),
            ("schema_version", 2),
        ):
            with self.subTest(attr=attr):
                broken = _Store()
                setattr(broken, attr, value)
                with self.assertRaises(PersistentIndexError):
                    index_documents(docs, provider, broken)

    def test_bad_vectors_matches_query_and_bounds_fail_closed(self) -> None:
        class BadProvider(_Provider):
            def embed_query(self, text: str):
                return (1.0, 2.0)

        with self.assertRaises(PersistentIndexError):
            embed_query_concrete(BadProvider(), "query")

        provider = _Provider()
        store = _Store()
        bad_chunk = TextChunk(
            chunk_id="a" * 64,
            source_url="https://example.com/a",
            chunk_index=0,
            text="a",
            token_count=1,
        )
        store.matches = (
            PersistentVectorHit(
                point_id="a",
                chunk=bad_chunk,
                score=float("nan"),
                embedding_model=EMBEDDING_MODEL,
                embedding_dimension=EMBEDDING_DIMENSION,
                schema_version=1,
            ),
        )
        with self.assertRaises(PersistentIndexError):
            retrieve_persistent_semantic("query", provider, store)
        with self.assertRaises(PersistentIndexError):
            retrieve_persistent_semantic("", provider, _Store())
        with self.assertRaises(PersistentIndexError):
            retrieve_persistent_semantic("query", provider, _Store(), limit=21)


if __name__ == "__main__":
    unittest.main()

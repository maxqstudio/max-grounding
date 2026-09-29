from __future__ import annotations

import math
import unittest

from max_grounding.errors import EmbeddingProviderError, RetrievalError
from max_grounding.models import FetchedDocument, TextChunk
from max_grounding.semantic import (
    MAX_EMBEDDING_CALLS,
    build_semantic_chunks,
    cosine_similarity,
    embed_bounded,
    rank_semantic,
    retrieve_semantic,
)


def document(url: str, text: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


def chunk(url: str, index: int, text: str) -> TextChunk:
    return TextChunk(
        chunk_id=f"{url}#{index}",
        source_url=url,
        chunk_index=index,
        text=text,
        token_count=len(text.split()),
    )


def keyword_vector(text: str) -> tuple[float, ...]:
    lowered = text.casefold()
    if "gold" in lowered or "precious metal" in lowered:
        return (1.0, 0.1, 0.0)
    if "central bank" in lowered or "inflation" in lowered:
        return (0.8, 0.2, 0.0)
    return (0.0, 1.0, 0.1)


class KeywordEmbeddingProvider:
    def __init__(self) -> None:
        self.query_calls: list[str] = []
        self.document_calls: list[tuple[str, ...]] = []

    def embed_query(self, text: str) -> tuple[float, ...]:
        self.query_calls.append(text)
        return keyword_vector(text)

    def embed_documents(
        self,
        texts: tuple[str, ...],
    ) -> tuple[tuple[float, ...], ...]:
        self.document_calls.append(texts)
        return tuple(keyword_vector(text) for text in texts)


class FixedProvider:
    def __init__(self, query_result: object, document_batches: list[object]) -> None:
        self.query_result = query_result
        self.document_batches = list(document_batches)
        self.query_calls: list[str] = []
        self.document_calls: list[tuple[str, ...]] = []

    def embed_query(self, text: str):
        self.query_calls.append(text)
        if isinstance(self.query_result, BaseException):
            raise self.query_result
        return self.query_result

    def embed_documents(self, texts: tuple[str, ...]):
        self.document_calls.append(texts)
        value = self.document_batches.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class SemanticRetrievalTests(unittest.TestCase):
    def test_cosine_similarity_is_deterministic(self) -> None:
        self.assertAlmostEqual(cosine_similarity((1.0, 0.0), (1.0, 0.0)), 1.0)
        self.assertAlmostEqual(cosine_similarity((1.0, 0.0), (0.0, 1.0)), 0.0)
        self.assertAlmostEqual(
            cosine_similarity((1.0, 1.0), (1.0, 0.0)),
            1.0 / math.sqrt(2.0),
        )

    def test_query_and_documents_use_distinct_embedding_roles(self) -> None:
        provider = KeywordEmbeddingProvider()
        query_vector, document_vectors = embed_bounded(
            provider,
            "precious metal demand",
            ("gold market", "football match"),
            batch_size=32,
        )
        self.assertEqual(query_vector, keyword_vector("precious metal demand"))
        self.assertEqual(len(document_vectors), 2)
        self.assertEqual(provider.query_calls, ["precious metal demand"])
        self.assertEqual(
            provider.document_calls,
            [("gold market", "football match")],
        )

    def test_retrieve_semantic_preserves_provenance_and_ranks_meaning(self) -> None:
        docs = (
            document(
                "https://example.com/gold",
                "Gold demand rises while central bank purchases remain elevated.",
            ),
            document(
                "https://example.com/sport",
                "The football team won the evening match.",
            ),
        )
        provider = KeywordEmbeddingProvider()

        hits = retrieve_semantic(
            docs,
            "precious metal demand",
            provider,
            max_words=32,
            overlap_words=4,
            max_chunks_per_document=8,
            limit=2,
        )

        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.source_url, "https://example.com/gold")
        self.assertGreater(hits[0].score, 0.0)
        self.assertEqual(tuple(hit.rank for hit in hits), tuple(range(1, len(hits) + 1)))
        self.assertEqual(provider.query_calls, ["precious metal demand"])
        self.assertTrue(provider.document_calls)

    def test_embedding_calls_are_batched_and_bounded(self) -> None:
        provider = KeywordEmbeddingProvider()
        texts = tuple(f"gold text {i}" for i in range(70))
        query_vector, vectors = embed_bounded(
            provider,
            "gold query",
            texts,
            batch_size=32,
        )
        self.assertEqual(query_vector, keyword_vector("gold query"))
        self.assertEqual(len(vectors), 70)
        self.assertEqual(
            tuple(len(call) for call in provider.document_calls),
            (32, 32, 6),
        )
        self.assertEqual(len(provider.query_calls), 1)
        self.assertLessEqual(
            len(provider.query_calls) + len(provider.document_calls),
            MAX_EMBEDDING_CALLS,
        )

    def test_embedding_call_cap_rejects_before_provider_work(self) -> None:
        provider = KeywordEmbeddingProvider()
        texts = tuple(f"text {i}" for i in range(256))
        with self.assertRaises(RetrievalError):
            embed_bounded(provider, "query", texts, batch_size=31)
        self.assertEqual(provider.query_calls, [])
        self.assertEqual(provider.document_calls, [])

    def test_query_provider_exception_is_wrapped_fail_closed(self) -> None:
        provider = FixedProvider(RuntimeError("provider down"), [])
        with self.assertRaises(EmbeddingProviderError):
            embed_bounded(provider, "query", (), batch_size=32)

    def test_document_provider_exception_is_wrapped_fail_closed(self) -> None:
        provider = FixedProvider((1.0, 0.0), [RuntimeError("provider down")])
        with self.assertRaises(EmbeddingProviderError):
            embed_bounded(provider, "query", ("doc",), batch_size=32)

    def test_rejects_wrong_vector_count(self) -> None:
        provider = FixedProvider((1.0, 0.0), [((1.0, 0.0),)])
        with self.assertRaises(EmbeddingProviderError):
            embed_bounded(provider, "query", ("a", "b"), batch_size=32)

    def test_rejects_inconsistent_dimensions_across_batches(self) -> None:
        provider = FixedProvider(
            (1.0, 0.0),
            [
                tuple((1.0, 0.0) for _ in range(2)),
                ((1.0, 0.0, 0.0),),
            ],
        )
        with self.assertRaises(EmbeddingProviderError):
            embed_bounded(
                provider,
                "query",
                ("a", "b", "c"),
                batch_size=2,
            )

    def test_rejects_non_finite_boolean_zero_and_oversized_vectors(self) -> None:
        bad_query_vectors = [
            (float("nan"), 1.0),
            (float("inf"), 1.0),
            (True, 1.0),
            (0.0, 0.0),
            tuple(1.0 for _ in range(4097)),
        ]
        for vector in bad_query_vectors:
            with self.subTest(vector_len=len(vector)):
                provider = FixedProvider(vector, [])
                with self.assertRaises(EmbeddingProviderError):
                    embed_bounded(provider, "query", (), batch_size=32)

    def test_rank_semantic_returns_positive_hits_with_stable_ties(self) -> None:
        chunks = (
            chunk("https://example.com/b", 0, "b"),
            chunk("https://example.com/a", 0, "a"),
            chunk("https://example.com/negative", 0, "negative"),
        )
        vectors = (
            (1.0, 0.0),
            (1.0, 0.0),
            (-1.0, 0.0),
        )
        hits = rank_semantic(
            (1.0, 0.0),
            chunks,
            vectors,
            limit=3,
        )
        self.assertEqual(
            tuple(hit.chunk.source_url for hit in hits),
            ("https://example.com/a", "https://example.com/b"),
        )
        self.assertEqual(tuple(hit.rank for hit in hits), (1, 2))

    def test_build_semantic_chunks_rejects_total_chunk_overflow(self) -> None:
        docs = tuple(
            document(
                f"https://example.com/{i}",
                " ".join(f"w{n}" for n in range(100)),
            )
            for i in range(3)
        )
        with self.assertRaises(RetrievalError):
            build_semantic_chunks(
                docs,
                max_words=1,
                overlap_words=0,
                max_chunks_per_document=100,
            )

    def test_invalid_request_bounds_fail_before_provider(self) -> None:
        provider = KeywordEmbeddingProvider()
        doc = document("https://example.com/a", "gold market")
        cases = [
            lambda: retrieve_semantic((doc,), "   ", provider),
            lambda: retrieve_semantic((doc,), "x" * 4097, provider),
            lambda: retrieve_semantic((doc,), "gold", provider, limit=0),
            lambda: retrieve_semantic((doc,), "gold", provider, batch_size=0),
            lambda: retrieve_semantic((doc,), "gold", provider, batch_size=65),
        ]
        for invoke in cases:
            with self.subTest(invoke=invoke):
                with self.assertRaises(RetrievalError):
                    invoke()
        self.assertEqual(provider.query_calls, [])
        self.assertEqual(provider.document_calls, [])

    def test_repeated_retrieval_is_deterministic_for_deterministic_provider(self) -> None:
        docs = (
            document("https://example.com/a", "gold market outlook"),
            document("https://example.com/b", "central bank policy"),
        )
        first = retrieve_semantic(
            docs,
            "gold outlook",
            KeywordEmbeddingProvider(),
            limit=2,
        )
        second = retrieve_semantic(
            docs,
            "gold outlook",
            KeywordEmbeddingProvider(),
            limit=2,
        )
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

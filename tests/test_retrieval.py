from __future__ import annotations

import unittest

from max_grounding.errors import RetrievalError
from max_grounding.models import FetchedDocument, TextChunk
from max_grounding.retrieval import (
    chunk_document,
    rank_chunks,
    retrieve_lexical,
    tokenize_text,
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
        token_count=len(tokenize_text(text)),
    )


class LexicalRetrievalTests(unittest.TestCase):
    def test_tokenizer_is_unicode_casefolded_and_punctuation_stable(self) -> None:
        self.assertEqual(
            tokenize_text("Gold-price GOLD! Café_42"),
            ("gold", "price", "gold", "café", "42"),
        )

    def test_chunking_is_bounded_overlapping_and_deterministic(self) -> None:
        doc = document(
            "https://example.com/a",
            "one two three four five six seven eight",
        )
        first = chunk_document(
            doc,
            max_words=3,
            overlap_words=1,
            max_chunks=10,
        )
        second = chunk_document(
            doc,
            max_words=3,
            overlap_words=1,
            max_chunks=10,
        )

        self.assertEqual(
            tuple(item.text for item in first),
            (
                "one two three",
                "three four five",
                "five six seven",
                "seven eight",
            ),
        )
        self.assertEqual(first, second)
        self.assertEqual(tuple(item.chunk_index for item in first), (0, 1, 2, 3))
        self.assertTrue(all(item.source_url == doc.url for item in first))
        self.assertTrue(all(item.chunk_id for item in first))

    def test_chunk_limit_stops_work_deterministically(self) -> None:
        doc = document(
            "https://example.com/limit",
            "one two three four five six seven eight nine ten",
        )
        chunks = chunk_document(
            doc,
            max_words=3,
            overlap_words=1,
            max_chunks=2,
        )
        self.assertEqual(
            tuple(item.text for item in chunks),
            ("one two three", "three four five"),
        )

    def test_rank_chunks_returns_only_positive_matches(self) -> None:
        chunks = (
            chunk("https://example.com/a", 0, "gold price inflation outlook"),
            chunk("https://example.com/b", 0, "football match schedule"),
            chunk("https://example.com/c", 0, "inflation data and gold demand"),
        )
        hits = rank_chunks("gold inflation", chunks, limit=3)

        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].chunk.source_url, "https://example.com/a")
        self.assertGreater(hits[0].score, 0.0)
        self.assertEqual(tuple(hit.rank for hit in hits), (1, 2))
        self.assertNotIn(
            "https://example.com/b",
            {hit.chunk.source_url for hit in hits},
        )

    def test_equal_scores_use_stable_provenance_order(self) -> None:
        chunks = (
            chunk("https://example.com/b", 0, "gold"),
            chunk("https://example.com/a", 0, "gold"),
        )
        hits = rank_chunks("gold", chunks, limit=2)
        self.assertEqual(
            tuple(hit.chunk.source_url for hit in hits),
            ("https://example.com/a", "https://example.com/b"),
        )

    def test_no_lexical_match_returns_empty_result(self) -> None:
        chunks = (chunk("https://example.com/a", 0, "copper market"),)
        self.assertEqual(rank_chunks("gold", chunks, limit=5), ())

    def test_retrieve_lexical_preserves_document_provenance(self) -> None:
        docs = (
            document(
                "https://example.com/gold",
                "Gold demand rises. Central banks buy gold. Inflation remains elevated.",
            ),
            document(
                "https://example.com/sport",
                "The football team won the evening match.",
            ),
        )
        before = docs
        hits = retrieve_lexical(
            docs,
            "central bank gold",
            max_words=6,
            overlap_words=1,
            max_chunks_per_document=8,
            limit=3,
        )

        self.assertIs(docs, before)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.source_url, "https://example.com/gold")
        self.assertLessEqual(len(hits), 3)

    def test_rejects_invalid_query_and_bounds_before_retrieval(self) -> None:
        doc = document("https://example.com/a", "gold market")
        cases = [
            lambda: retrieve_lexical((doc,), "   "),
            lambda: retrieve_lexical((doc,), "gold", max_words=0),
            lambda: retrieve_lexical((doc,), "gold", max_words=3, overlap_words=3),
            lambda: retrieve_lexical((doc,), "gold", max_chunks_per_document=0),
            lambda: retrieve_lexical((doc,), "gold", limit=0),
            lambda: rank_chunks("gold", (), limit=0),
            lambda: chunk_document(doc, max_words=3, overlap_words=-1),
        ]
        for invoke in cases:
            with self.subTest(invoke=invoke):
                with self.assertRaises(RetrievalError):
                    invoke()

    def test_hard_caps_reject_unbounded_internal_requests(self) -> None:
        doc = document("https://example.com/a", "gold")
        too_many_documents = tuple(doc for _ in range(21))
        with self.assertRaises(RetrievalError):
            retrieve_lexical(too_many_documents, "gold")
        with self.assertRaises(RetrievalError):
            retrieve_lexical((doc,), "x" * 4097)
        with self.assertRaises(RetrievalError):
            chunk_document(doc, max_words=513)
        with self.assertRaises(RetrievalError):
            chunk_document(doc, max_chunks=129)
        with self.assertRaises(RetrievalError):
            rank_chunks("gold", (), limit=21)


if __name__ == "__main__":
    unittest.main()

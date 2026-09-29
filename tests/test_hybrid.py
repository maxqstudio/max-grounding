from __future__ import annotations

import math
import unittest

from max_grounding.errors import RetrievalError
from max_grounding.hybrid import RRF_K, fuse_hybrid, retrieve_hybrid
from max_grounding.models import (
    FetchedDocument,
    LexicalHit,
    SemanticHit,
    TextChunk,
)


def chunk(url: str, index: int, text: str, *, chunk_id: str | None = None) -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id or f"{url}#{index}",
        source_url=url,
        chunk_index=index,
        text=text,
        token_count=len(text.split()),
    )


def lexical(item: TextChunk, rank: int, score: float = 1.0) -> LexicalHit:
    return LexicalHit(chunk=item, score=score, rank=rank)


def semantic(item: TextChunk, rank: int, score: float = 0.9) -> SemanticHit:
    return SemanticHit(chunk=item, score=score, rank=rank)


def document(url: str, text: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


class _EmbeddingProvider:
    def embed_query(self, text: str):
        return (1.0, 0.0)

    def embed_documents(self, texts: tuple[str, ...]):
        return tuple(
            (1.0, 0.0) if "central" in text.casefold() else (0.0, 1.0)
            for text in texts
        )


class HybridFusionTests(unittest.TestCase):
    def test_dual_modality_hit_accumulates_fixed_rrf_score(self) -> None:
        both = chunk("https://example.com/a", 0, "gold central bank", chunk_id="a")
        lexical_only = chunk("https://example.com/b", 0, "gold", chunk_id="b")

        hits = fuse_hybrid(
            (lexical(both, 1), lexical(lexical_only, 2)),
            (semantic(both, 1),),
            limit=2,
        )

        self.assertEqual(tuple(hit.chunk.chunk_id for hit in hits), ("a", "b"))
        self.assertAlmostEqual(
            hits[0].score,
            2.0 / (RRF_K + 1),
        )
        self.assertEqual(hits[0].lexical_rank, 1)
        self.assertEqual(hits[0].semantic_rank, 1)
        self.assertEqual(hits[1].semantic_rank, None)
        self.assertEqual(tuple(hit.rank for hit in hits), (1, 2))

    def test_equal_fused_scores_use_stable_provenance_order(self) -> None:
        a = chunk("https://example.com/a", 0, "alpha", chunk_id="a")
        b = chunk("https://example.com/b", 0, "beta", chunk_id="b")
        hits = fuse_hybrid(
            (lexical(b, 1),),
            (semantic(a, 1),),
            limit=2,
        )
        self.assertEqual(
            tuple(hit.chunk.source_url for hit in hits),
            ("https://example.com/a", "https://example.com/b"),
        )

    def test_rejects_non_contiguous_duplicate_and_over_limit_ranks(self) -> None:
        a = chunk("https://example.com/a", 0, "a", chunk_id="a")
        b = chunk("https://example.com/b", 0, "b", chunk_id="b")
        cases = [
            lambda: fuse_hybrid((lexical(a, 2),), (), limit=1),
            lambda: fuse_hybrid((lexical(a, 1), lexical(b, 1)), (), limit=2),
            lambda: fuse_hybrid(tuple(lexical(a, i + 1) for i in range(21)), (), limit=20),
            lambda: fuse_hybrid((), (), limit=0),
            lambda: fuse_hybrid((), (), limit=21),
        ]
        for invoke in cases:
            with self.subTest(invoke=invoke):
                with self.assertRaises(RetrievalError):
                    invoke()

    def test_rejects_duplicate_chunk_within_one_modality(self) -> None:
        a = chunk("https://example.com/a", 0, "a", chunk_id="same")
        with self.assertRaises(RetrievalError):
            fuse_hybrid(
                (lexical(a, 1), lexical(a, 2)),
                (),
                limit=2,
            )

    def test_rejects_cross_modality_chunk_identity_conflict(self) -> None:
        lexical_chunk = chunk(
            "https://example.com/a",
            0,
            "trusted text",
            chunk_id="same",
        )
        semantic_chunk = chunk(
            "https://example.com/b",
            0,
            "different text",
            chunk_id="same",
        )
        with self.assertRaises(RetrievalError):
            fuse_hybrid(
                (lexical(lexical_chunk, 1),),
                (semantic(semantic_chunk, 1),),
                limit=2,
            )

    def test_empty_modalities_return_empty_tuple(self) -> None:
        self.assertEqual(fuse_hybrid((), (), limit=5), ())

    def test_retrieve_hybrid_combines_real_lexical_and_semantic_rankers(self) -> None:
        docs = (
            document(
                "https://example.com/gold",
                "Central banks buy gold while inflation remains elevated.",
            ),
            document(
                "https://example.com/sport",
                "The football club won the evening match.",
            ),
        )
        hits = retrieve_hybrid(
            docs,
            "central bank gold",
            _EmbeddingProvider(),
            max_words=8,
            overlap_words=1,
            max_chunks_per_document=4,
            limit=4,
            batch_size=2,
        )

        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.source_url, "https://example.com/gold")
        self.assertIsNotNone(hits[0].lexical_rank)
        self.assertIsNotNone(hits[0].semantic_rank)
        self.assertLessEqual(len(hits), 4)
        self.assertTrue(all(math.isfinite(hit.score) and hit.score > 0 for hit in hits))


if __name__ == "__main__":
    unittest.main()

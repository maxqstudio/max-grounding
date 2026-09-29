from __future__ import annotations

import math
import unittest

from max_grounding.errors import RerankingError, RerankProviderError
from max_grounding.models import HybridHit, TextChunk
from max_grounding.reranking import (
    MAX_CONTEXT_CHARS,
    MAX_CONTEXT_EXCERPTS,
    MAX_EXCERPT_CHARS,
    MAX_RERANK_CANDIDATES,
    build_grounded_context,
    compress_context,
    rerank_hybrid,
)


def chunk(chunk_id: str, url: str, index: int, text: str) -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id,
        source_url=url,
        chunk_index=index,
        text=text,
        token_count=len(text.split()),
    )


def hybrid(
    item: TextChunk,
    rank: int,
    *,
    score: float = 0.01,
    lexical_rank: int | None = 1,
    semantic_rank: int | None = 1,
) -> HybridHit:
    return HybridHit(
        chunk=item,
        score=score,
        rank=rank,
        lexical_rank=lexical_rank,
        semantic_rank=semantic_rank,
    )


class _Provider:
    def __init__(self, scores=(0.0,), *, error: Exception | None = None) -> None:
        self.scores = scores
        self.error = error
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def score(self, query: str, documents: tuple[str, ...]):
        self.calls.append((query, documents))
        if self.error is not None:
            raise self.error
        return self.scores


class Phase7RerankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = chunk(
            "a",
            "https://example.com/a",
            0,
            "Opening note. Gold central banks increased reserves. Closing note.",
        )
        self.b = chunk(
            "b",
            "https://example.com/b",
            1,
            "Semiconductors rallied after earnings. Markets closed higher.",
        )

    def test_reranker_reorders_and_preserves_prior_provenance(self) -> None:
        provider = _Provider((0.1, 0.9))
        hits = rerank_hybrid(
            "gold reserves",
            (hybrid(self.a, 1), hybrid(self.b, 2, lexical_rank=None)),
            provider,
            limit=2,
        )

        self.assertEqual(tuple(hit.chunk.chunk_id for hit in hits), ("b", "a"))
        self.assertEqual(tuple(hit.rank for hit in hits), (1, 2))
        self.assertEqual(hits[0].hybrid_rank, 2)
        self.assertIsNone(hits[0].lexical_rank)
        self.assertEqual(hits[0].semantic_rank, 1)
        self.assertEqual(provider.calls, [
            (
                "gold reserves",
                (self.a.text, self.b.text),
            )
        ])

    def test_equal_provider_scores_preserve_hybrid_rank(self) -> None:
        provider = _Provider((0.5, 0.5))
        hits = rerank_hybrid(
            "query",
            (hybrid(self.a, 1), hybrid(self.b, 2)),
            provider,
            limit=2,
        )
        self.assertEqual(tuple(hit.chunk.chunk_id for hit in hits), ("a", "b"))
        self.assertEqual(tuple(hit.hybrid_rank for hit in hits), (1, 2))

    def test_empty_candidates_do_not_call_provider(self) -> None:
        provider = _Provider(())
        self.assertEqual(rerank_hybrid("query", (), provider), ())
        self.assertEqual(provider.calls, [])

    def test_provider_exception_wrong_count_and_invalid_scores_fail_closed(self) -> None:
        providers = [
            _Provider(error=RuntimeError("boom")),
            _Provider(()),
            _Provider((True,)),
            _Provider((float("nan"),)),
            _Provider((float("inf"),)),
            _Provider(("not-a-number",)),
        ]
        for provider in providers:
            with self.subTest(provider=provider.scores, error=provider.error):
                with self.assertRaises(RerankProviderError):
                    rerank_hybrid("query", (hybrid(self.a, 1),), provider)

    def test_invalid_candidate_rank_identity_score_and_bounds_fail_closed(self) -> None:
        twenty_one = tuple(
            hybrid(
                chunk(
                    f"id-{index}",
                    f"https://example.com/{index}",
                    index,
                    f"document {index}",
                ),
                index + 1,
            )
            for index in range(MAX_RERANK_CANDIDATES + 1)
        )
        cases = [
            lambda: rerank_hybrid("query", (hybrid(self.a, 2),), _Provider((1.0,))),
            lambda: rerank_hybrid(
                "query",
                (hybrid(self.a, 1), hybrid(self.a, 2)),
                _Provider((1.0, 0.5)),
            ),
            lambda: rerank_hybrid(
                "query",
                (hybrid(self.a, 1, score=float("nan")),),
                _Provider((1.0,)),
            ),
            lambda: rerank_hybrid("query", twenty_one, _Provider(tuple(range(21)))),
            lambda: rerank_hybrid("", (hybrid(self.a, 1),), _Provider((1.0,))),
            lambda: rerank_hybrid("x" * 4097, (hybrid(self.a, 1),), _Provider((1.0,))),
            lambda: rerank_hybrid("query", (hybrid(self.a, 1),), _Provider((1.0,)), limit=0),
            lambda: rerank_hybrid("query", (hybrid(self.a, 1),), _Provider((1.0,)), limit=21),
        ]
        for invoke in cases:
            with self.subTest(invoke=invoke):
                with self.assertRaises(RerankingError):
                    invoke()

    def test_compression_selects_query_relevant_exact_source_sentence(self) -> None:
        reranked = rerank_hybrid(
            "central banks gold",
            (hybrid(self.a, 1),),
            _Provider((0.9,)),
        )
        excerpts = compress_context(
            "central banks gold",
            reranked,
        )
        self.assertEqual(len(excerpts), 1)
        self.assertEqual(
            excerpts[0].text,
            "Gold central banks increased reserves.",
        )
        self.assertIn(excerpts[0].text, self.a.text)
        self.assertEqual(excerpts[0].source_url, self.a.source_url)
        self.assertEqual(excerpts[0].chunk_id, self.a.chunk_id)
        self.assertEqual(excerpts[0].rerank_rank, 1)
        self.assertEqual(excerpts[0].char_count, len(excerpts[0].text))

    def test_compression_semantic_fallback_is_still_extractive(self) -> None:
        reranked = rerank_hybrid(
            "totally unrelated terms",
            (hybrid(self.a, 1),),
            _Provider((0.9,)),
        )
        excerpts = compress_context(
            "totally unrelated terms",
            reranked,
            max_excerpt_chars=12,
            max_total_chars=12,
        )
        self.assertEqual(len(excerpts), 1)
        self.assertEqual(excerpts[0].text, "Opening note")
        self.assertIn(excerpts[0].text, self.a.text)

    def test_compression_hard_budgets_and_determinism(self) -> None:
        provider = _Provider((0.9, 0.8))
        reranked = rerank_hybrid(
            "markets gold",
            (hybrid(self.a, 1), hybrid(self.b, 2)),
            provider,
            limit=2,
        )
        first = compress_context(
            "markets gold",
            reranked,
            max_excerpts=2,
            max_excerpt_chars=20,
            max_total_chars=25,
        )
        second = compress_context(
            "markets gold",
            reranked,
            max_excerpts=2,
            max_excerpt_chars=20,
            max_total_chars=25,
        )
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 2)
        self.assertLessEqual(sum(item.char_count for item in first), 25)
        self.assertTrue(all(item.char_count <= 20 for item in first))
        self.assertTrue(all(item.text in item.source_url or item.text for item in first))
        for item in first:
            source = self.a if item.chunk_id == "a" else self.b
            self.assertIn(item.text, source.text)

    def test_compression_rejects_invalid_budgets(self) -> None:
        reranked = rerank_hybrid(
            "query",
            (hybrid(self.a, 1),),
            _Provider((0.9,)),
        )
        cases = [
            dict(max_excerpts=0),
            dict(max_excerpts=MAX_CONTEXT_EXCERPTS + 1),
            dict(max_excerpt_chars=0),
            dict(max_excerpt_chars=MAX_EXCERPT_CHARS + 1),
            dict(max_total_chars=0),
            dict(max_total_chars=MAX_CONTEXT_CHARS + 1),
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(RerankingError):
                    compress_context("query", reranked, **kwargs)

    def test_build_grounded_context_runs_rerank_then_extractive_compression(self) -> None:
        provider = _Provider((0.3, 0.9))
        excerpts = build_grounded_context(
            "markets earnings",
            (hybrid(self.a, 1), hybrid(self.b, 2)),
            provider,
            rerank_limit=2,
            max_excerpts=1,
        )
        self.assertEqual(len(excerpts), 1)
        self.assertEqual(excerpts[0].chunk_id, "b")
        self.assertEqual(
            excerpts[0].text,
            "Semiconductors rallied after earnings.",
        )
        self.assertEqual(provider.calls[0][0], "markets earnings")


if __name__ == "__main__":
    unittest.main()

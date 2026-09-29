from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from max_grounding.errors import AuthorityProviderError, TemporalScoringError
from max_grounding.models import (
    AuthoritySubject,
    EvidenceExcerpt,
    EvidenceMetadata,
)
from max_grounding.temporal import (
    MAX_FRESHNESS_HORIZON_SECONDS,
    MAX_TEMPORAL_EVIDENCE,
    MIN_FRESHNESS_HORIZON_SECONDS,
    score_authority,
    score_evidence_quality,
    score_temporal_components,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)


def excerpt(
    chunk_id: str,
    url: str,
    *,
    chunk_index: int,
    rerank_rank: int,
    text: str = "evidence",
) -> EvidenceExcerpt:
    return EvidenceExcerpt(
        source_url=url,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        rerank_rank=rerank_rank,
        text=text,
        char_count=len(text),
    )


def metadata(
    item: EvidenceExcerpt,
    *,
    retrieved_at: datetime = NOW,
    published_at: datetime | None = NOW,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    source_type: str | None = "news",
    geography: str | None = "ID",
) -> EvidenceMetadata:
    return EvidenceMetadata(
        source_url=item.source_url,
        chunk_id=item.chunk_id,
        retrieved_at=retrieved_at,
        published_at=published_at,
        valid_from=valid_from,
        valid_until=valid_until,
        source_type=source_type,
        geography=geography,
    )


class _AuthorityProvider:
    def __init__(self, scores=(1.0,), *, error: Exception | None = None) -> None:
        self.scores = scores
        self.error = error
        self.calls: list[tuple[AuthoritySubject, ...]] = []

    def score(self, subjects: tuple[AuthoritySubject, ...]):
        self.calls.append(subjects)
        if self.error is not None:
            raise self.error
        return self.scores


class _GeneratorAuthorityProvider:
    def score(self, subjects: tuple[AuthoritySubject, ...]):
        return (score for score in (1.0,) * len(subjects))


class Phase8TemporalScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.a = excerpt(
            "a",
            "https://official.example/a",
            chunk_index=0,
            rerank_rank=1,
        )
        self.b = excerpt(
            "b",
            "https://news.example/b",
            chunk_index=1,
            rerank_rank=2,
        )

    def test_authority_provider_is_called_once_with_source_context(self) -> None:
        provider = _AuthorityProvider((0.9, 0.7))
        items = (metadata(self.a, source_type="official", geography="ID"), metadata(self.b))

        scores = score_authority(items, provider)

        self.assertEqual(scores, (0.9, 0.7))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(
            provider.calls[0][0],
            AuthoritySubject(
                source_url=self.a.source_url,
                source_type="official",
                geography="ID",
            ),
        )

    def test_combined_score_multiplies_authority_freshness_and_validity(self) -> None:
        provider = _AuthorityProvider((0.8, 1.0))
        items = (
            metadata(self.a, published_at=NOW - timedelta(hours=6)),
            metadata(self.b, published_at=NOW - timedelta(hours=18)),
        )

        scored = score_evidence_quality(
            (self.a, self.b),
            items,
            provider,
            now=NOW,
            freshness_horizon_seconds=24 * 60 * 60,
        )

        self.assertEqual(tuple(item.excerpt.chunk_id for item in scored), ("a", "b"))
        self.assertAlmostEqual(scored[0].freshness_score, 0.75)
        self.assertAlmostEqual(scored[0].authority_score, 0.8)
        self.assertEqual(scored[0].temporal_validity, 1.0)
        self.assertAlmostEqual(scored[0].score, 0.6)
        self.assertAlmostEqual(scored[1].freshness_score, 0.25)
        self.assertAlmostEqual(scored[1].score, 0.25)
        self.assertEqual(tuple(item.rank for item in scored), (1, 2))

    def test_published_at_missing_falls_back_to_retrieved_at(self) -> None:
        item = metadata(
            self.a,
            retrieved_at=NOW - timedelta(hours=6),
            published_at=None,
        )
        result = score_temporal_components(
            (item,),
            now=NOW,
            freshness_horizon_seconds=24 * 60 * 60,
        )
        self.assertAlmostEqual(result[0].freshness_score, 0.75)
        self.assertEqual(result[0].temporal_validity, 1.0)

    def test_not_yet_valid_and_expired_evidence_score_zero_temporally(self) -> None:
        future = metadata(
            self.a,
            valid_from=NOW + timedelta(minutes=1),
        )
        expired = metadata(
            self.b,
            valid_until=NOW - timedelta(microseconds=1),
        )
        result = score_temporal_components(
            (future, expired),
            now=NOW,
            freshness_horizon_seconds=60,
        )
        self.assertEqual(
            tuple(item.temporal_validity for item in result),
            (0.0, 0.0),
        )

    def test_exact_validity_boundaries_are_inclusive(self) -> None:
        item = metadata(
            self.a,
            valid_from=NOW,
            valid_until=NOW,
        )
        result = score_temporal_components(
            (item,),
            now=NOW,
            freshness_horizon_seconds=60,
        )
        self.assertEqual(result[0].temporal_validity, 1.0)

    def test_equal_combined_scores_preserve_prior_rerank_order(self) -> None:
        provider = _AuthorityProvider((1.0, 1.0))
        scored = score_evidence_quality(
            (self.a, self.b),
            (metadata(self.a), metadata(self.b)),
            provider,
            now=NOW,
            freshness_horizon_seconds=60,
        )
        self.assertEqual(tuple(item.excerpt.chunk_id for item in scored), ("a", "b"))

    def test_empty_input_does_not_call_authority_provider(self) -> None:
        provider = _AuthorityProvider(())
        self.assertEqual(
            score_evidence_quality(
                (),
                (),
                provider,
                now=NOW,
                freshness_horizon_seconds=60,
            ),
            (),
        )
        self.assertEqual(provider.calls, [])

    def test_rejects_unbounded_iterable_inputs(self) -> None:
        item = metadata(self.a)
        provider = _AuthorityProvider((1.0,))
        with self.assertRaises(TemporalScoringError):
            score_authority((value for value in (item,)), provider)
        with self.assertRaises(TemporalScoringError):
            score_temporal_components(
                (value for value in (item,)),
                now=NOW,
                freshness_horizon_seconds=60,
            )
        with self.assertRaises(TemporalScoringError):
            score_evidence_quality(
                (value for value in (self.a,)),
                (item,),
                provider,
                now=NOW,
                freshness_horizon_seconds=60,
            )

    def test_invalid_prior_rerank_rank_fails_closed(self) -> None:
        invalid = EvidenceExcerpt(
            source_url=self.a.source_url,
            chunk_id=self.a.chunk_id,
            chunk_index=self.a.chunk_index,
            rerank_rank=0,
            text=self.a.text,
            char_count=self.a.char_count,
        )
        with self.assertRaises(TemporalScoringError):
            score_evidence_quality(
                (invalid,),
                (metadata(invalid),),
                _AuthorityProvider((1.0,)),
                now=NOW,
                freshness_horizon_seconds=60,
            )

    def test_provider_failures_and_invalid_scores_fail_closed(self) -> None:
        providers = [
            _AuthorityProvider(error=RuntimeError("boom")),
            _GeneratorAuthorityProvider(),
            _AuthorityProvider(()),
            _AuthorityProvider((True,)),
            _AuthorityProvider(("bad",)),
            _AuthorityProvider((float("nan"),)),
            _AuthorityProvider((float("inf"),)),
            _AuthorityProvider((-0.1,)),
            _AuthorityProvider((1.1,)),
        ]
        item = metadata(self.a)
        for provider in providers:
            with self.subTest(provider=provider):
                with self.assertRaises(AuthorityProviderError):
                    score_authority((item,), provider)

    def test_invalid_identity_timestamp_and_horizon_fail_closed(self) -> None:
        bad_identity = EvidenceMetadata(
            source_url="https://wrong.example/",
            chunk_id=self.a.chunk_id,
            retrieved_at=NOW,
            published_at=NOW,
        )
        naive = metadata(self.a, retrieved_at=NOW.replace(tzinfo=None))
        offset = metadata(
            self.a,
            retrieved_at=datetime(2026, 9, 29, 15, 0, tzinfo=timezone(timedelta(hours=1))),
        )
        future_retrieved = metadata(self.a, retrieved_at=NOW + timedelta(seconds=1))
        future_published = metadata(self.a, published_at=NOW + timedelta(seconds=1))
        inverted_validity = metadata(
            self.a,
            valid_from=NOW + timedelta(seconds=1),
            valid_until=NOW,
        )

        calls = [
            lambda: score_evidence_quality(
                (self.a,),
                (bad_identity,),
                _AuthorityProvider((1.0,)),
                now=NOW,
                freshness_horizon_seconds=60,
            ),
            lambda: score_temporal_components((naive,), now=NOW, freshness_horizon_seconds=60),
            lambda: score_temporal_components((offset,), now=NOW, freshness_horizon_seconds=60),
            lambda: score_temporal_components((future_retrieved,), now=NOW, freshness_horizon_seconds=60),
            lambda: score_temporal_components((future_published,), now=NOW, freshness_horizon_seconds=60),
            lambda: score_temporal_components((inverted_validity,), now=NOW, freshness_horizon_seconds=60),
            lambda: score_temporal_components((metadata(self.a),), now=NOW.replace(tzinfo=None), freshness_horizon_seconds=60),
            lambda: score_temporal_components((metadata(self.a),), now=NOW, freshness_horizon_seconds=MIN_FRESHNESS_HORIZON_SECONDS - 1),
            lambda: score_temporal_components((metadata(self.a),), now=NOW, freshness_horizon_seconds=MAX_FRESHNESS_HORIZON_SECONDS + 1),
        ]
        for invoke in calls:
            with self.subTest(invoke=invoke):
                with self.assertRaises(TemporalScoringError):
                    invoke()

    def test_more_than_eight_items_and_duplicate_identity_fail_closed(self) -> None:
        many_excerpts = tuple(
            excerpt(
                f"id-{index}",
                f"https://example.com/{index}",
                chunk_index=index,
                rerank_rank=index + 1,
            )
            for index in range(MAX_TEMPORAL_EVIDENCE + 1)
        )
        many_metadata = tuple(metadata(item) for item in many_excerpts)
        with self.assertRaises(TemporalScoringError):
            score_evidence_quality(
                many_excerpts,
                many_metadata,
                _AuthorityProvider(tuple(1.0 for _ in many_excerpts)),
                now=NOW,
                freshness_horizon_seconds=60,
            )

        duplicate = excerpt(
            self.a.chunk_id,
            self.a.source_url,
            chunk_index=self.a.chunk_index,
            rerank_rank=2,
        )
        with self.assertRaises(TemporalScoringError):
            score_evidence_quality(
                (self.a, duplicate),
                (metadata(self.a), metadata(duplicate)),
                _AuthorityProvider((1.0, 1.0)),
                now=NOW,
                freshness_horizon_seconds=60,
            )


if __name__ == "__main__":
    unittest.main()

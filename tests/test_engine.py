from __future__ import annotations

import unittest

from max_grounding.engine import GroundingEngine
from max_grounding.errors import InvalidGroundingRequest
from max_grounding.models import EvidenceStatus, GroundingRequest, SourceCandidate


def candidate(url: str, rank: int = 1) -> SourceCandidate:
    return SourceCandidate(
        url=url,
        title="source",
        snippet="evidence",
        provider="fake",
        rank=rank,
    )


class FakeProvider:
    def __init__(self, rounds: list[tuple[SourceCandidate, ...]]) -> None:
        self.rounds = list(rounds)
        self.calls = []

    def search(self, query):
        self.calls.append(query)
        index = len(self.calls) - 1
        if index >= len(self.rounds):
            return ()
        return self.rounds[index]


class FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, query):
        self.calls += 1
        raise RuntimeError("upstream unavailable")


class GroundingEngineTests(unittest.TestCase):
    def test_stops_after_first_call_when_evidence_is_sufficient(self) -> None:
        provider = FakeProvider([(candidate("https://example.com/a"),)])
        result = GroundingEngine(provider).ground(
            GroundingRequest(query="test", min_evidence_sources=1)
        )
        self.assertEqual(result.status, EvidenceStatus.GROUNDED)
        self.assertEqual(result.search_calls_used, 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(result.sources), 1)

    def test_uses_at_most_two_calls_and_duplicates_do_not_fake_sufficiency(self) -> None:
        provider = FakeProvider(
            [
                (candidate("https://example.com/a#one"),),
                (candidate("https://EXAMPLE.com/a/", rank=1),),
            ]
        )
        result = GroundingEngine(provider).ground(
            GroundingRequest(
                query="test",
                max_search_rounds=2,
                min_evidence_sources=2,
            )
        )
        self.assertEqual(result.status, EvidenceStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.search_calls_used, 2)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(len(result.sources), 1)

    def test_no_results_fails_closed_after_budget_is_exhausted(self) -> None:
        provider = FakeProvider([(), ()])
        result = GroundingEngine(provider).ground(
            GroundingRequest(query="missing", max_search_rounds=2)
        )
        self.assertEqual(result.status, EvidenceStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.search_calls_used, 2)
        self.assertEqual(result.sources, ())

    def test_provider_exception_fails_closed_without_retry_loop(self) -> None:
        provider = FailingProvider()
        result = GroundingEngine(provider).ground(
            GroundingRequest(query="test", max_search_rounds=2)
        )
        self.assertEqual(result.status, EvidenceStatus.PROVIDER_ERROR)
        self.assertEqual(result.search_calls_used, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.sources, ())

    def test_invalid_request_never_calls_provider(self) -> None:
        provider = FakeProvider([(candidate("https://example.com/a"),)])
        with self.assertRaises(InvalidGroundingRequest):
            GroundingEngine(provider).ground(
                GroundingRequest(query="test", max_search_rounds=3)
            )
        self.assertEqual(provider.calls, [])

    def test_provider_receives_bounded_round_metadata(self) -> None:
        provider = FakeProvider([(), ()])
        GroundingEngine(provider).ground(
            GroundingRequest(
                query="  latest  price ",
                language="ID",
                country="id",
                max_search_rounds=2,
                results_per_call=4,
            )
        )
        self.assertEqual([x.round_index for x in provider.calls], [1, 2])
        self.assertEqual([x.limit for x in provider.calls], [4, 4])
        self.assertEqual({x.query for x in provider.calls}, {"latest price"})
        self.assertEqual({x.language for x in provider.calls}, {"id"})
        self.assertEqual({x.country for x in provider.calls}, {"ID"})


if __name__ == "__main__":
    unittest.main()

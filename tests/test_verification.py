from __future__ import annotations

import unittest
from datetime import datetime, timezone

from max_grounding.errors import ClaimVerificationError
from max_grounding.evidence_graph import build_evidence_graph
from max_grounding.models import (
    AnswerClaim,
    ClaimVerificationStatus,
    EvidenceAssertion,
    EvidenceExcerpt,
    EvidenceMetadata,
    EvidenceQualityScore,
)
from max_grounding.verification import (
    MAX_ANSWER_CLAIMS,
    MAX_REQUIRED_SOURCES,
    build_claim_citations,
    build_synthesis_packet,
    verify_claims,
)


NOW = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)


def quality(
    source_url: str,
    chunk_id: str,
    *,
    score: float,
    rank: int,
    text: str,
) -> EvidenceQualityScore:
    excerpt = EvidenceExcerpt(
        source_url=source_url,
        chunk_id=chunk_id,
        chunk_index=rank - 1,
        rerank_rank=rank,
        text=text,
        char_count=len(text),
    )
    metadata = EvidenceMetadata(
        source_url=source_url,
        chunk_id=chunk_id,
        retrieved_at=NOW,
        published_at=NOW,
        source_type="test",
        geography="ID",
    )
    return EvidenceQualityScore(
        excerpt=excerpt,
        metadata=metadata,
        authority_score=score,
        freshness_score=1.0,
        temporal_validity=1.0,
        score=score,
        rank=rank,
    )


def assertion(
    assertion_id: str,
    claim_key: str,
    value: str,
    *,
    exclusive: bool,
    source_url: str,
    chunk_id: str,
    score: float,
    rank: int,
    text: str,
) -> EvidenceAssertion:
    return EvidenceAssertion(
        assertion_id=assertion_id,
        claim_key=claim_key,
        value=value,
        exclusive=exclusive,
        evidence=quality(
            source_url,
            chunk_id,
            score=score,
            rank=rank,
            text=text,
        ),
    )


def claim(
    claim_id: str,
    text: str,
    claim_key: str,
    value: str,
) -> AnswerClaim:
    return AnswerClaim(
        claim_id=claim_id,
        text=text,
        claim_key=claim_key,
        value=value,
    )


class Phase10VerificationTests(unittest.TestCase):
    def test_supported_claim_has_claim_level_citations_and_confidence_index(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "a",
                    "release status",
                    "stable",
                    exclusive=True,
                    source_url="https://official.example/release",
                    chunk_id="a",
                    score=0.8,
                    rank=1,
                    text="Version 1.0 is stable.",
                ),
                assertion(
                    "b",
                    "release status",
                    "stable",
                    exclusive=True,
                    source_url="https://docs.example/release",
                    chunk_id="b",
                    score=0.6,
                    rank=2,
                    text="The release is stable.",
                ),
            )
        )
        answer_claim = claim(
            "c1",
            "Version 1.0 is stable.",
            " Release   Status ",
            "STABLE",
        )

        packet = build_synthesis_packet(
            (answer_claim,),
            graph,
            required_sources=2,
        )

        self.assertEqual(len(packet.verifications), 1)
        result = packet.verifications[0]
        self.assertEqual(result.status, ClaimVerificationStatus.SUPPORTED)
        self.assertEqual(result.supporting_source_count, 2)
        self.assertAlmostEqual(result.confidence, 0.7)
        self.assertEqual(tuple(c.assertion_id for c in result.citations), ("a", "b"))
        self.assertEqual(
            tuple(c.source_url for c in result.citations),
            (
                "https://official.example/release",
                "https://docs.example/release",
            ),
        )
        self.assertEqual(packet.synthesis_claims, (result,))
        self.assertEqual(packet.blocked_claims, ())

    def test_partial_support_is_blocked_when_source_threshold_not_met(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "a",
                    "price",
                    "100",
                    exclusive=True,
                    source_url="https://shop.example/item",
                    chunk_id="a",
                    score=0.8,
                    rank=1,
                    text="Price is 100.",
                ),
            )
        )
        answer_claim = claim("c1", "Price is 100.", "price", "100")

        packet = build_synthesis_packet(
            (answer_claim,),
            graph,
            required_sources=2,
        )

        result = packet.verifications[0]
        self.assertEqual(result.status, ClaimVerificationStatus.PARTIALLY_SUPPORTED)
        self.assertAlmostEqual(result.confidence, 0.4)
        self.assertEqual(result.supporting_source_count, 1)
        self.assertEqual(packet.synthesis_claims, ())
        self.assertEqual(packet.blocked_claims, (result,))

    def test_conflict_has_precedence_over_strong_support(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "stable-a",
                    "status",
                    "stable",
                    exclusive=True,
                    source_url="https://a.example/",
                    chunk_id="a",
                    score=1.0,
                    rank=1,
                    text="Status is stable.",
                ),
                assertion(
                    "stable-b",
                    "status",
                    "stable",
                    exclusive=True,
                    source_url="https://b.example/",
                    chunk_id="b",
                    score=1.0,
                    rank=2,
                    text="Status is stable.",
                ),
                assertion(
                    "beta",
                    "status",
                    "beta",
                    exclusive=True,
                    source_url="https://c.example/",
                    chunk_id="c",
                    score=0.2,
                    rank=3,
                    text="Status is beta.",
                ),
            )
        )

        result = verify_claims(
            (claim("c1", "Status is stable.", "status", "stable"),),
            graph,
            required_sources=2,
        )[0]

        self.assertEqual(result.status, ClaimVerificationStatus.CONFLICTED)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.supporting_source_count, 2)

    def test_unsupported_claim_has_zero_confidence_and_no_citations(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "a",
                    "status",
                    "stable",
                    exclusive=True,
                    source_url="https://a.example/",
                    chunk_id="a",
                    score=0.9,
                    rank=1,
                    text="Status is stable.",
                ),
            )
        )
        answer_claim = claim("c1", "Status is final.", "status", "final")

        packet = build_synthesis_packet(
            (answer_claim,),
            graph,
            required_sources=1,
        )
        result = packet.verifications[0]

        self.assertEqual(result.status, ClaimVerificationStatus.UNSUPPORTED)
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.citations, ())
        self.assertEqual(packet.synthesis_claims, ())
        self.assertEqual(packet.blocked_claims, (result,))

    def test_nonexclusive_alternative_values_do_not_create_conflict(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "linux",
                    "supported platform",
                    "linux",
                    exclusive=False,
                    source_url="https://a.example/",
                    chunk_id="a",
                    score=0.9,
                    rank=1,
                    text="Linux is supported.",
                ),
                assertion(
                    "windows",
                    "supported platform",
                    "windows",
                    exclusive=False,
                    source_url="https://b.example/",
                    chunk_id="b",
                    score=0.9,
                    rank=2,
                    text="Windows is supported.",
                ),
            )
        )

        result = verify_claims(
            (claim("c1", "Linux is supported.", "supported platform", "linux"),),
            graph,
            required_sources=1,
        )[0]

        self.assertEqual(result.status, ClaimVerificationStatus.SUPPORTED)
        self.assertGreater(result.confidence, 0.0)

    def test_build_claim_citations_preserves_exact_evidence_provenance(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "a",
                    "status",
                    "stable",
                    exclusive=True,
                    source_url="https://a.example/",
                    chunk_id="chunk-a",
                    score=0.9,
                    rank=1,
                    text="Exact evidence text.",
                ),
            )
        )
        answer_claim = claim("c1", "Status is stable.", "status", "stable")

        citations = build_claim_citations((answer_claim,), graph)

        self.assertEqual(len(citations), 1)
        citation = citations[0]
        self.assertEqual(citation.claim_id, "c1")
        self.assertEqual(citation.assertion_id, "a")
        self.assertEqual(citation.source_url, "https://a.example/")
        self.assertEqual(citation.chunk_id, "chunk-a")
        self.assertEqual(citation.text, "Exact evidence text.")

    def test_generator_duplicate_ids_and_invalid_required_sources_fail_closed(self) -> None:
        graph = build_evidence_graph(())
        one = claim("one", "One.", "key", "value")

        with self.assertRaises(ClaimVerificationError):
            verify_claims((item for item in (one,)), graph)

        with self.assertRaises(ClaimVerificationError):
            verify_claims((one, one), graph)

        for required_sources in (0, MAX_REQUIRED_SOURCES + 1, True):
            with self.subTest(required_sources=required_sources):
                with self.assertRaises(ClaimVerificationError):
                    verify_claims(
                        (one,),
                        graph,
                        required_sources=required_sources,
                    )

    def test_more_than_max_claims_fail_closed(self) -> None:
        graph = build_evidence_graph(())
        claims = tuple(
            claim(
                f"c-{index}",
                f"Claim {index}.",
                "key",
                str(index),
            )
            for index in range(MAX_ANSWER_CLAIMS + 1)
        )
        with self.assertRaises(ClaimVerificationError):
            build_synthesis_packet(claims, graph)

    def test_malformed_graph_fails_closed(self) -> None:
        good_graph = build_evidence_graph(
            (
                assertion(
                    "a",
                    "status",
                    "stable",
                    exclusive=True,
                    source_url="https://a.example/",
                    chunk_id="a",
                    score=0.9,
                    rank=1,
                    text="Status is stable.",
                ),
            )
        )
        cluster = good_graph.clusters[0]
        malformed_cluster = type(cluster)(
            claim_key=cluster.claim_key,
            value=cluster.value,
            assertion_ids=("missing",),
            distinct_source_count=1,
            quality_weight_sum=0.9,
        )
        malformed_graph = type(good_graph)(
            assertions=good_graph.assertions,
            clusters=(malformed_cluster,),
            relations=good_graph.relations,
        )

        with self.assertRaises(ClaimVerificationError):
            verify_claims(
                (claim("c1", "Status is stable.", "status", "stable"),),
                malformed_graph,
            )

    def test_only_supported_claims_are_synthesis_safe(self) -> None:
        graph = build_evidence_graph(
            (
                assertion(
                    "supported",
                    "status",
                    "stable",
                    exclusive=True,
                    source_url="https://a.example/",
                    chunk_id="a",
                    score=1.0,
                    rank=1,
                    text="Status is stable.",
                ),
            )
        )
        supported = claim("supported-claim", "Status is stable.", "status", "stable")
        unsupported = claim("unsupported-claim", "Price is 10.", "price", "10")

        packet = build_synthesis_packet(
            (supported, unsupported),
            graph,
            required_sources=1,
        )

        self.assertEqual(
            tuple(item.claim.claim_id for item in packet.synthesis_claims),
            ("supported-claim",),
        )
        self.assertEqual(
            tuple(item.claim.claim_id for item in packet.blocked_claims),
            ("unsupported-claim",),
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from max_grounding.errors import EvidenceGraphError
from max_grounding.models import (
    EvidenceAssertion,
    EvidenceExcerpt,
    EvidenceMetadata,
    EvidenceQualityScore,
    EvidenceRelationType,
)
from max_grounding.evidence_graph import (
    MAX_GRAPH_ASSERTIONS,
    build_clusters,
    build_evidence_graph,
    build_relations,
)


NOW = datetime(2026, 9, 29, 14, 0, tzinfo=timezone.utc)


def quality(
    source_url: str,
    chunk_id: str,
    *,
    score: float,
    rank: int,
    chunk_index: int = 0,
) -> EvidenceQualityScore:
    excerpt = EvidenceExcerpt(
        source_url=source_url,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        rerank_rank=rank,
        text=f"evidence {chunk_id}",
        char_count=len(f"evidence {chunk_id}"),
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
        ),
    )


class Phase9EvidenceGraphTests(unittest.TestCase):
    def test_same_normalized_claim_and_value_corroborate(self) -> None:
        left = assertion(
            "b",
            " Product   Price ",
            "RP 10.000",
            exclusive=True,
            source_url="https://a.example/item",
            chunk_id="a",
            score=0.8,
            rank=1,
        )
        right = assertion(
            "a",
            "product price",
            "rp 10.000",
            exclusive=True,
            source_url="https://b.example/item",
            chunk_id="b",
            score=0.7,
            rank=2,
        )

        graph = build_evidence_graph((left, right))

        self.assertEqual(tuple(item.assertion_id for item in graph.assertions), ("a", "b"))
        self.assertEqual(graph.assertions[0].claim_key, "product price")
        self.assertEqual(graph.assertions[0].value, "rp 10.000")
        self.assertEqual(len(graph.relations), 1)
        relation = graph.relations[0]
        self.assertEqual(relation.left_assertion_id, "a")
        self.assertEqual(relation.right_assertion_id, "b")
        self.assertEqual(relation.relation, EvidenceRelationType.CORROBORATES)

    def test_exclusive_different_values_contradict(self) -> None:
        a = assertion(
            "a",
            "release status",
            "stable",
            exclusive=True,
            source_url="https://a.example/",
            chunk_id="a",
            score=0.9,
            rank=1,
        )
        b = assertion(
            "b",
            "release status",
            "beta",
            exclusive=True,
            source_url="https://b.example/",
            chunk_id="b",
            score=0.8,
            rank=2,
        )

        relations = build_relations((a, b))
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].relation, EvidenceRelationType.CONTRADICTS)

    def test_nonexclusive_different_values_do_not_contradict(self) -> None:
        a = assertion(
            "a",
            "supported platform",
            "linux",
            exclusive=False,
            source_url="https://a.example/",
            chunk_id="a",
            score=0.9,
            rank=1,
        )
        b = assertion(
            "b",
            "supported platform",
            "windows",
            exclusive=False,
            source_url="https://b.example/",
            chunk_id="b",
            score=0.8,
            rank=2,
        )

        self.assertEqual(build_relations((a, b)), ())

    def test_cluster_counts_distinct_sources_and_uses_per_source_max_quality(self) -> None:
        items = (
            assertion(
                "a1",
                "price",
                "100",
                exclusive=True,
                source_url="https://same.example/",
                chunk_id="a1",
                score=0.8,
                rank=1,
            ),
            assertion(
                "a2",
                "price",
                "100",
                exclusive=True,
                source_url="https://same.example/",
                chunk_id="a2",
                score=0.6,
                rank=2,
            ),
            assertion(
                "b",
                "price",
                "100",
                exclusive=True,
                source_url="https://other.example/",
                chunk_id="b",
                score=0.7,
                rank=3,
            ),
        )

        clusters = build_clusters(items)
        self.assertEqual(len(clusters), 1)
        cluster = clusters[0]
        self.assertEqual(cluster.distinct_source_count, 2)
        self.assertAlmostEqual(cluster.quality_weight_sum, 1.5)
        self.assertEqual(cluster.assertion_ids, ("a1", "a2", "b"))

    def test_inconsistent_exclusivity_for_same_claim_fails_closed(self) -> None:
        a = assertion(
            "a",
            "status",
            "stable",
            exclusive=True,
            source_url="https://a.example/",
            chunk_id="a",
            score=1.0,
            rank=1,
        )
        b = assertion(
            "b",
            "status",
            "beta",
            exclusive=False,
            source_url="https://b.example/",
            chunk_id="b",
            score=1.0,
            rank=2,
        )
        with self.assertRaises(EvidenceGraphError):
            build_evidence_graph((a, b))

    def test_generator_input_and_over_limit_input_fail_closed(self) -> None:
        one = assertion(
            "one",
            "status",
            "stable",
            exclusive=True,
            source_url="https://one.example/",
            chunk_id="one",
            score=1.0,
            rank=1,
        )
        with self.assertRaises(EvidenceGraphError):
            build_evidence_graph(item for item in (one,))

        many = tuple(
            assertion(
                f"id-{index}",
                "status",
                str(index),
                exclusive=True,
                source_url=f"https://{index}.example/",
                chunk_id=f"chunk-{index}",
                score=1.0,
                rank=index + 1,
            )
            for index in range(MAX_GRAPH_ASSERTIONS + 1)
        )
        with self.assertRaises(EvidenceGraphError):
            build_evidence_graph(many)

    def test_duplicate_assertion_and_chunk_identity_fail_closed(self) -> None:
        a = assertion(
            "same",
            "status",
            "stable",
            exclusive=True,
            source_url="https://a.example/",
            chunk_id="chunk-a",
            score=1.0,
            rank=1,
        )
        duplicate_id = assertion(
            "same",
            "status",
            "stable",
            exclusive=True,
            source_url="https://b.example/",
            chunk_id="chunk-b",
            score=1.0,
            rank=2,
        )
        duplicate_chunk = assertion(
            "other",
            "status",
            "stable",
            exclusive=True,
            source_url="https://a.example/",
            chunk_id="chunk-a",
            score=1.0,
            rank=2,
        )
        for pair in ((a, duplicate_id), (a, duplicate_chunk)):
            with self.subTest(pair=pair):
                with self.assertRaises(EvidenceGraphError):
                    build_evidence_graph(pair)

    def test_malformed_phase8_quality_or_provenance_fails_closed(self) -> None:
        good = quality(
            "https://a.example/",
            "a",
            score=0.8,
            rank=1,
        )
        mismatched_metadata = EvidenceMetadata(
            source_url="https://wrong.example/",
            chunk_id="a",
            retrieved_at=NOW,
            published_at=NOW,
        )
        malformed = EvidenceQualityScore(
            excerpt=good.excerpt,
            metadata=mismatched_metadata,
            authority_score=0.8,
            freshness_score=1.0,
            temporal_validity=1.0,
            score=0.8,
            rank=1,
        )
        bad_score = EvidenceQualityScore(
            excerpt=good.excerpt,
            metadata=good.metadata,
            authority_score=0.8,
            freshness_score=1.0,
            temporal_validity=1.0,
            score=float("nan"),
            rank=1,
        )

        for evidence in (malformed, bad_score):
            item = EvidenceAssertion(
                assertion_id="a",
                claim_key="status",
                value="stable",
                exclusive=True,
                evidence=evidence,
            )
            with self.subTest(evidence=evidence):
                with self.assertRaises(EvidenceGraphError):
                    build_evidence_graph((item,))

    def test_quality_weight_is_descriptive_not_a_truth_winner(self) -> None:
        stable = assertion(
            "stable",
            "status",
            "stable",
            exclusive=True,
            source_url="https://a.example/",
            chunk_id="a",
            score=0.4,
            rank=1,
        )
        beta = assertion(
            "beta",
            "status",
            "beta",
            exclusive=True,
            source_url="https://b.example/",
            chunk_id="b",
            score=0.9,
            rank=2,
        )
        graph = build_evidence_graph((stable, beta))

        self.assertEqual(len(graph.clusters), 2)
        self.assertEqual(len(graph.relations), 1)
        self.assertEqual(graph.relations[0].relation, EvidenceRelationType.CONTRADICTS)
        self.assertFalse(hasattr(graph, "winner"))
        self.assertFalse(hasattr(graph, "truth"))

    def test_empty_input_returns_empty_graph(self) -> None:
        graph = build_evidence_graph(())
        self.assertEqual(graph.assertions, ())
        self.assertEqual(graph.clusters, ())
        self.assertEqual(graph.relations, ())


if __name__ == "__main__":
    unittest.main()

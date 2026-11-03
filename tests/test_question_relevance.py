"""Question-level relevance gates must not confuse a valid quote with an answer."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from max_grounding.errors import InvalidGroundingRequest
from max_grounding.models import (
    AnswerClaim,
    ClaimCitation,
    ClaimVerification,
    ClaimVerificationStatus,
    SynthesisPacket,
)
from max_grounding.question_relevance import (
    assess_question_sufficiency,
    compile_question_contract,
)


IDENTIFIER = "8901234567890"
SOURCE = "https://source.example/records"


def scoped(question: str, keys: list[str], *, anchors: list[str] | None = None):
    return compile_question_contract(
        {
            "question": question,
            "requested_claim_keys": keys,
            "subject_anchors": anchors or [],
        },
        required_sources=1,
    )


def packet_for(
    key: str,
    value: str,
    excerpt: str,
    *,
    status: ClaimVerificationStatus = ClaimVerificationStatus.SUPPORTED,
    url: str = SOURCE,
) -> SynthesisPacket:
    claim = AnswerClaim(
        claim_id="claim-0001", text=f"{key}: {value}", claim_key=key, value=value
    )
    citation = ClaimCitation(
        claim_id=claim.claim_id,
        assertion_id="assertion-1",
        source_url=url,
        chunk_id="sha256:example",
        text=excerpt,
    )
    verification = ClaimVerification(
        claim=claim,
        status=status,
        confidence=0.8 if status is ClaimVerificationStatus.SUPPORTED else 0.0,
        supporting_source_count=1,
        required_sources=1,
        citations=(citation,),
    )
    supported = status is ClaimVerificationStatus.SUPPORTED
    return SynthesisPacket(
        verifications=(verification,),
        synthesis_claims=(verification,) if supported else (),
        blocked_claims=() if supported else (verification,),
    )


class QuestionRelevanceContracts(unittest.TestCase):
    def test_missing_question_and_unanchored_open_question_fail_closed(self) -> None:
        with self.assertRaises(InvalidGroundingRequest):
            compile_question_contract(None, required_sources=1)
        with self.assertRaises(InvalidGroundingRequest):
            scoped("What is the brand?", ["brand"])

    def test_prefix_excerpt_does_not_bind_complete_identifier(self) -> None:
        contract = scoped(
            f"What brand is linked to barcode {IDENTIFIER}?", ["brand"]
        )
        result = assess_question_sufficiency(
            contract,
            packet_for("brand", "SampleCo", "Barcode 8901234 | Brand SampleCo"),
        )
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("IDENTIFIER_NOT_BOUND", result.reason_codes)

    def test_full_identifier_and_labeled_requested_field_is_positive_control(self) -> None:
        contract = scoped(
            f"What brand is linked to barcode {IDENTIFIER}?", ["brand"]
        )
        result = assess_question_sufficiency(
            contract,
            packet_for(
                "brand",
                "SampleCo",
                f"GTIN {IDENTIFIER} | Brand SampleCo",
            ),
        )
        self.assertEqual(result.status, "SUFFICIENT")
        self.assertEqual(result.reason_codes, ())

    def test_irrelevant_literal_quote_is_not_a_supported_answer(self) -> None:
        contract = scoped(
            f"What does barcode {IDENTIFIER} represent?",
            ["exact_evidence"],
        )
        result = assess_question_sufficiency(
            contract,
            packet_for(
                "exact_evidence",
                "8901234 is a prefix",
                "8901234 is a prefix",
            ),
        )
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("EXTRACTIVE_NOT_REQUESTED", result.reason_codes)

    def test_requested_field_coverage_blocks_partial_packet(self) -> None:
        contract = scoped(
            f"What are the brand and product name for barcode {IDENTIFIER}?",
            ["brand", "product_name"],
        )
        result = assess_question_sufficiency(
            contract,
            packet_for("brand", "SampleCo", f"GTIN {IDENTIFIER} | Brand SampleCo"),
        )
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("INCOMPLETE_COVERAGE", result.reason_codes)

    def test_manufacturer_authority_is_not_inferred_from_citation_domain(self) -> None:
        contract = scoped(
            f"Which manufacturer record gives the brand for GTIN {IDENTIFIER}?",
            ["brand"],
        )
        evidence = packet_for(
            "brand", "SampleCo", f"GTIN {IDENTIFIER} | Brand SampleCo"
        )
        missing = assess_question_sufficiency(contract, evidence)
        self.assertEqual(missing.status, "INSUFFICIENT")
        self.assertIn("SOURCE_AUTHORITY_MISSING", missing.reason_codes)
        untrusted = assess_question_sufficiency(
            contract, evidence, trusted_source_roles={SOURCE: "merchant"}
        )
        self.assertEqual(untrusted.status, "INSUFFICIENT")
        accepted = assess_question_sufficiency(
            contract, evidence, trusted_source_roles={SOURCE: "manufacturer"}
        )
        self.assertEqual(accepted.status, "SUFFICIENT")

    def test_current_record_requires_trusted_publication_metadata(self) -> None:
        contract = scoped(
            f"What is the latest brand for barcode {IDENTIFIER}?", ["brand"]
        )
        evidence = packet_for(
            "brand", "SampleCo", f"GTIN {IDENTIFIER} | Brand SampleCo"
        )
        unknown = assess_question_sufficiency(contract, evidence)
        self.assertIn("TEMPORAL_NOT_PROVEN", unknown.reason_codes)
        published = assess_question_sufficiency(
            contract,
            evidence,
            trusted_publication_dates={
                SOURCE: datetime(2026, 11, 1, tzinfo=timezone.utc)
            },
            now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(published.status, "INSUFFICIENT")
        self.assertIn("TEMPORAL_NOT_PROVEN", published.reason_codes)

    def test_explicit_publication_cutoff_can_be_verified_from_trusted_metadata(self) -> None:
        contract = scoped(
            f"What brand for barcode {IDENTIFIER} was published after 2026-10-01?",
            ["brand"],
        )
        evidence = packet_for(
            "brand", "SampleCo", f"GTIN {IDENTIFIER} | Brand SampleCo"
        )
        result = assess_question_sufficiency(
            contract,
            evidence,
            trusted_publication_dates={
                SOURCE: datetime(2026, 11, 1, tzinfo=timezone.utc)
            },
            now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(result.status, "SUFFICIENT")

    def test_conflicted_claims_cannot_pass_question_sufficiency(self) -> None:
        contract = scoped(f"What brand is linked to barcode {IDENTIFIER}?", ["brand"])
        evidence = packet_for(
            "brand",
            "SampleCo",
            f"GTIN {IDENTIFIER} | Brand SampleCo",
            status=ClaimVerificationStatus.CONFLICTED,
        )
        result = assess_question_sufficiency(contract, evidence)
        self.assertEqual(result.status, "INSUFFICIENT")
        self.assertIn("CLAIM_NOT_SUPPORTED", result.reason_codes)

    def test_without_exact_identifier_subject_anchor_is_required_and_verified(self) -> None:
        contract = scoped(
            "What brand is listed for the Sample Widget?", ["brand"],
            anchors=["Sample Widget"],
        )
        wrong = assess_question_sufficiency(
            contract, packet_for("brand", "SampleCo", "Brand SampleCo for Other Widget")
        )
        self.assertIn("SUBJECT_NOT_BOUND", wrong.reason_codes)
        correct = assess_question_sufficiency(
            contract,
            packet_for("brand", "SampleCo", "Sample Widget | Brand SampleCo"),
        )
        self.assertEqual(correct.status, "SUFFICIENT")


if __name__ == "__main__":
    unittest.main()

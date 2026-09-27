from __future__ import annotations

import unittest

from max_grounding.errors import InvalidGroundingRequest
from max_grounding.models import GroundingRequest
from max_grounding.policy import GroundingPolicy


class GroundingPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = GroundingPolicy()

    def test_rejects_blank_query(self) -> None:
        with self.assertRaises(InvalidGroundingRequest):
            self.policy.validate_request(GroundingRequest(query="   "))

    def test_rejects_more_than_two_search_rounds(self) -> None:
        with self.assertRaises(InvalidGroundingRequest):
            self.policy.validate_request(
                GroundingRequest(query="test", max_search_rounds=3)
            )

    def test_normalizes_query_and_locale(self) -> None:
        request = GroundingRequest(
            query="  latest   product   price  ",
            language=" ID ",
            country=" id ",
        )
        validated = self.policy.validate_request(request)
        self.assertEqual(validated.query, "latest product price")
        self.assertEqual(validated.language, "id")
        self.assertEqual(validated.country, "ID")

    def test_rejects_invalid_evidence_and_result_limits(self) -> None:
        for request in (
            GroundingRequest(query="x", min_evidence_sources=0),
            GroundingRequest(query="x", results_per_call=0),
            GroundingRequest(query="x", results_per_call=11),
        ):
            with self.subTest(request=request):
                with self.assertRaises(InvalidGroundingRequest):
                    self.policy.validate_request(request)


if __name__ == "__main__":
    unittest.main()

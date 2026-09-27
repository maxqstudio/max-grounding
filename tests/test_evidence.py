from __future__ import annotations

import unittest

from max_grounding.evidence import normalize_candidates
from max_grounding.models import SourceCandidate


class EvidenceNormalizationTests(unittest.TestCase):
    def test_canonical_url_dedupes_host_case_fragment_and_trailing_slash(self) -> None:
        candidates = (
            SourceCandidate(
                url="https://Example.com/product/#offer",
                title="A",
                snippet="first",
                provider="fake",
                rank=1,
            ),
            SourceCandidate(
                url="https://example.com/product",
                title="B",
                snippet="duplicate",
                provider="fake",
                rank=2,
            ),
        )
        seen: set[str] = set()
        sources = normalize_candidates(candidates, seen)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].canonical_url, "https://example.com/product")
        self.assertEqual(seen, {"https://example.com/product"})

    def test_unsupported_or_hostless_urls_are_discarded(self) -> None:
        candidates = (
            SourceCandidate(
                url="file:///etc/passwd",
                title="bad",
                snippet="bad",
                provider="fake",
                rank=1,
            ),
            SourceCandidate(
                url="javascript:alert(1)",
                title="bad2",
                snippet="bad2",
                provider="fake",
                rank=2,
            ),
        )
        self.assertEqual(normalize_candidates(candidates, set()), ())


if __name__ == "__main__":
    unittest.main()

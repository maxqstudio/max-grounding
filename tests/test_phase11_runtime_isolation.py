"""Guard local Qdrant restart fixture isolation from the production index."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from integration.phase11_services import PHASE11_E2E_COLLECTION, services
from max_grounding.providers.qdrant import DEFAULT_COLLECTION


class Phase11E2EIsolationTests(unittest.TestCase):
    def test_restart_fixture_has_distinct_collection_identity(self) -> None:
        self.assertNotEqual(PHASE11_E2E_COLLECTION, DEFAULT_COLLECTION)

    def test_realstack_fixture_explicitly_uses_isolated_qdrant_collection(self) -> None:
        with (
            patch("integration.phase11_services.OllamaEmbeddingProvider") as embedding,
            patch("integration.phase11_services.QdrantVectorStore") as store,
            patch.dict(
                os.environ,
                {"OLLAMA_URL": "http://127.0.0.1:11434",
                 "QDRANT_URL": "http://127.0.0.1:6333"},
            ),
        ):
            services()
        embedding.assert_called_once_with(
            "http://127.0.0.1:11434", timeout_seconds=120
        )
        store.assert_called_once_with(
            "http://127.0.0.1:6333",
            collection=PHASE11_E2E_COLLECTION,
            timeout_seconds=30,
        )
        embedding.return_value.verify_runtime.assert_called_once()
        store.return_value.verify_runtime.assert_called_once()


if __name__ == "__main__":
    unittest.main()

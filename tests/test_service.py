from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from max_grounding.errors import ServiceConfigurationError, ServiceOperationError
from max_grounding.models import (
    EvidencePack,
    EvidenceStatus,
    FetchedDocument,
    PersistentIndexResult,
    SemanticHit,
    SourceCandidate,
    TextChunk,
)
from max_grounding.service import (
    MAX_INDEX_URLS,
    GroundingService,
    ProductionSettings,
    ServiceReadiness,
    build_production_runtime,
)


API_KEY = "k" * 32


def doc(url: str = "https://example.com/a") -> FetchedDocument:
    text = "bounded grounding evidence"
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


class Phase12ServiceTests(unittest.TestCase):
    def settings(self) -> ProductionSettings:
        return ProductionSettings.from_mapping(
            {
                "MAX_GROUNDING_SEARXNG_URL": "http://searxng:8080",
                "MAX_GROUNDING_OLLAMA_URL": "http://ollama:11434",
                "MAX_GROUNDING_QDRANT_URL": "http://qdrant:6333",
                "MAX_GROUNDING_API_KEY": API_KEY,
                "MAX_GROUNDING_ALLOWED_HOSTS": "localhost:*,127.0.0.1:*,grounding.example",
                "MAX_GROUNDING_ALLOWED_ORIGINS": "https://client.example",
            }
        )

    def service(self) -> tuple[GroundingService, MagicMock, MagicMock, MagicMock, MagicMock]:
        search = MagicMock()
        search.search.return_value = (
            SourceCandidate(
                url="https://example.com/a",
                title="Example",
                snippet="Evidence",
                provider="test",
                rank=1,
            ),
        )
        embed = MagicMock()
        embed.model_name = "qwen3-embedding:0.6b"
        embed.embedding_dimension = 1024
        store = MagicMock()
        store.model_name = "qwen3-embedding:0.6b"
        store.embedding_dimension = 1024
        store.schema_version = 1
        store.collection_name = "max_grounding_qwen3_embedding_0_6b_v1"
        fetcher = MagicMock(side_effect=lambda url: doc(url))
        return GroundingService(search, embed, store, fetcher=fetcher), search, embed, store, fetcher

    def test_settings_require_exact_nonempty_runtime_configuration_and_strong_api_key(self) -> None:
        settings = self.settings()
        self.assertEqual(settings.api_key, API_KEY)
        self.assertEqual(settings.searxng_url, "http://searxng:8080")
        self.assertEqual(settings.allowed_origins, ("https://client.example",))
        self.assertIn("grounding.example", settings.allowed_hosts)

        required = (
            "MAX_GROUNDING_SEARXNG_URL",
            "MAX_GROUNDING_OLLAMA_URL",
            "MAX_GROUNDING_QDRANT_URL",
            "MAX_GROUNDING_API_KEY",
        )
        base = {
            "MAX_GROUNDING_SEARXNG_URL": "http://searxng:8080",
            "MAX_GROUNDING_OLLAMA_URL": "http://ollama:11434",
            "MAX_GROUNDING_QDRANT_URL": "http://qdrant:6333",
            "MAX_GROUNDING_API_KEY": API_KEY,
        }
        for missing in required:
            broken = dict(base)
            del broken[missing]
            with self.subTest(missing=missing):
                with self.assertRaises(ServiceConfigurationError):
                    ProductionSettings.from_mapping(broken)

        weak = dict(base)
        weak["MAX_GROUNDING_API_KEY"] = "too-short"
        with self.assertRaises(ServiceConfigurationError):
            ProductionSettings.from_mapping(weak)

    def test_build_runtime_constructs_only_accepted_phase11_dependencies(self) -> None:
        settings = self.settings()
        with (
            patch("max_grounding.service.SearxngProvider") as searxng,
            patch("max_grounding.service.OllamaEmbeddingProvider") as ollama,
            patch("max_grounding.service.QdrantVectorStore") as qdrant,
        ):
            runtime = build_production_runtime(settings)

        self.assertIsInstance(runtime, GroundingService)
        searxng.assert_called_once_with(settings.searxng_url)
        ollama.assert_called_once_with(settings.ollama_url)
        qdrant.assert_called_once_with(
            settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )

    def test_service_search_and_fetch_reuse_accepted_core(self) -> None:
        service, search, _embed, _store, fetcher = self.service()

        pack = service.search_web(
            "gold reserves",
            language="en",
            country="ID",
            max_search_rounds=1,
            results_per_call=2,
            min_evidence_sources=1,
        )
        fetched = service.fetch_evidence("https://example.com/a")

        self.assertEqual(pack.status, EvidenceStatus.GROUNDED)
        self.assertEqual(pack.sources[0].url, "https://example.com/a")
        self.assertEqual(search.search.call_count, 1)
        self.assertEqual(fetched.url, "https://example.com/a")
        fetcher.assert_called_once_with("https://example.com/a")

    def test_index_and_query_delegate_to_phase11_persistent_contracts(self) -> None:
        service, _search, embed, store, fetcher = self.service()
        indexed = PersistentIndexResult(
            chunks_indexed=2,
            vectors_indexed=2,
            embedding_model="qwen3-embedding:0.6b",
            embedding_dimension=1024,
            schema_version=1,
            collection_name="max_grounding_qwen3_embedding_0_6b_v1",
        )
        hit = SemanticHit(
            chunk=TextChunk(
                chunk_id="abc",
                source_url="https://example.com/a",
                chunk_index=0,
                text="evidence",
                token_count=1,
            ),
            score=0.9,
            rank=1,
        )

        with (
            patch("max_grounding.service.index_documents", return_value=indexed) as indexer,
            patch(
                "max_grounding.service.retrieve_persistent_semantic",
                return_value=(hit,),
            ) as query,
        ):
            result = service.index_evidence(
                ("https://example.com/a", "https://example.com/b")
            )
            hits = service.query_evidence("gold reserves", limit=3)

        self.assertEqual(result, indexed)
        self.assertEqual(hits, (hit,))
        self.assertEqual(fetcher.call_count, 2)
        indexer.assert_called_once()
        query.assert_called_once_with(
            "gold reserves",
            embed,
            store,
            limit=3,
        )

    def test_index_input_is_bounded_and_generators_fail_closed(self) -> None:
        service, *_ = self.service()
        with self.assertRaises(ServiceOperationError):
            service.index_evidence(url for url in ("https://example.com/a",))
        with self.assertRaises(ServiceOperationError):
            service.index_evidence(
                tuple(
                    f"https://example.com/{index}"
                    for index in range(MAX_INDEX_URLS + 1)
                )
            )

    def test_readiness_checks_local_runtime_dependencies_without_leaking_secrets(self) -> None:
        service, _search, embed, store, _fetcher = self.service()
        readiness = service.readiness()

        self.assertEqual(
            readiness,
            ServiceReadiness(
                ready=True,
                ollama=True,
                qdrant=True,
                search_configured=True,
            ),
        )
        embed.verify_runtime.assert_called_once_with()
        store.verify_runtime.assert_called_once_with()

        embed.verify_runtime.side_effect = RuntimeError("secret=do-not-leak")
        with self.assertRaises(ServiceOperationError) as ctx:
            service.readiness()
        self.assertNotIn("do-not-leak", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

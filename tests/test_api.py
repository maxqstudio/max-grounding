from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from max_grounding.errors import ServiceOperationError
from max_grounding.models import (
    EvidencePack,
    EvidenceSource,
    EvidenceStatus,
    FetchedDocument,
    PersistentIndexResult,
    SemanticHit,
    TextChunk,
)
from max_grounding.api import MAX_REQUEST_BODY_BYTES, create_rest_app
from max_grounding.service import ServiceReadiness


API_KEY = "a" * 32


def service_mock() -> MagicMock:
    service = MagicMock()
    service.readiness.return_value = ServiceReadiness(
        ready=True,
        ollama=True,
        qdrant=True,
        search_configured=True,
    )
    service.search_web.return_value = EvidencePack(
        query="gold",
        status=EvidenceStatus.GROUNDED,
        sources=(
            EvidenceSource(
                canonical_url="https://example.com/gold",
                url="https://example.com/gold",
                title="Gold",
                snippet="Evidence",
                provider="test",
                rank=1,
            ),
        ),
        search_calls_used=1,
        reason="minimum evidence reached",
    )
    text = "evidence"
    service.fetch_evidence.return_value = FetchedDocument(
        url="https://example.com/gold",
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text),
        text=text,
    )
    service.index_evidence.return_value = PersistentIndexResult(
        chunks_indexed=1,
        vectors_indexed=1,
        embedding_model="qwen3-embedding:0.6b",
        embedding_dimension=1024,
        schema_version=1,
        collection_name="max_grounding_qwen3_embedding_0_6b_v1",
    )
    service.query_evidence.return_value = (
        SemanticHit(
            chunk=TextChunk(
                chunk_id="abc",
                source_url="https://example.com/gold",
                chunk_index=0,
                text="evidence",
                token_count=1,
            ),
            score=0.9,
            rank=1,
        ),
    )
    return service


class Phase12RestApiTests(unittest.TestCase):
    def client(self, service: MagicMock | None = None) -> tuple[TestClient, MagicMock]:
        runtime = service or service_mock()
        app = create_rest_app(
            runtime,
            api_key=API_KEY,
            allowed_hosts=("testserver", "localhost:*", "127.0.0.1:*"),
            allowed_origins=(),
        )
        return TestClient(app), runtime

    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {API_KEY}"}

    def test_health_is_public_but_readiness_and_v1_operations_require_auth(self) -> None:
        client, _service = self.client()
        self.assertEqual(client.get("/healthz").status_code, 200)
        for method, path, payload in (
            ("get", "/readyz", None),
            ("post", "/v1/search", {"query": "gold"}),
            ("post", "/v1/fetch", {"url": "https://example.com/gold"}),
            ("post", "/v1/index", {"urls": ["https://example.com/gold"]}),
            ("post", "/v1/query", {"query": "gold", "limit": 3}),
        ):
            if method == "get":
                response = client.get(path)
            else:
                response = client.post(path, json=payload)
            with self.subTest(path=path):
                self.assertEqual(response.status_code, 401)

    def test_authenticated_rest_operations_preserve_structured_provenance(self) -> None:
        client, service = self.client()

        ready = client.get("/readyz", headers=self.auth())
        search = client.post("/v1/search", headers=self.auth(), json={"query": "gold"})
        fetched = client.post(
            "/v1/fetch",
            headers=self.auth(),
            json={"url": "https://example.com/gold"},
        )
        indexed = client.post(
            "/v1/index",
            headers=self.auth(),
            json={"urls": ["https://example.com/gold"]},
        )
        queried = client.post(
            "/v1/query",
            headers=self.auth(),
            json={"query": "gold", "limit": 3},
        )

        self.assertTrue(ready.json()["ready"])
        self.assertEqual(search.json()["sources"][0]["url"], "https://example.com/gold")
        self.assertEqual(fetched.json()["fetched_from_ip"], "93.184.216.34")
        self.assertEqual(indexed.json()["embedding_dimension"], 1024)
        self.assertEqual(queried.json()[0]["chunk"]["chunk_id"], "abc")
        service.search_web.assert_called_once()
        service.fetch_evidence.assert_called_once()
        service.index_evidence.assert_called_once_with(("https://example.com/gold",))
        service.query_evidence.assert_called_once_with("gold", limit=3)

    def test_api_key_header_and_wrong_tokens_fail_closed(self) -> None:
        client, _service = self.client()
        self.assertEqual(
            client.post(
                "/v1/search",
                headers={"X-API-Key": API_KEY},
                json={"query": "gold"},
            ).status_code,
            200,
        )
        self.assertEqual(
            client.post(
                "/v1/search",
                headers={"Authorization": "Bearer wrong"},
                json={"query": "gold"},
            ).status_code,
            401,
        )

    def test_request_body_cap_applies_before_service_execution(self) -> None:
        client, service = self.client()
        body = '{"query":"' + ("x" * MAX_REQUEST_BODY_BYTES) + '"}'
        response = client.post(
            "/v1/search",
            headers={**self.auth(), "Content-Type": "application/json"},
            content=body,
        )
        self.assertEqual(response.status_code, 413)
        service.search_web.assert_not_called()

    def test_service_errors_are_bounded_and_do_not_leak_exception_text(self) -> None:
        service = service_mock()
        service.search_web.side_effect = ServiceOperationError(
            "credential=must-not-leak"
        )
        client, _ = self.client(service)
        response = client.post(
            "/v1/search",
            headers=self.auth(),
            json={"query": "gold"},
        )
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("must-not-leak", response.text)
        self.assertEqual(response.json()["error"], "service_unavailable")


if __name__ == "__main__":
    unittest.main()

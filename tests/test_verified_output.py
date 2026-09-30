from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from max_grounding.api import create_rest_app
from max_grounding.models import FetchedDocument
from max_grounding.mcp_server import create_mcp_server, mcp_fetch_evidence
from max_grounding.service import GroundingService


API_KEY = "v" * 32


def document(url: str, text: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


def runtime(documents: dict[str, FetchedDocument]) -> GroundingService:
    def fetcher(url: str) -> FetchedDocument:
        return documents[url]

    return GroundingService(
        search_provider=MagicMock(),
        embedding_provider=MagicMock(),
        vector_store=MagicMock(),
        fetcher=fetcher,
    )


def rest_client(service: GroundingService) -> TestClient:
    app = create_rest_app(
        service,
        api_key=API_KEY,
        allowed_hosts=("testserver", "localhost:*", "127.0.0.1:*"),
        allowed_origins=(),
    )
    return TestClient(app)


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


def claim(claim_id: str, key: str, value: str) -> dict[str, str]:
    return {
        "claim_id": claim_id,
        "text": f"{key} is {value}.",
        "claim_key": key,
        "value": value,
    }


def fetch_ref(client: TestClient, url: str) -> str:
    response = client.post("/v1/fetch", headers=auth(), json={"url": url})
    if response.status_code != 200:
        raise AssertionError(response.text)
    evidence_ref = response.json().get("evidence_ref")
    if not isinstance(evidence_ref, str) or not evidence_ref:
        raise AssertionError("secure fetch did not return an opaque evidence_ref")
    if evidence_ref == url:
        raise AssertionError("evidence_ref must not expose the source URL as its identity")
    return evidence_ref


class VerifiedOutputBoundaryTests(unittest.TestCase):
    def test_fetch_then_verify_returns_server_canonical_citation(self) -> None:
        url = "https://source.example/product"
        text = "Product name: Sample Widget. Package size: 330 ml."
        service = runtime({url: document(url, text)})
        client = rest_client(service)
        evidence_ref = fetch_ref(client, url)

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("package", "package_size", "330 ml")],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(packet["verifications"][0]["status"], "supported")
        self.assertEqual(len(packet["synthesis_claims"]), 1)
        citation = packet["synthesis_claims"][0]["citations"][0]
        self.assertEqual(citation["source_url"], url)
        self.assertTrue(citation["chunk_id"])
        self.assertIn("330 ml", citation["text"])

    def test_unknown_or_forged_evidence_reference_fails_closed(self) -> None:
        url = "https://source.example/product"
        service = runtime({url: document(url, "Brand: SampleCo.")})
        client = rest_client(service)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("brand", "brand", "SampleCo")],
                "evidence_refs": ["forged-reference"],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertNotIn("synthesis_claims", response.json())

    def test_caller_cannot_supply_graph_scores_or_citation_identity(self) -> None:
        client = rest_client(runtime({}))
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    {
                        **claim("brand", "brand", "SampleCo"),
                        "source_url": "https://attacker.example/fake",
                        "chunk_id": "caller-chosen",
                        "citation_text": "caller-authored evidence",
                        "score": 1.0,
                        "status": "supported",
                    }
                ],
                "evidence_refs": ["forged-reference"],
                "required_sources": 1,
                "evidence_graph": {"assertions": []},
            },
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_partial_claim_is_blocked_from_synthesis(self) -> None:
        url = "https://source.example/product"
        client = rest_client(runtime({url: document(url, "Brand: SampleCo.")}))
        evidence_ref = fetch_ref(client, url)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("brand", "brand", "SampleCo")],
                "evidence_refs": [evidence_ref],
                "required_sources": 2,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(packet["verifications"][0]["status"], "partially_supported")
        self.assertEqual(packet["synthesis_claims"], [])
        self.assertEqual(packet["blocked_claims"][0]["status"], "partially_supported")

    def test_conflicted_claims_are_blocked_from_synthesis(self) -> None:
        first = "https://one.example/product"
        second = "https://two.example/product"
        client = rest_client(runtime({
            first: document(first, "Brand: SampleCo."),
            second: document(second, "Brand: OtherCo."),
        }))
        refs = [fetch_ref(client, first), fetch_ref(client, second)]
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    claim("brand-a", "brand", "SampleCo"),
                    claim("brand-b", "brand", "OtherCo"),
                ],
                "evidence_refs": refs,
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(
            [item["status"] for item in packet["verifications"]],
            ["conflicted", "conflicted"],
        )
        self.assertEqual(packet["synthesis_claims"], [])
        self.assertEqual(len(packet["blocked_claims"]), 2)

    def test_unsupported_claim_is_blocked_without_citation(self) -> None:
        url = "https://source.example/product"
        client = rest_client(runtime({url: document(url, "Material: paper.")}))
        evidence_ref = fetch_ref(client, url)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("brand", "brand", "Invented Brand")],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(packet["verifications"][0]["status"], "unsupported")
        self.assertEqual(packet["verifications"][0]["citations"], [])
        self.assertEqual(packet["synthesis_claims"], [])

    def test_duplicate_canonical_source_counts_once(self) -> None:
        requested_one = "https://source.example/product?mirror=1"
        requested_two = "https://source.example/product?mirror=2"
        canonical = "https://source.example/product"
        client = rest_client(runtime({
            requested_one: document(canonical, "Brand: SampleCo."),
            requested_two: document(canonical, "Brand: SampleCo."),
        }))
        refs = [fetch_ref(client, requested_one), fetch_ref(client, requested_two)]
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("brand", "brand", "SampleCo")],
                "evidence_refs": refs,
                "required_sources": 2,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        verification = response.json()["verifications"][0]
        self.assertEqual(verification["supporting_source_count"], 1)
        self.assertEqual(verification["status"], "partially_supported")

    def test_evidence_for_one_field_cannot_support_another_field(self) -> None:
        url = "https://source.example/product"
        text = "Package size: 330 ml."
        client = rest_client(runtime({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    claim("package", "package_size", "330 ml"),
                    claim("brand", "brand", "SampleCo"),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        verifications = packet["verifications"]
        self.assertEqual(verifications[0]["status"], "supported")
        self.assertEqual(verifications[1]["status"], "unsupported")
        self.assertEqual(
            packet["synthesis_claims"][0]["claim"]["claim_id"],
            "package",
        )

    def test_mcp_exposes_same_verified_output_boundary(self) -> None:
        url = "https://source.example/product"
        service = runtime({url: document(url, "Brand: SampleCo.")})
        server = create_mcp_server(service)
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertEqual(
            names,
            {"search_web", "fetch_evidence", "index_evidence", "query_evidence", "verify_claims"},
        )
        fetched = mcp_fetch_evidence(service, url=url)
        evidence_ref = fetched["evidence_ref"]

        async def invoke():
            return await server.call_tool(
                "verify_claims",
                {
                    "claims": [claim("brand", "brand", "SampleCo")],
                    "evidence_refs": [evidence_ref],
                    "required_sources": 1,
                },
            )

        result = asyncio.run(invoke())
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["synthesis_claims"][0]["status"], "supported")

    def test_missing_verification_auth_is_rejected(self) -> None:
        client = rest_client(runtime({}))
        response = client.post(
            "/v1/verify",
            json={"claims": [], "evidence_refs": [], "required_sources": 1},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()

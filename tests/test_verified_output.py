from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from max_grounding.api import MAX_REQUEST_BODY_BYTES, create_rest_app
from max_grounding.evidence_authority import EvidenceAuthority
from max_grounding.errors import InvalidGroundingRequest, ServiceOperationError
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
                "evidence_refs": ["A" * 43],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json(), {"error": "invalid_request"})
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
                        "text": "SampleCo is the safest and top-selling brand.",
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
                    claim("brand", "brand", "330 ml"),
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

    def test_barcode_binding_does_not_support_a_brand_claim(self) -> None:
        url = "https://source.example/product"
        barcode = "8993163502059"
        text = f"Barcode: {barcode}."
        client = rest_client(runtime({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    claim("barcode", "barcode_binding", barcode),
                    claim("brand", "brand", barcode),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        verifications = response.json()["verifications"]
        self.assertEqual(verifications[0]["status"], "supported")
        self.assertEqual(verifications[1]["status"], "unsupported")
        self.assertEqual(len(response.json()["synthesis_claims"]), 1)

    def test_product_fields_require_same_source_barcode_binding(self) -> None:
        barcode_url = "https://one.example/barcode"
        brand_url = "https://two.example/brand"
        client = rest_client(
            runtime(
                {
                    barcode_url: document(barcode_url, "Barcode: 8993163502059."),
                    brand_url: document(brand_url, "Brand: SampleCo."),
                }
            )
        )
        refs = [fetch_ref(client, barcode_url), fetch_ref(client, brand_url)]
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    claim("barcode", "barcode_binding", "8993163502059"),
                    claim("brand", "brand", "SampleCo"),
                ],
                "evidence_refs": refs,
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(packet["verifications"][0]["status"], "supported")
        self.assertEqual(packet["verifications"][1]["status"], "unsupported")
        self.assertEqual(len(packet["synthesis_claims"]), 1)

    def test_product_fields_are_supported_when_same_source_binds_barcode(self) -> None:
        url = "https://source.example/product"
        text = "Barcode: 8993163502059. Brand: SampleCo."
        client = rest_client(runtime({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    claim("barcode", "barcode_binding", "8993163502059"),
                    claim("brand", "brand", "SampleCo"),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["synthesis_claims"]), 2)

    def test_mixed_barcode_identities_are_rejected(self) -> None:
        url = "https://source.example/product"
        client = rest_client(runtime({url: document(url, "Barcode: 8993163502059.")}))
        evidence_ref = fetch_ref(client, url)
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    claim("barcode-a", "barcode_binding", "8993163502059"),
                    claim("barcode-b", "barcode_binding", "8993163502066"),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_malformed_evidence_reference_rejected_without_echo(self) -> None:
        client = rest_client(runtime({}))
        marker = "x" * 64
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("brand", "brand", "SampleCo")],
                "evidence_refs": [marker],
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json(), {"error": "invalid_request"})
        self.assertNotIn(marker, response.text)

    def test_oversized_request_is_rejected(self) -> None:
        client = rest_client(runtime({}))
        response = client.post(
            "/v1/verify",
            headers={**auth(), "Content-Type": "application/json"},
            content=b" " * (MAX_REQUEST_BODY_BYTES + 1),
        )
        self.assertEqual(response.status_code, 413, response.text)
        self.assertEqual(response.json(), {"error": "request_too_large"})

    def test_wrong_authentication_is_rejected_before_verification(self) -> None:
        client = rest_client(runtime({}))
        response = client.post(
            "/v1/verify",
            headers={"Authorization": "Bearer wrong-token"},
            json={"claims": [], "evidence_refs": []},
        )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json(), {"error": "unauthorized"})

    def test_malformed_request_returns_bounded_error_without_input_echo(self) -> None:
        client = rest_client(runtime({}))
        marker = "sensitive-caller-text-marker"
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    {**claim("brand", "brand", "SampleCo"), "caller_marker": marker}
                ],
                "evidence_refs": ["A" * 43],
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json(), {"error": "invalid_request"})
        self.assertNotIn(marker, response.text)

    def test_internal_verification_error_is_bounded(self) -> None:
        client = rest_client(runtime({}))
        client.app.state.grounding_service.verify_candidate_claims = MagicMock(
            side_effect=ServiceOperationError("internal secret diagnostic")
        )
        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [claim("brand", "brand", "SampleCo")],
                "evidence_refs": ["A" * 43],
            },
        )
        self.assertEqual(response.status_code, 502, response.text)
        self.assertEqual(response.json(), {"error": "service_unavailable"})
        self.assertNotIn("internal secret diagnostic", response.text)

    def test_mounted_mcp_auth_is_required_for_verify_path(self) -> None:
        client = rest_client(runtime({}))
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        for headers in ({}, {"Authorization": "Bearer wrong-token"}):
            response = client.post("/mcp", headers=headers, json=body)
            self.assertEqual(response.status_code, 401, response.text)
            self.assertEqual(response.json(), {"error": "unauthorized"})

    def test_expired_or_restarted_registry_rejects_old_reference(self) -> None:
        monotonic = [100.0]
        now = datetime(2026, 9, 30, tzinfo=timezone.utc)
        registry = EvidenceAuthority(
            ttl_seconds=60,
            capacity=2,
            monotonic_clock=lambda: monotonic[0],
            utc_clock=lambda: now,
        )
        issued = registry.issue(document("https://source.example/product", "Brand: SampleCo."))
        reference = issued.evidence_ref
        self.assertIsInstance(reference, str)
        self.assertEqual(len(registry.resolve([reference])), 1)

        restarted_registry = EvidenceAuthority(
            ttl_seconds=60,
            capacity=2,
            monotonic_clock=lambda: monotonic[0],
            utc_clock=lambda: now,
        )
        with self.assertRaises(InvalidGroundingRequest):
            restarted_registry.resolve([reference])

        monotonic[0] = 160.0
        with self.assertRaises(InvalidGroundingRequest):
            registry.resolve([reference])

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

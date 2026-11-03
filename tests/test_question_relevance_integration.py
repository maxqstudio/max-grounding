"""REST/MCP scoped verification must not promote a merely literal citation."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from max_grounding.api import create_rest_app
from max_grounding.models import FetchedDocument
from max_grounding.mcp_server import mcp_verify_claims
from max_grounding.service import GroundingService

API_KEY = "r" * 32
CODE = "8993163502059"
URL = "https://source.example/product"


def source(text: str) -> FetchedDocument:
    return FetchedDocument(
        url=URL,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


def runtime(text: str, **kwargs) -> GroundingService:
    return GroundingService(
        search_provider=MagicMock(),
        embedding_provider=MagicMock(),
        vector_store=MagicMock(),
        fetcher=lambda url: source(text),
        **kwargs,
    )


def api(service: GroundingService) -> TestClient:
    return TestClient(
        create_rest_app(
            service, api_key=API_KEY, allowed_hosts=("testserver",),
            allowed_origins=(),
        )
    )


def headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


def scope(question: str, keys: list[str]) -> dict:
    return {
        "question": question,
        "requested_claim_keys": keys,
        "subject_anchors": [],
    }


def proposal(key: str, value: str, span_ref: str, mode: str = "STRUCTURED_FIELD") -> dict:
    return {
        "claim_key": key,
        "value": value,
        "verification_mode": mode,
        "evidence_span_refs": [span_ref],
    }


def refs(client: TestClient, query: str) -> tuple[str, str]:
    fetched = client.post("/v1/fetch", json={"url": URL}, headers=headers())
    assert fetched.status_code == 200, fetched.text
    evidence = fetched.json()["evidence_ref"]
    selected = client.post(
        "/v1/evidence/spans",
        json={"evidence_ref": evidence, "query": query, "limit": 4},
        headers=headers(),
    )
    assert selected.status_code == 200, selected.text
    return evidence, selected.json()["spans"][0]["evidence_span_ref"]


class ScopedQuestionAcceptanceTests(unittest.TestCase):
    def test_exact_identifier_brand_yields_question_sufficient(self) -> None:
        client = api(runtime(f"Barcode | {CODE} | Brand | SampleCo"))
        ev, sp = refs(client, CODE)
        question = f"What brand is linked to barcode {CODE}?"
        response = client.post(
            "/v1/verify",
            headers=headers(),
            json={
                "question_scope": scope(question, ["brand"]),
                "claims": [proposal("brand", "SampleCo", sp)],
                "evidence_refs": [ev],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        output = response.json()
        self.assertEqual(output["question_assessment"]["status"], "SUFFICIENT")
        self.assertEqual(output["question_assessment"]["reason_codes"], [])
        self.assertEqual(len(output["synthesis_claims"]), 1)
        self.assertEqual(output["synthesis_claims"][0]["claim"]["value"], "SampleCo")

    def test_prefix_only_source_cannot_promote_exact_barcode_answer(self) -> None:
        client = api(runtime("Barcode prefix | 8993163 | Brand | SampleCo"))
        ev, sp = refs(client, "8993163")
        response = client.post(
            "/v1/verify",
            headers=headers(),
            json={
                "question_scope": scope(
                    f"What brand is linked to barcode {CODE}?", ["brand"]
                ),
                "claims": [proposal("brand", "SampleCo", sp)],
                "evidence_refs": [ev],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        output = response.json()
        self.assertEqual(output["question_assessment"]["status"], "INSUFFICIENT")
        self.assertEqual(output["synthesis_claims"], [])
        self.assertEqual(len(output["blocked_claims"]), 1)

    def test_true_but_unrequested_quote_must_not_be_question_answer(self) -> None:
        text = f"Barcode | {CODE} | Brand | SampleCo | The shop ships worldwide"
        client = api(runtime(text))
        ev, sp = refs(client, CODE)
        response = client.post(
            "/v1/verify",
            headers=headers(),
            json={
                "question_scope": scope(
                    f"What does barcode {CODE} indicate?", ["exact_evidence"]
                ),
                "claims": [
                    proposal(
                        "exact_evidence", "The shop ships worldwide",
                        sp, "EXTRACTIVE_STATEMENT",
                    )
                ],
                "evidence_refs": [ev],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        output = response.json()
        self.assertEqual(output["question_assessment"]["status"], "INSUFFICIENT")
        self.assertIn(
            "EXTRACTIVE_NOT_REQUESTED",
            output["question_assessment"]["reason_codes"],
        )
        self.assertEqual(output["synthesis_claims"], [])

    def test_wrong_declared_keys_are_refused_even_when_quote_is_true(self) -> None:
        client = api(runtime(f"Barcode | {CODE} | Brand | SampleCo"))
        ev, sp = refs(client, CODE)
        response = client.post(
            "/v1/verify",
            headers=headers(),
            json={
                "question_scope": scope(
                    f"What brand is linked to barcode {CODE}?", ["exact_evidence"]
                ),
                "claims": [proposal("exact_evidence", "Brand | SampleCo", sp, "EXTRACTIVE_STATEMENT")],
                "evidence_refs": [ev],
                "required_sources": 1,
            },
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["error"], "invalid_request")

    def test_authority_requires_trusted_server_injected_role(self) -> None:
        question = f"Which manufacturer record gives the brand for GTIN {CODE}?"
        payload = {
            "question_scope": scope(question, ["brand"]),
            "claims": [proposal("brand", "SampleCo", "PLACEHOLDER")],
            "required_sources": 1,
        }
        for trusted in (None, {URL: "manufacturer"}):
            client = api(runtime(
                f"GTIN | {CODE} | Brand | SampleCo",
                trusted_source_roles=trusted,
            ))
            ev, sp = refs(client, CODE)
            payload["claims"][0]["evidence_span_refs"] = [sp]
            payload["evidence_refs"] = [ev]
            resp = client.post("/v1/verify", json=payload, headers=headers())
            self.assertEqual(resp.status_code, 200, resp.text)
            expected = "SUFFICIENT" if trusted else "INSUFFICIENT"
            self.assertEqual(resp.json()["question_assessment"]["status"], expected)
            self.assertEqual(len(resp.json()["synthesis_claims"]), 1 if trusted else 0)

    def test_mcp_direct_uses_same_question_scope_and_server_packet(self) -> None:
        svc = runtime(f"Barcode | {CODE} | Brand | SampleCo")
        item = svc.fetch_evidence(URL)
        selected = svc.select_evidence_spans(item.evidence_ref, query=CODE, limit=1)
        result = mcp_verify_claims(
            svc,
            claims=[proposal("brand", "SampleCo", selected[0].evidence_span_ref)],
            evidence_refs=[item.evidence_ref],
            required_sources=1,
            question_scope=scope(f"What brand is linked to barcode {CODE}?", ["brand"]),
        )
        self.assertEqual(result["question_assessment"]["status"], "SUFFICIENT")
        self.assertEqual(len(result["synthesis_claims"]), 1)


if __name__ == "__main__":
    unittest.main()

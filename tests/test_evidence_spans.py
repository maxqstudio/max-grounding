from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from max_grounding.api import create_rest_app
from max_grounding.evidence_authority import EvidenceAuthority
from max_grounding.errors import InvalidGroundingRequest
from max_grounding.models import FetchedDocument
from max_grounding.service import GroundingService


API_KEY = "s" * 32
BARCODE = "8993163502059"


def document(url: str, text: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


def service_for(documents: dict[str, FetchedDocument]) -> GroundingService:
    return GroundingService(
        search_provider=MagicMock(),
        embedding_provider=MagicMock(),
        vector_store=MagicMock(),
        fetcher=lambda url: documents[url],
    )


def rest_client(service: GroundingService) -> TestClient:
    return TestClient(
        create_rest_app(
            service,
            api_key=API_KEY,
            allowed_hosts=("testserver", "localhost:*", "127.0.0.1:*"),
            allowed_origins=(),
        )
    )


def auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"}


def fetch_ref(client: TestClient, url: str) -> str:
    response = client.post("/v1/fetch", headers=auth(), json={"url": url})
    if response.status_code != 200:
        raise AssertionError(response.text)
    return response.json()["evidence_ref"]


def select_spans(client: TestClient, evidence_ref: str, query: str) -> list[dict]:
    response = client.post(
        "/v1/evidence/spans",
        headers=auth(),
        json={"evidence_ref": evidence_ref, "query": query, "limit": 4},
    )
    if response.status_code != 200:
        raise AssertionError(response.text)
    return response.json()["spans"]


def proposal(
    claim_key: str,
    value: str,
    span_refs: list[str],
    mode: str = "STRUCTURED_FIELD",
) -> dict:
    return {
        "claim_key": claim_key,
        "value": value,
        "verification_mode": mode,
        "evidence_span_refs": span_refs,
    }


class EvidenceSpanSelectionTests(unittest.TestCase):
    def test_reference_tokens_have_distinct_prefixes_and_cross_type_rejection(self) -> None:
        registry = EvidenceAuthority()
        issued = registry.issue(
            document("https://source.example/item", "Barcode | 8993163502059")
        )
        span = registry.select_spans(
            issued.evidence_ref,
            query="8993163502059",
            limit=1,
        )[0]

        self.assertTrue(issued.evidence_ref.startswith("ev_"))
        self.assertTrue(span.evidence_span_ref.startswith("sp_"))
        with self.assertRaises(InvalidGroundingRequest):
            registry.resolve([span.evidence_span_ref])
        with self.assertRaises(InvalidGroundingRequest):
            registry.resolve_spans([issued.evidence_ref])

    def test_span_selection_returns_exact_server_owned_text_and_offsets(self) -> None:
        url = "https://source.example/item"
        text = f"Nama | MONTISS 200 S | Kode | {BARCODE} | Merk | MONTISS"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)

        spans = select_spans(client, evidence_ref, BARCODE)

        self.assertTrue(spans)
        for span in spans:
            self.assertEqual(span["evidence_ref"], evidence_ref)
            self.assertEqual(span["source_url"], url)
            self.assertEqual(
                text[span["start_offset"] : span["end_offset"]],
                span["excerpt"],
            )
            self.assertNotEqual(span["evidence_span_ref"], span["excerpt"])

    def test_forged_or_expired_span_reference_fails_closed(self) -> None:
        monotonic = [100.0]
        registry = EvidenceAuthority(
            ttl_seconds=30,
            monotonic_clock=lambda: monotonic[0],
            utc_clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        issued = registry.issue(document("https://source.example/item", "Brand | SampleCo"))
        with self.assertRaises(InvalidGroundingRequest):
            registry.resolve_spans(["sp_" + "A" * 40])

        span = registry.select_spans(issued.evidence_ref, query="SampleCo", limit=2)[0]
        monotonic[0] = 130.0
        with self.assertRaises(InvalidGroundingRequest):
            registry.resolve_spans([span.evidence_span_ref])

        restarted_registry = EvidenceAuthority(
            ttl_seconds=30,
            monotonic_clock=lambda: monotonic[0],
            utc_clock=lambda: datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        with self.assertRaises(InvalidGroundingRequest):
            restarted_registry.resolve_spans([span.evidence_span_ref])

    def test_flattened_fields_bind_barcode_name_and_brand_from_one_source(self) -> None:
        url = "https://source.example/catalog"
        text = f"Nama | MONTISS 200 S | Kode | {BARCODE} | Merk | MONTISS"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        spans = select_spans(client, evidence_ref, BARCODE)
        span_ref = spans[0]["evidence_span_ref"]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("barcode_binding", BARCODE, [span_ref]),
                    proposal("product_name", "MONTISS 200 S", [span_ref]),
                    proposal("brand", "MONTISS", [span_ref]),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(
            [item["status"] for item in packet["verifications"]],
            ["supported", "supported", "supported"],
        )
        self.assertEqual(len(packet["synthesis_claims"]), 3)
        self.assertTrue(
            all(item["citations"][0]["source_url"] == url for item in packet["synthesis_claims"])
        )

    def test_flattened_field_values_cannot_contaminate_another_claim_key(self) -> None:
        url = "https://source.example/catalog"
        text = f"Nama | 330 ml | Kode | {BARCODE} | Merk | MONTISS"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, BARCODE)[0]["evidence_span_ref"]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [proposal("brand", "330 ml", [span_ref])],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["verifications"][0]["status"], "unsupported")
        self.assertEqual(response.json()["synthesis_claims"], [])

    def test_indonesian_marketplace_labels_bind_valid_gtin_product_fields(self) -> None:
        first_url = "https://source.example/montiss-product-code"
        second_url = "https://source.example/montiss-marketplace"
        first_text = (
            f"Product name | Montiss Tissue (200Lembar) | Product code | {BARCODE} "
            "| Product type | Facial Tissue"
        )
        second_text = (
            f"Product name | MONTISS TISSUE 200'S BENDED | Merek | MONTISS "
            f"| Kategori | TISSUE | SKU | {BARCODE}"
        )
        client = rest_client(
            service_for(
                {
                    first_url: document(first_url, first_text),
                    second_url: document(second_url, second_text),
                }
            )
        )
        first_ref = fetch_ref(client, first_url)
        second_ref = fetch_ref(client, second_url)
        first_span = select_spans(client, first_ref, BARCODE)[0]
        second_span = select_spans(client, second_ref, BARCODE)[0]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal(
                        "barcode_binding",
                        BARCODE,
                        [
                            first_span["evidence_span_ref"],
                            second_span["evidence_span_ref"],
                        ],
                    ),
                    proposal(
                        "product_name",
                        "Montiss Tissue (200Lembar)",
                        [first_span["evidence_span_ref"]],
                    ),
                    proposal(
                        "brand",
                        "MONTISS",
                        [second_span["evidence_span_ref"]],
                    ),
                    proposal(
                        "product_type",
                        "TISSUE",
                        [second_span["evidence_span_ref"]],
                    ),
                    proposal(
                        "package_size",
                        "200Lembar",
                        [first_span["evidence_span_ref"]],
                    ),
                ],
                "evidence_refs": [first_ref, second_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["status"] for item in response.json()["verifications"]],
            ["supported"] * 5,
        )
        barcode_verification = response.json()["verifications"][0]
        self.assertEqual(barcode_verification["supporting_source_count"], 2)

    def test_flattened_marketplace_labels_and_literal_title_bind_product_claims(self) -> None:
        url = "https://source.example/flattened-marketplace"
        text = (
            "Menu Kategori ACCESSORIES ADULT DIAPERS TISSUE WOMENS CARE "
            "Tanpa Kategori TAS TAS BELANJA TISSUE WOMENS CARE "
            "Kategori Beranda Produk Promosi Keranjang "
            f"Lihat Keranjang Beranda Product Name: MONTISS TISSUE 200`S BENDED Merek MONTISS "
            f"Kategori TISSUE SKU {BARCODE} Stok Stok lebih dari 500"
        )
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, BARCODE)[0][
            "evidence_span_ref"
        ]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("barcode_binding", BARCODE, [span_ref]),
                    proposal(
                        "product_name",
                        "MONTISS TISSUE 200`S BENDED",
                        [span_ref],
                    ),
                    proposal("brand", "MONTISS", [span_ref]),
                    proposal("product_type", "TISSUE", [span_ref]),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(
            [item["status"] for item in packet["verifications"]],
            ["supported"] * 4,
        )
        type_claim = next(
            item for item in packet["synthesis_claims"]
            if item["claim"]["claim_key"] == "product_type"
        )
        self.assertEqual(type_claim["claim"]["value"], "TISSUE")
        self.assertEqual(len(packet["synthesis_claims"]), 4)
        title_claim = next(
            item for item in packet["synthesis_claims"]
            if item["claim"]["claim_key"] == "product_name"
        )
        self.assertEqual(title_claim["citations"][0]["text"], text)

    def test_extractive_statement_cannot_verify_structured_product_fields(self) -> None:
        url = "https://source.example/product-field-mode"
        text = f"Product name | MONTISS TISSUE | Brand | MONTISS | Barcode | {BARCODE}"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, BARCODE)[0][
            "evidence_span_ref"
        ]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("brand", "MONTISS", [span_ref], "EXTRACTIVE_STATEMENT")
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json(), {"error": "invalid_request"})

    def test_navigation_category_is_not_treated_as_a_product_type(self) -> None:
        url = "https://source.example/navigation"
        text = "Menu Kategori TISSUE Womens Care"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, "TISSUE")[0][
            "evidence_span_ref"
        ]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [proposal("product_type", "TISSUE", [span_ref])],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["verifications"][0]["status"], "unsupported")
        self.assertEqual(response.json()["synthesis_claims"], [])

    def test_inline_fields_stop_before_marketplace_navigation_and_promotion(self) -> None:
        url = "https://source.example/indonesian-product-page"
        text = (
            "Kategori: READY TO DRINK Brand: ADEM SARI "
            "Flashdeal: ADEM SARI CINGKU PET 350 ML Deskripsi Produk"
        )
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, "ADEM SARI")[0][
            "evidence_span_ref"
        ]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("brand", "ADEM SARI", [span_ref]),
                    proposal("product_type", "READY TO DRINK", [span_ref]),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [item["status"] for item in response.json()["verifications"]],
            ["supported", "supported"],
        )
        self.assertEqual(
            [item["claim"]["value"] for item in response.json()["synthesis_claims"]],
            ["ADEM SARI", "READY TO DRINK"],
        )

    def test_invalid_gtin_in_sku_does_not_bind_a_barcode_claim(self) -> None:
        invalid_barcode = "8993163502050"
        url = "https://source.example/invalid-check-digit"
        text = f"Nama | MONTISS TISSUE | SKU | {invalid_barcode}"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, invalid_barcode)[0][
            "evidence_span_ref"
        ]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("barcode_binding", invalid_barcode, [span_ref]),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["verifications"][0]["status"], "unsupported")

    def test_valid_gtin_is_extracted_from_labeled_value_with_page_footer(self) -> None:
        url = "https://source.example/barcode-with-footer"
        text = f"Share ABC BARCODE / SKU : {BARCODE} IPUNGCELL.ID www.ipungcell.id"
        client = rest_client(service_for({url: document(url, text)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, BARCODE)[0][
            "evidence_span_ref"
        ]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [proposal("barcode_binding", BARCODE, [span_ref])],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["verifications"][0]["status"], "supported")

    def test_barcode_and_product_fields_from_different_sources_do_not_bind(self) -> None:
        barcode_url = "https://source.example/code"
        product_url = "https://other.example/item"
        documents = {
            barcode_url: document(barcode_url, f"Kode | {BARCODE}"),
            product_url: document(product_url, "Nama | MONTISS 200 S | Merk | MONTISS"),
        }
        client = rest_client(service_for(documents))
        barcode_ref = fetch_ref(client, barcode_url)
        product_ref = fetch_ref(client, product_url)
        barcode_span = select_spans(client, barcode_ref, BARCODE)[0]["evidence_span_ref"]
        brand_span = select_spans(client, product_ref, "MONTISS")[0]["evidence_span_ref"]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("barcode_binding", BARCODE, [barcode_span]),
                    proposal("brand", "MONTISS", [brand_span]),
                ],
                "evidence_refs": [barcode_ref, product_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        self.assertEqual(packet["verifications"][0]["status"], "supported")
        self.assertEqual(packet["verifications"][1]["status"], "unsupported")
        self.assertEqual(len(packet["synthesis_claims"]), 1)

    def test_extractive_statement_uses_server_span_and_rejects_paraphrase(self) -> None:
        url = "https://source.example/docs"
        source = "The API validates public addresses before connecting."
        client = rest_client(service_for({url: document(url, source)}))
        evidence_ref = fetch_ref(client, url)
        span_ref = select_spans(client, evidence_ref, "public addresses")[0]["evidence_span_ref"]

        accepted = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal(
                        "extractive_statement",
                        "validates public addresses before connecting.",
                        [span_ref],
                        "EXTRACTIVE_STATEMENT",
                    )
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        accepted_packet = accepted.json()
        self.assertEqual(accepted_packet["synthesis_claims"][0]["status"], "supported")
        citation = accepted_packet["synthesis_claims"][0]["citations"][0]
        self.assertEqual(citation["text"], source)
        self.assertEqual(citation["source_url"], url)

        rejected = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal(
                        "extractive_statement",
                        "The service checks public IPs before opening a connection.",
                        [span_ref],
                        "EXTRACTIVE_STATEMENT",
                    )
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )
        self.assertEqual(rejected.status_code, 200, rejected.text)
        rejected_packet = rejected.json()
        self.assertEqual(rejected_packet["synthesis_claims"], [])
        self.assertEqual(
            rejected_packet["correction"]["reason_code"],
            "NON_CONTIGUOUS_EXACT_SPAN",
        )
        self.assertTrue(rejected_packet["correction"]["eligible"])

    def test_multiple_extractive_spans_are_not_concatenated_into_one_quote(self) -> None:
        url = "https://source.example/docs"
        first = "The API validates public addresses before connecting."
        second = "The fetcher rejects private targets before every redirect hop."
        client = rest_client(service_for({url: document(url, first + "\n" + second)}))
        evidence_ref = fetch_ref(client, url)
        first_ref = select_spans(client, evidence_ref, "public addresses")[0]["evidence_span_ref"]
        second_ref = select_spans(client, evidence_ref, "redirect hop")[0]["evidence_span_ref"]

        response = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [
                    proposal("extractive_statement", first, [first_ref], "EXTRACTIVE_STATEMENT"),
                    proposal("extractive_statement", second, [second_ref], "EXTRACTIVE_STATEMENT"),
                ],
                "evidence_refs": [evidence_ref],
                "required_sources": 1,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        packet = response.json()
        quotes = [item["citations"][0]["text"] for item in packet["synthesis_claims"]]
        self.assertEqual(quotes, [first, second])
        self.assertNotIn(first + " " + second, quotes)

    def test_same_source_spans_count_as_one_source_but_canonical_urls_are_independent(self) -> None:
        canonical = "https://source.example/shared"
        mirrors = {
            canonical + "?a=1": document(canonical, "Brand | SampleCo"),
            canonical + "?a=2": document(canonical, "Brand | SampleCo"),
            "https://independent.example/shared": document(
                "https://independent.example/shared", "Brand | SampleCo"
            ),
        }
        client = rest_client(service_for(mirrors))
        first_ref = fetch_ref(client, canonical + "?a=1")
        second_ref = fetch_ref(client, canonical + "?a=2")
        independent_ref = fetch_ref(client, "https://independent.example/shared")
        first_span = select_spans(client, first_ref, "SampleCo")[0]["evidence_span_ref"]
        second_span = select_spans(client, second_ref, "SampleCo")[0]["evidence_span_ref"]
        independent_span = select_spans(client, independent_ref, "SampleCo")[0]["evidence_span_ref"]

        one_source = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [proposal("brand", "SampleCo", [first_span, second_span])],
                "evidence_refs": [first_ref, second_ref],
                "required_sources": 2,
            },
        )
        self.assertEqual(one_source.status_code, 200, one_source.text)
        self.assertEqual(
            one_source.json()["verifications"][0]["supporting_source_count"],
            1,
        )
        self.assertEqual(one_source.json()["verifications"][0]["status"], "partially_supported")

        two_sources = client.post(
            "/v1/verify",
            headers=auth(),
            json={
                "claims": [proposal("brand", "SampleCo", [first_span, independent_span])],
                "evidence_refs": [first_ref, independent_ref],
                "required_sources": 2,
            },
        )
        self.assertEqual(two_sources.status_code, 200, two_sources.text)
        self.assertEqual(two_sources.json()["verifications"][0]["supporting_source_count"], 2)
        self.assertEqual(two_sources.json()["verifications"][0]["status"], "supported")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import MagicMock

from max_grounding.models import (
    EvidencePack,
    EvidenceSource,
    EvidenceStatus,
    FetchedDocument,
    PersistentIndexResult,
    SemanticHit,
    TextChunk,
)
from max_grounding.mcp_server import (
    create_mcp_server,
    mcp_fetch_evidence,
    mcp_index_evidence,
    mcp_query_evidence,
    mcp_search_web,
)


def runtime() -> MagicMock:
    service = MagicMock()
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
        reason="grounded",
    )
    service.fetch_evidence.return_value = FetchedDocument(
        url="https://example.com/gold",
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=8,
        text="evidence",
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


class Phase12McpTests(unittest.TestCase):
    def test_six_explicit_grounding_tools_are_registered(self) -> None:
        server = create_mcp_server(runtime())
        tools = asyncio.run(server.list_tools())
        names = {tool.name for tool in tools}
        self.assertEqual(
            names,
            {
                "search_web",
                "fetch_evidence",
                "select_evidence_spans",
                "index_evidence",
                "query_evidence",
                "verify_claims",
            },
        )
        for forbidden in ("research", "answer", "shell", "filesystem", "command"):
            self.assertNotIn(forbidden, names)

    def test_verify_tool_schema_explains_server_issued_evidence_refs(self) -> None:
        server = create_mcp_server(runtime())
        tools = asyncio.run(server.list_tools())
        verify = next(tool for tool in tools if tool.name == "verify_claims")

        definitions = verify.input_schema.get("$defs", {})
        self.assertIn("CandidateClaimProposal", definitions)
        claim_schema = definitions["CandidateClaimProposal"]
        self.assertFalse(claim_schema["additionalProperties"])
        self.assertEqual(
            set(claim_schema["required"]),
            {"claim_key", "value", "verification_mode", "evidence_span_refs"},
        )
        self.assertIn(
            "exact_evidence",
            claim_schema["properties"]["claim_key"]["description"],
        )
        self.assertIn(
            "verbatim source text",
            claim_schema["properties"]["value"]["description"],
        )
        claim_key_description = claim_schema["properties"]["claim_key"][
            "description"
        ]
        self.assertIn("Stable field name", claim_key_description)
        self.assertIn("never a field value", claim_key_description)
        self.assertIn(
            "never the whole source excerpt",
            claim_schema["properties"]["value"]["description"],
        )
        mode_schema = claim_schema["properties"]["verification_mode"]
        self.assertEqual(
            mode_schema["enum"],
            ["STRUCTURED_FIELD", "EXTRACTIVE_STATEMENT"],
        )
        self.assertIn("Required for every MCP", mode_schema["description"])
        self.assertIn("for product fields", mode_schema["description"])
        self.assertIn("only for claim_key=exact_evidence", mode_schema["description"])
        span_item_description = claim_schema["properties"]["evidence_span_refs"][
            "items"
        ]["description"]
        self.assertIn("call that tool first", span_item_description)
        self.assertIn("never invent", span_item_description)
        self.assertIn("sp_-prefixed", span_item_description)
        self.assertEqual(
            claim_schema["properties"]["evidence_span_refs"]["items"]["pattern"],
            r"^sp_[A-Za-z0-9_-]{40}$",
        )
        span_description = claim_schema["properties"]["evidence_span_refs"][
            "description"
        ]
        self.assertIn("Required for every MCP", span_description)
        self.assertIn("shares the deduplicated references", span_description)
        self.assertIn("selects one exact-barcode span", span_description)
        self.assertIn("labeled product-heading span", span_description)
        self.assertIn("falls back to a labeled product-name span", span_description)
        self.assertEqual(
            claim_schema["properties"]["evidence_span_refs"]["minItems"],
            1,
        )
        reference_schema = verify.input_schema["properties"]["evidence_refs"]
        item_description = reference_schema["items"].get("description", "")
        self.assertIn("fetch_evidence", item_description)
        self.assertIn("evidence_ref", item_description)
        self.assertIn("URL", item_description)
        self.assertIn("ev_-prefixed", item_description)
        self.assertEqual(
            reference_schema["items"]["pattern"],
            r"^ev_[A-Za-z0-9_-]{40}$",
        )
        self.assertIn("server-generated", verify.description)
        self.assertIn("literal text span", " ".join(verify.description.split()))

    def test_span_selection_and_verification_schema_explain_reference_authority(self) -> None:
        server = create_mcp_server(runtime())
        tools = asyncio.run(server.list_tools())
        selector = next(tool for tool in tools if tool.name == "select_evidence_spans")
        verify = next(tool for tool in tools if tool.name == "verify_claims")

        evidence_description = selector.input_schema["properties"]["evidence_ref"].get(
            "description", ""
        )
        self.assertIn("fetch_evidence", evidence_description)
        self.assertIn("evidence_ref", evidence_description)
        self.assertIn("URL", evidence_description)
        selector_query = selector.input_schema["properties"]["query"].get(
            "description", ""
        )
        self.assertIn("exact barcode", selector_query)
        self.assertIn("every fetched product page", selector_query)
        verify_description = " ".join(verify.description.split())
        self.assertIn("Every claim needs at least one span ref", verify_description)
        self.assertIn("deduplicates the selected refs", verify_description)
        self.assertIn(
            "never invent references",
            verify_description.casefold(),
        )
        self.assertIn(
            "never combine multiple excerpts",
            verify_description.casefold(),
        )
        self.assertIn("STRUCTURED_FIELD", verify_description)
        self.assertIn("EXTRACTIVE_STATEMENT", verify_description)
        self.assertIn("only for claim_key=exact_evidence", verify_description)
        self.assertIn("do not resubmit unchanged claims", verify_description)

    def test_span_selection_tool_calls_server_service(self) -> None:
        service = runtime()
        service.select_evidence_spans.return_value = {
            "evidence_ref": "ev_" + "R" * 40,
            "spans": [],
        }
        server = create_mcp_server(service)

        async def invoke():
            return await server.call_tool(
                "select_evidence_spans",
                {"evidence_ref": "ev_" + "R" * 40, "query": "barcode", "limit": 3},
            )

        result = asyncio.run(invoke())
        self.assertFalse(result.is_error)
        service.select_evidence_spans.assert_called_once_with(
            "ev_" + "R" * 40,
            query="barcode",
            limit=3,
        )

    def test_mcp_wrappers_reuse_service_capabilities_and_return_provenance(self) -> None:
        service = runtime()
        search = mcp_search_web(service, query="gold")
        fetched = mcp_fetch_evidence(
            service,
            url="https://example.com/gold",
        )
        indexed = mcp_index_evidence(
            service,
            urls=("https://example.com/gold",),
        )
        queried = mcp_query_evidence(service, query="gold", limit=2)

        self.assertEqual(search["sources"][0]["url"], "https://example.com/gold")
        self.assertEqual(fetched["fetched_from_ip"], "93.184.216.34")
        self.assertEqual(indexed["embedding_dimension"], 1024)
        self.assertEqual(queried[0]["chunk"]["chunk_id"], "abc")
        service.index_evidence.assert_called_once_with(("https://example.com/gold",))
        service.query_evidence.assert_called_once_with("gold", limit=2)

    def test_mcp_tool_call_returns_structured_content(self) -> None:
        server = create_mcp_server(runtime())

        async def invoke():
            return await server.call_tool("search_web", {"query": "gold"})

        result = asyncio.run(invoke())
        self.assertFalse(result.is_error)
        self.assertIsNotNone(result.structured_content)
        self.assertEqual(
            result.structured_content["sources"][0]["url"],
            "https://example.com/gold",
        )

    def test_search_web_marks_exact_provider_url_for_fetching(self) -> None:
        service = runtime()
        service.search_web.return_value = EvidencePack(
            query="barcode",
            status=EvidenceStatus.GROUNDED,
            sources=(
                EvidenceSource(
                    canonical_url="https://example.com/product",
                    url="https://example.com/product/",
                    title="Product",
                    snippet="Barcode evidence",
                    provider="test",
                    rank=1,
                ),
            ),
            search_calls_used=1,
            reason="grounded",
        )

        result = mcp_search_web(service, query="barcode")

        source = result["sources"][0]
        self.assertEqual(source["canonical_url"], "https://example.com/product")
        self.assertEqual(source["url"], "https://example.com/product/")
        self.assertEqual(source["fetch_url"], "https://example.com/product/")


if __name__ == "__main__":
    unittest.main()

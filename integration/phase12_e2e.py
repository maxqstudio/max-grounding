"""Phase 12 real production REST/MCP/security/bounded-load probe."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from max_grounding.models import FetchedDocument
from max_grounding.persistent import index_documents
from max_grounding.providers.ollama_embedding import OllamaEmbeddingProvider
from max_grounding.providers.qdrant import QdrantVectorStore


API_KEY = "phase12-production-acceptance-key-0001"
GOLD_URL = "https://example.com/gold"
GOLD_TEXT = "Central banks increased gold reserves during the measured period."


class _SearchStub(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if not self.path.startswith("/search?"):
            self.send_response(404)
            self.end_headers()
            return
        payload = json.dumps(
            {
                "results": [
                    {
                        "url": GOLD_URL,
                        "title": "Gold reserve evidence",
                        "content": GOLD_TEXT,
                    }
                ]
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args) -> None:
        return


def _start_search_stub(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _SearchStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _seed_index(ollama_url: str, qdrant_url: str) -> str:
    provider = OllamaEmbeddingProvider(ollama_url)
    store = QdrantVectorStore(qdrant_url)
    document = FetchedDocument(
        url=GOLD_URL,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(GOLD_TEXT.encode("utf-8")),
        text=GOLD_TEXT,
    )
    result = index_documents((document,), provider, store)
    if result.embedding_dimension != 1024:
        raise AssertionError(result)
    return result.collection_name


def _wait_health(base_url: str) -> None:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(base_url + "/healthz", timeout=3)
            if response.status_code == 200:
                return
        except Exception as exc:
            last_error = exc
        time.sleep(1)
    raise AssertionError(f"service did not become healthy: {last_error}")


def _rest_probe(base_url: str) -> None:
    auth = {"Authorization": f"Bearer {API_KEY}"}

    health = httpx.get(base_url + "/healthz", timeout=10)
    assert health.status_code == 200, health.text

    unauth = httpx.get(base_url + "/readyz", timeout=10)
    assert unauth.status_code == 401, unauth.text

    wrong = httpx.post(
        base_url + "/v1/query",
        headers={"Authorization": "Bearer wrong"},
        json={"query": "gold reserves", "limit": 2},
        timeout=10,
    )
    assert wrong.status_code == 401, wrong.text

    fabricated_verify = httpx.post(
        base_url + "/v1/verify",
        headers=auth,
        json={
            "claims": [{"claim_id": "fake", "claim_key": "name", "value": "Invented"}],
            "evidence_refs": ["ev_" + "A" * 40],
            "required_sources": 1,
        },
        timeout=10,
    )
    assert fabricated_verify.status_code == 400, fabricated_verify.text
    assert fabricated_verify.json() == {"error": "invalid_request"}

    mcp_unauth = httpx.post(
        base_url + "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "server/discover"},
        timeout=10,
    )
    assert mcp_unauth.status_code == 401, mcp_unauth.text

    ready = httpx.get(base_url + "/readyz", headers=auth, timeout=20)
    assert ready.status_code == 200, ready.text
    assert ready.json()["ready"] is True

    search = httpx.post(
        base_url + "/v1/search",
        headers=auth,
        json={
            "query": "gold reserves",
            "max_search_rounds": 1,
            "results_per_call": 2,
            "min_evidence_sources": 1,
        },
        timeout=20,
    )
    assert search.status_code == 200, search.text
    payload = search.json()
    assert payload["sources"][0]["url"] == GOLD_URL, payload

    queried = httpx.post(
        base_url + "/v1/query",
        headers=auth,
        json={"query": "central bank gold reserves", "limit": 2},
        timeout=60,
    )
    assert queried.status_code == 200, queried.text
    hits = queried.json()
    assert hits and hits[0]["chunk"]["source_url"] == GOLD_URL, hits

    oversized = httpx.post(
        base_url + "/v1/search",
        headers={**auth, "Content-Type": "application/json"},
        content='{"query":"' + ("x" * (256 * 1024)) + '"}',
        timeout=20,
    )
    assert oversized.status_code == 413, oversized.text


async def _mcp_probe(base_url: str) -> None:
    client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {API_KEY}"},
        timeout=60.0,
    )
    async with client:
        async with streamable_http_client(
            base_url + "/mcp",
            http_client=client,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {item.name for item in tools.tools}
                assert names == {
                    "search_web",
                    "fetch_evidence",
                    "index_evidence",
                    "query_evidence",
                    "verify_claims",
                    "select_evidence_spans",
                }, names

                invalid_verification = await session.call_tool(
                    "verify_claims",
                    {
                        "claims": [{"claim_id": "fake", "claim_key": "name", "value": "Invented"}],
                        "evidence_refs": ["ev_" + "A" * 40],
                        "required_sources": 1,
                    },
                )
                assert invalid_verification.is_error, invalid_verification

                result = await session.call_tool(
                    "query_evidence",
                    {"query": "central bank gold reserves", "limit": 2},
                )
                assert not result.is_error, result
                structured = result.structured_content
                if isinstance(structured, dict) and set(structured) == {"result"}:
                    structured = structured["result"]
                assert isinstance(structured, list), structured
                assert structured[0]["chunk"]["source_url"] == GOLD_URL, structured


def _bounded_load_probe(base_url: str) -> dict[str, float | int]:
    auth = {"Authorization": f"Bearer {API_KEY}"}

    def one() -> float:
        started = time.perf_counter()
        response = httpx.post(
            base_url + "/v1/query",
            headers=auth,
            json={"query": "central bank gold reserves", "limit": 1},
            timeout=60,
        )
        elapsed = time.perf_counter() - started
        assert response.status_code == 200, response.text
        assert response.json()[0]["chunk"]["source_url"] == GOLD_URL
        return elapsed

    with ThreadPoolExecutor(max_workers=4) as pool:
        latencies = list(pool.map(lambda _index: one(), range(16)))

    ordered = sorted(latencies)
    p95_index = max(0, int(len(ordered) * 0.95) - 1)
    return {
        "requests": len(latencies),
        "concurrency": 4,
        "p50_seconds": statistics.median(latencies),
        "p95_seconds": ordered[p95_index],
        "max_seconds": max(latencies),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--qdrant-url", default="http://127.0.0.1:6333")
    parser.add_argument("--search-port", type=int, default=18080)
    args = parser.parse_args()

    stub = _start_search_stub(args.search_port)
    try:
        collection = _seed_index(args.ollama_url, args.qdrant_url)
        _wait_health(args.base_url)
        _rest_probe(args.base_url)
        asyncio.run(_mcp_probe(args.base_url))
        load = _bounded_load_probe(args.base_url)
    finally:
        stub.shutdown()
        stub.server_close()

    print(
        json.dumps(
            {
                "status": "PASS",
                "collection": collection,
                "embedding_model": "qwen3-embedding:0.6b",
                "embedding_dimension": 1024,
                "rest": True,
                "mcp": True,
                "security": True,
                "bounded_load": load,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

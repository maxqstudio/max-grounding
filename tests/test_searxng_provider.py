from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from max_grounding.errors import InvalidProviderConfiguration, SearchProviderError
from max_grounding.models import SearchQuery
from max_grounding.providers.searxng import (
    SearxngProvider,
    fetch_searxng_json,
)


class _ResponseHandler(BaseHTTPRequestHandler):
    status = 200
    content_type = "application/json"
    body = b'{"results": []}'
    location = None

    def do_GET(self) -> None:
        self.send_response(type(self).status)
        self.send_header("Content-Type", type(self).content_type)
        if type(self).location is not None:
            self.send_header("Location", type(self).location)
        self.send_header("Content-Length", str(len(type(self).body)))
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, format: str, *args: object) -> None:
        return


class _Server:
    def __init__(
        self,
        *,
        status: int = 200,
        content_type: str = "application/json",
        body: bytes = b'{"results": []}',
        location: str | None = None,
    ) -> None:
        handler = type(
            "Handler",
            (_ResponseHandler,),
            {
                "status": status,
                "content_type": content_type,
                "body": body,
                "location": location,
            },
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc, tb) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def query(*, freshness: str | None = "week", round_index: int = 2) -> SearchQuery:
    return SearchQuery(
        query="gold price",
        language="en",
        country="ID",
        freshness=freshness,
        limit=3,
        round_index=round_index,
    )


class SearxngProviderTests(unittest.TestCase):
    def test_operator_trusted_private_endpoint_is_allowed_but_query_cannot_replace_host(self) -> None:
        captured: dict[str, object] = {}

        def fake_fetch(url: str, *, timeout_seconds: float, max_response_bytes: int):
            captured["url"] = url
            captured["timeout"] = timeout_seconds
            captured["max_bytes"] = max_response_bytes
            return {
                "results": [
                    {
                        "url": "https://example.com/gold",
                        "title": "Gold",
                        "content": "Price update",
                    },
                    {
                        "url": "http://127.0.0.1/private",
                        "title": "Unsafe",
                        "content": "must be rejected",
                    },
                ]
            }

        provider = SearxngProvider(
            "http://127.0.0.1:8080/searxng",
            timeout_seconds=4.0,
            max_response_bytes=4096,
        )
        with patch(
            "max_grounding.providers.searxng.fetch_searxng_json",
            side_effect=fake_fetch,
        ):
            results = provider.search(query())

        parsed = urlsplit(str(captured["url"]))
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "127.0.0.1:8080")
        self.assertEqual(parsed.path, "/searxng/search")
        self.assertEqual(params["q"], ["gold price"])
        self.assertEqual(params["format"], ["json"])
        self.assertEqual(params["language"], ["en"])
        self.assertEqual(params["pageno"], ["2"])
        self.assertEqual(params["time_range"], ["week"])
        self.assertEqual(params["safesearch"], ["1"])
        self.assertEqual(captured["timeout"], 4.0)
        self.assertEqual(captured["max_bytes"], 4096)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].url, "https://example.com/gold")
        self.assertEqual(results[0].provider, "searxng")
        self.assertEqual(results[0].rank, 1)

    def test_base_url_rejects_credentials_query_and_fragment(self) -> None:
        bad = [
            "https://user:pass@example.com",
            "https://example.com/?override=1",
            "https://example.com/#fragment",
            "ftp://example.com",
        ]
        for base_url in bad:
            with self.subTest(base_url=base_url):
                with self.assertRaises(InvalidProviderConfiguration):
                    SearxngProvider(base_url)

    def test_fetch_accepts_bounded_json_object(self) -> None:
        body = json.dumps({"results": [{"url": "https://example.com"}]}).encode()
        with _Server(body=body) as base_url:
            payload = fetch_searxng_json(
                base_url + "/search",
                timeout_seconds=2.0,
                max_response_bytes=4096,
            )
        self.assertEqual(payload["results"][0]["url"], "https://example.com")

    def test_fetch_rejects_redirects(self) -> None:
        with _Server(status=302, location="/other") as base_url:
            with self.assertRaises(SearchProviderError):
                fetch_searxng_json(
                    base_url + "/search",
                    timeout_seconds=2.0,
                    max_response_bytes=4096,
                )

    def test_fetch_rejects_oversized_response_before_json_decode(self) -> None:
        with _Server(body=b"x" * 65) as base_url:
            with self.assertRaises(SearchProviderError):
                fetch_searxng_json(
                    base_url + "/search",
                    timeout_seconds=2.0,
                    max_response_bytes=64,
                )

    def test_fetch_rejects_non_json_and_invalid_json(self) -> None:
        cases = [
            ("text/html", b"<html>no</html>"),
            ("application/json", b"{not-json"),
        ]
        for content_type, body in cases:
            with self.subTest(content_type=content_type, body=body):
                with _Server(content_type=content_type, body=body) as base_url:
                    with self.assertRaises(SearchProviderError):
                        fetch_searxng_json(
                            base_url + "/search",
                            timeout_seconds=2.0,
                            max_response_bytes=4096,
                        )

    def test_provider_rejects_invalid_results_schema(self) -> None:
        provider = SearxngProvider("https://search.example")
        invalid_payloads = [
            {},
            {"results": "not-a-list"},
            {"results": [42]},
            {"results": [{"url": "https://example.com"}]},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                with patch(
                    "max_grounding.providers.searxng.fetch_searxng_json",
                    return_value=payload,
                ):
                    with self.assertRaises(SearchProviderError):
                        provider.search(query())


if __name__ == "__main__":
    unittest.main()

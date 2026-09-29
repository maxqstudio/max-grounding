"""SearXNG JSON search provider with bounded, fail-closed HTTP handling."""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)

from ..errors import InvalidProviderConfiguration, SearchProviderError
from ..models import SearchQuery, SourceCandidate
from ..network_policy import is_admissible_result_url

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
_ALLOWED_TIME_RANGES = {"day", "week", "month", "year"}


class _NoRedirect(HTTPRedirectHandler):
    """Keep provider authority fixed instead of following redirects elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalize_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise InvalidProviderConfiguration(
                "SearXNG base URL must use http or https and include a host"
            )
        if parsed.username is not None or parsed.password is not None:
            raise InvalidProviderConfiguration(
                "SearXNG base URL must not contain credentials"
            )
        if parsed.query or parsed.fragment:
            raise InvalidProviderConfiguration(
                "SearXNG base URL must not contain query or fragment data"
            )
        _ = parsed.port
    except InvalidProviderConfiguration:
        raise
    except (TypeError, ValueError) as exc:
        raise InvalidProviderConfiguration("invalid SearXNG base URL") from exc

    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


def build_searxng_search_url(
    base_url: str,
    query: SearchQuery,
    *,
    safe_search: int = 1,
) -> str:
    """Build one provider request without allowing query data to change authority."""
    endpoint = base_url.rstrip("/") + "/search"
    params: list[tuple[str, str]] = [
        ("q", query.query),
        ("format", "json"),
        ("language", query.language),
        ("pageno", str(query.round_index)),
        ("safesearch", str(safe_search)),
    ]
    if query.freshness in _ALLOWED_TIME_RANGES:
        params.append(("time_range", query.freshness))
    return endpoint + "?" + urlencode(params)


def fetch_searxng_json(
    url: str,
    *,
    timeout_seconds: float,
    max_response_bytes: int,
) -> dict[str, Any]:
    """Fetch one bounded JSON object from the already-trusted provider endpoint."""
    if timeout_seconds <= 0:
        raise InvalidProviderConfiguration("timeout_seconds must be greater than zero")
    if max_response_bytes <= 0:
        raise InvalidProviderConfiguration(
            "max_response_bytes must be greater than zero"
        )

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "User-Agent": "max-grounding/0.0.1",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect())

    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                raise SearchProviderError("SearXNG returned a non-200 response")

            content_type = response.headers.get_content_type()
            if content_type != "application/json":
                raise SearchProviderError("SearXNG response is not application/json")

            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    if int(length) > max_response_bytes:
                        raise SearchProviderError("SearXNG response exceeds byte limit")
                except ValueError:
                    pass

            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise SearchProviderError("SearXNG response exceeds byte limit")
    except SearchProviderError:
        raise
    except HTTPError as exc:
        raise SearchProviderError("SearXNG HTTP request failed") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SearchProviderError("SearXNG transport failed") from exc

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SearchProviderError("SearXNG returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise SearchProviderError("SearXNG JSON root must be an object")
    return payload


def parse_searxng_results(
    payload: dict[str, Any],
    limit: int,
) -> tuple[SourceCandidate, ...]:
    """Validate SearXNG result shape and retain only admissible result URLs."""
    results = payload.get("results")
    if not isinstance(results, list):
        raise SearchProviderError("SearXNG JSON must contain a results list")

    candidates: list[SourceCandidate] = []
    for rank, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            raise SearchProviderError("SearXNG result must be an object")

        url = item.get("url")
        title = item.get("title")
        content = item.get("content")
        if not isinstance(url, str) or not isinstance(title, str) or not isinstance(
            content, str
        ):
            raise SearchProviderError(
                "SearXNG result requires string url, title, and content"
            )
        if not is_admissible_result_url(url):
            continue

        candidates.append(
            SourceCandidate(
                url=url,
                title=title,
                snippet=content,
                provider="searxng",
                rank=rank,
            )
        )
        if len(candidates) >= limit:
            break

    return tuple(candidates)


class SearxngProvider:
    """Live search provider for one operator-configured SearXNG instance."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        safe_search: int = 1,
    ) -> None:
        if timeout_seconds <= 0:
            raise InvalidProviderConfiguration(
                "timeout_seconds must be greater than zero"
            )
        if max_response_bytes <= 0:
            raise InvalidProviderConfiguration(
                "max_response_bytes must be greater than zero"
            )
        if safe_search not in {0, 1, 2}:
            raise InvalidProviderConfiguration("safe_search must be 0, 1, or 2")

        self._base_url = _normalize_base_url(base_url)
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._safe_search = safe_search

    def search(self, query: SearchQuery) -> tuple[SourceCandidate, ...]:
        """Execute one bounded SearXNG request for an already-budgeted SearchQuery."""
        url = build_searxng_search_url(
            self._base_url,
            query,
            safe_search=self._safe_search,
        )
        payload = fetch_searxng_json(
            url,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        return parse_searxng_results(payload, query.limit)

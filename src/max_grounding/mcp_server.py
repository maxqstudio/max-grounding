"""Production MCP tool boundary for accepted MAX Grounding capabilities."""

from __future__ import annotations

from typing import Any

from mcp.server import MCPServer

from .service import GroundingService
from .wire import to_wire


def mcp_search_web(
    service: GroundingService,
    *,
    query: str,
    language: str = "en",
    country: str | None = None,
    freshness: str | None = None,
    max_search_rounds: int = 2,
    results_per_call: int = 5,
    min_evidence_sources: int = 1,
) -> dict[str, Any]:
    return to_wire(
        service.search_web(
            query,
            language=language,
            country=country,
            freshness=freshness,
            max_search_rounds=max_search_rounds,
            results_per_call=results_per_call,
            min_evidence_sources=min_evidence_sources,
        )
    )


def mcp_fetch_evidence(
    service: GroundingService,
    *,
    url: str,
) -> dict[str, Any]:
    return to_wire(service.fetch_evidence(url))


def mcp_index_evidence(
    service: GroundingService,
    *,
    urls: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    if not isinstance(urls, (tuple, list)):
        raise ValueError("urls must be a bounded list or tuple")
    return to_wire(service.index_evidence(tuple(urls)))


def mcp_query_evidence(
    service: GroundingService,
    *,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    return to_wire(service.query_evidence(query, limit=limit))


def register_mcp_tools(server: MCPServer, service: GroundingService) -> None:
    """Register exactly the four production grounding tools."""

    def _tool_search_web(
        query: str,
        language: str = "en",
        country: str | None = None,
        freshness: str | None = None,
        max_search_rounds: int = 2,
        results_per_call: int = 5,
        min_evidence_sources: int = 1,
    ) -> dict[str, Any]:
        """Search the configured web provider and return bounded source evidence."""
        return mcp_search_web(
            service,
            query=query,
            language=language,
            country=country,
            freshness=freshness,
            max_search_rounds=max_search_rounds,
            results_per_call=results_per_call,
            min_evidence_sources=min_evidence_sources,
        )

    def _tool_fetch_evidence(url: str) -> dict[str, Any]:
        """Securely fetch one public evidence URL and return extracted text provenance."""
        return mcp_fetch_evidence(service, url=url)

    def _tool_index_evidence(urls: list[str]) -> dict[str, Any]:
        """Securely fetch and persist a bounded list of evidence URLs."""
        return mcp_index_evidence(service, urls=urls)

    def _tool_query_evidence(query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Query the accepted persistent semantic evidence index."""
        return mcp_query_evidence(service, query=query, limit=limit)

    server.tool(name="search_web", structured_output=True)(_tool_search_web)
    server.tool(name="fetch_evidence", structured_output=True)(_tool_fetch_evidence)
    server.tool(name="index_evidence", structured_output=True)(_tool_index_evidence)
    server.tool(name="query_evidence", structured_output=True)(_tool_query_evidence)


def create_mcp_server(service: GroundingService) -> MCPServer:
    server = MCPServer(
        "MAX Grounding",
        instructions=(
            "Evidence retrieval only. Tools return bounded provenance-bearing evidence; "
            "they do not produce a final factual answer or research conclusion."
        ),
    )
    register_mcp_tools(server, service)
    return server

"""Authenticated production REST and mounted MCP ASGI application."""

from __future__ import annotations

import hmac
import json
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.cors import CORSMiddleware

from . import __version__
from .evidence_authority import (
    DEFAULT_REQUIRED_SOURCES,
    MAX_EVIDENCE_REFERENCE_CHARS,
    MAX_EVIDENCE_REFERENCES,
)
from .errors import GroundingError, InvalidGroundingRequest, ServiceOperationError
from .mcp_server import create_mcp_server
from .policy import MAX_QUERY_CHARACTERS, MAX_RESULTS_PER_CALL, MAX_SEARCH_CALLS
from .retrieval import MAX_RESULTS
from .service import (
    MAX_INDEX_URLS,
    MIN_API_KEY_CHARS,
    GroundingService,
    ProductionSettings,
    build_production_runtime,
)
from .verification import (
    MAX_ANSWER_CLAIMS,
    MAX_CLAIM_KEY_CHARS,
    MAX_CLAIM_VALUE_CHARS,
    MAX_REQUIRED_SOURCES,
)
from .wire import to_wire

MAX_REQUEST_BODY_BYTES = 256 * 1024


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)
    language: str = Field(default="en", min_length=1, max_length=32)
    country: str | None = Field(default=None, max_length=16)
    freshness: str | None = Field(default=None, max_length=32)
    max_search_rounds: int = Field(default=2, ge=1, le=MAX_SEARCH_CALLS)
    results_per_call: int = Field(default=5, ge=1, le=MAX_RESULTS_PER_CALL)
    min_evidence_sources: int = Field(default=1, ge=1, le=MAX_RESULTS_PER_CALL)


class FetchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=4096)


class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: tuple[str, ...] = Field(min_length=1, max_length=MAX_INDEX_URLS)


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARACTERS)
    limit: int = Field(default=5, ge=1, le=MAX_RESULTS)


class CandidateClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    claim_id: str | int | None = Field(
        default=None,
        description="Optional bookkeeping value; MAX Grounding generates the canonical claim ID.",
    )
    claim_key: str = Field(
        min_length=1,
        max_length=MAX_CLAIM_KEY_CHARS,
        description=(
            "Structured fact key, or exact_evidence for a literal source span "
            "that must appear in fetched evidence."
        ),
    )
    value: str = Field(
        min_length=1,
        max_length=MAX_CLAIM_VALUE_CHARS,
        description=(
            "Proposed fact value. For exact_evidence, provide source text verbatim; "
            "semantic paraphrases are not accepted as evidence."
        ),
    )


EvidenceReference = Annotated[
    str,
    Field(
        min_length=MAX_EVIDENCE_REFERENCE_CHARS,
        max_length=MAX_EVIDENCE_REFERENCE_CHARS,
        pattern=r"^[A-Za-z0-9_-]{43}$",
        description=(
            "Use only the opaque evidence_ref returned by this service's /v1/fetch; "
            "source URLs are not evidence references."
        ),
    ),
]


class VerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: tuple[CandidateClaimRequest, ...] = Field(
        min_length=1,
        max_length=MAX_ANSWER_CLAIMS,
    )
    evidence_refs: tuple[EvidenceReference, ...] = Field(
        min_length=1,
        max_length=MAX_EVIDENCE_REFERENCES,
    )
    required_sources: int = Field(
        default=DEFAULT_REQUIRED_SOURCES,
        ge=1,
        le=MAX_REQUIRED_SOURCES,
        strict=True,
    )


def _header_map(scope: dict[str, Any]) -> dict[bytes, bytes]:
    return {
        key.lower(): value
        for key, value in scope.get("headers", [])
    }


def _extract_token(headers: dict[bytes, bytes]) -> str | None:
    direct = headers.get(b"x-api-key")
    if direct is not None:
        try:
            return direct.decode("utf-8")
        except UnicodeDecodeError:
            return None
    auth = headers.get(b"authorization")
    if auth is None:
        return None
    try:
        value = auth.decode("utf-8")
    except UnicodeDecodeError:
        return None
    scheme, separator, token = value.partition(" ")
    if separator and scheme.casefold() == "bearer":
        return token
    return None


async def _send_error(send, status: int, error: str) -> None:
    body = json.dumps({"error": error}, separators=(",", ":")).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class _RequestTooLarge(Exception):
    pass


class ProductionSecurityMiddleware:
    """Bound request bytes and authenticate every non-health operation."""

    def __init__(
        self,
        app,
        *,
        api_key: str,
        max_body_bytes: int = MAX_REQUEST_BODY_BYTES,
    ) -> None:
        if not isinstance(api_key, str) or len(api_key) < MIN_API_KEY_CHARS:
            raise ValueError("production API key is too short")
        if max_body_bytes <= 0:
            raise ValueError("max_body_bytes must be positive")
        self.app = app
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = _header_map(scope)
        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))

        if method != "OPTIONS" and path != "/healthz":
            token = _extract_token(headers)
            if token is None or not hmac.compare_digest(token, self.api_key):
                await _send_error(send, 401, "unauthorized")
                return

        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length.decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await _send_error(send, 400, "invalid_content_length")
                return
            if declared < 0 or declared > self.max_body_bytes:
                await _send_error(send, 413, "request_too_large")
                return

        received = 0
        response_started = False

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise _RequestTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _RequestTooLarge:
            if response_started:
                raise
            await _send_error(send, 413, "request_too_large")


router = APIRouter()


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request):
    return to_wire(request.app.state.grounding_service.readiness())


@router.post("/v1/search")
def search_endpoint(payload: SearchRequest, request: Request):
    service = request.app.state.grounding_service
    return to_wire(
        service.search_web(
            payload.query,
            language=payload.language,
            country=payload.country,
            freshness=payload.freshness,
            max_search_rounds=payload.max_search_rounds,
            results_per_call=payload.results_per_call,
            min_evidence_sources=payload.min_evidence_sources,
        )
    )


@router.post("/v1/fetch")
def fetch_endpoint(payload: FetchRequest, request: Request):
    return to_wire(
        request.app.state.grounding_service.fetch_evidence(payload.url)
    )


@router.post("/v1/index")
def index_endpoint(payload: IndexRequest, request: Request):
    return to_wire(
        request.app.state.grounding_service.index_evidence(payload.urls)
    )


@router.post("/v1/query")
def query_endpoint(payload: QueryRequest, request: Request):
    return to_wire(
        request.app.state.grounding_service.query_evidence(
            payload.query,
            limit=payload.limit,
        )
    )


@router.post("/v1/verify")
def verify_endpoint(payload: VerifyRequest, request: Request):
    claims = tuple(item.model_dump() for item in payload.claims)
    return to_wire(
        request.app.state.grounding_service.verify_candidate_claims(
            claims,
            evidence_refs=payload.evidence_refs,
            required_sources=payload.required_sources,
        )
    )


def create_rest_app(
    service: GroundingService,
    *,
    api_key: str,
    mcp_server: MCPServer | None = None,
    allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    ),
    allowed_origins: tuple[str, ...] = (),
) -> FastAPI:
    if not isinstance(api_key, str) or len(api_key) < MIN_API_KEY_CHARS:
        raise ValueError("production API key is too short")
    if not allowed_hosts:
        raise ValueError("at least one allowed host is required")

    mcp_app = None
    if mcp_server is not None:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(allowed_hosts),
            allowed_origins=list(allowed_origins),
        )
        mcp_app = mcp_server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            max_request_body_size=MAX_REQUEST_BODY_BYTES,
            transport_security=transport_security,
            host="0.0.0.0",
        )

        @asynccontextmanager
        async def lifespan(_app):
            async with mcp_server.session_manager.run():
                yield
    else:
        @asynccontextmanager
        async def lifespan(_app):
            yield

    app = FastAPI(
        title="MAX Grounding",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        debug=False,
        lifespan=lifespan,
    )
    app.state.grounding_service = service
    app.include_router(router)

    if mcp_app is not None:
        app.mount("/", mcp_app)

    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(allowed_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-API-Key",
                "Last-Event-ID",
                "Mcp-Method",
                "Mcp-Name",
                "Mcp-Protocol-Version",
                "Mcp-Session-Id",
            ],
            expose_headers=["Mcp-Session-Id"],
        )

    app.add_middleware(
        ProductionSecurityMiddleware,
        api_key=api_key,
        max_body_bytes=MAX_REQUEST_BODY_BYTES,
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "invalid_request"},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(InvalidGroundingRequest)
    async def invalid_request_handler(_request: Request, _exc: InvalidGroundingRequest):
        return JSONResponse(
            status_code=400,
            content={"error": "invalid_request"},
            headers={"Cache-Control": "no-store"},
        )

    @app.exception_handler(GroundingError)
    async def grounding_error_handler(_request: Request, _exc: GroundingError):
        return JSONResponse(
            status_code=502,
            content={"error": "service_unavailable"},
            headers={"Cache-Control": "no-store"},
        )

    return app


def create_production_app() -> FastAPI:
    settings = ProductionSettings.from_env()
    service = build_production_runtime(settings)
    mcp_server = create_mcp_server(service)
    return create_rest_app(
        service,
        api_key=settings.api_key,
        mcp_server=mcp_server,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )

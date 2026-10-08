"""Pinned stdlib Ollama embedding adapter for Phase 11."""

from __future__ import annotations

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .. import __version__
from ..errors import EmbeddingProviderError, RuntimeProviderError
from ..semantic import MAX_EMBEDDING_BATCH, _validated_vector

OLLAMA_VERSION = "0.35.1"
# Maintain the accepted model/dimension contract; only versions with explicit
# compatibility evidence are admitted. 0.40.0 was exercised with real local
# qwen3-embedding:0.6b inference on October 8, 2026.
OLLAMA_COMPATIBLE_VERSIONS = frozenset({"0.34.0", OLLAMA_VERSION, "0.40.0"})
OLLAMA_MODEL = "qwen3-embedding:0.6b"
OLLAMA_EMBEDDING_DIMENSION = 1024
QUERY_INSTRUCTION = (
    "Instruct: Given a web grounding query, retrieve passages that contain "
    "evidence needed to answer it.\nQuery: "
)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESPONSE_BYTES = 16_000_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalize_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeProviderError(
                "Ollama base URL must use http or https and include a host"
            )
        if parsed.username is not None or parsed.password is not None:
            raise RuntimeProviderError(
                "Ollama base URL must not contain credentials"
            )
        if parsed.query or parsed.fragment:
            raise RuntimeProviderError(
                "Ollama base URL must not contain query or fragment data"
            )
        _ = parsed.port
    except RuntimeProviderError:
        raise
    except (TypeError, ValueError) as exc:
        raise RuntimeProviderError("invalid Ollama base URL") from exc

    return urlunsplit(
        (
            scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def request_ollama_json(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> dict:
    """Execute one bounded non-redirecting request to a trusted Ollama service."""
    if timeout_seconds <= 0 or max_response_bytes <= 0:
        raise RuntimeProviderError("Ollama transport bounds must be positive")

    body = None
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": f"max-grounding/{__version__}",
    }
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = Request(
        base_url.rstrip("/") + path,
        data=body,
        headers=headers,
        method=method,
    )
    opener = build_opener(_NoRedirect())

    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            if getattr(response, "status", 200) != 200:
                raise RuntimeProviderError("Ollama returned a non-200 response")
            if response.headers.get_content_type() != "application/json":
                raise RuntimeProviderError(
                    "Ollama response is not application/json"
                )
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    if int(length) > max_response_bytes:
                        raise RuntimeProviderError(
                            "Ollama response exceeds byte limit"
                        )
                except ValueError:
                    pass
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise RuntimeProviderError(
                    "Ollama response exceeds byte limit"
                )
    except RuntimeProviderError:
        raise
    except HTTPError as exc:
        raise RuntimeProviderError("Ollama HTTP request failed") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeProviderError("Ollama transport failed") from exc

    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeProviderError("Ollama returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeProviderError("Ollama JSON root must be an object")
    return value


class OllamaEmbeddingProvider:
    """Concrete Phase 11 EmbeddingProvider backed by pinned local Ollama."""

    model_name = OLLAMA_MODEL
    embedding_dimension = OLLAMA_EMBEDDING_DIMENSION
    runtime_version = OLLAMA_VERSION

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise RuntimeProviderError(
                "Ollama transport bounds must be positive"
            )
        self._base_url = _normalize_base_url(base_url)
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._runtime_verified = False
        self.detected_runtime_version: str | None = None

    def verify_runtime(self) -> None:
        if self._runtime_verified:
            return
        payload = request_ollama_json(
            self._base_url,
            "GET",
            "/api/version",
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        runtime_version = payload.get("version")
        if (
            not isinstance(runtime_version, str)
            or runtime_version not in OLLAMA_COMPATIBLE_VERSIONS
        ):
            raise RuntimeProviderError(
                "Ollama runtime is not in the explicitly verified compatibility set"
            )
        self.detected_runtime_version = runtime_version
        self.runtime_version = runtime_version
        self._runtime_verified = True

    def _embed(
        self,
        inputs: str | list[str],
        *,
        expected_count: int,
    ) -> tuple[tuple[float, ...], ...]:
        self.verify_runtime()
        payload = request_ollama_json(
            self._base_url,
            "POST",
            "/api/embed",
            payload={
                "model": OLLAMA_MODEL,
                "input": inputs,
                "truncate": False,
                "dimensions": OLLAMA_EMBEDDING_DIMENSION,
            },
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
        )
        if payload.get("model") != OLLAMA_MODEL:
            raise EmbeddingProviderError(
                "Ollama embedding response model does not match pinned model"
            )
        returned = payload.get("embeddings")
        if not isinstance(returned, list) or len(returned) != expected_count:
            raise EmbeddingProviderError(
                "Ollama returned the wrong embedding count"
            )

        vectors: list[tuple[float, ...]] = []
        for raw in returned:
            try:
                validated = _validated_vector(
                    raw,
                    expected_dimension=OLLAMA_EMBEDDING_DIMENSION,
                )
            except EmbeddingProviderError:
                raise
            if not math.isfinite(math.fsum(x * x for x in validated)):
                raise EmbeddingProviderError(
                    "Ollama embedding norm is not finite"
                )
            vectors.append(validated)
        return tuple(vectors)

    def embed_query(self, text: str):
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingProviderError("embedding query must be non-empty")
        vectors = self._embed(
            QUERY_INSTRUCTION + text.strip(),
            expected_count=1,
        )
        return vectors[0]

    def embed_documents(self, texts: tuple[str, ...]):
        if not isinstance(texts, tuple):
            raise EmbeddingProviderError(
                "embedding documents must be a bounded tuple"
            )
        if len(texts) > MAX_EMBEDDING_BATCH:
            raise EmbeddingProviderError(
                f"embedding batch must not exceed {MAX_EMBEDDING_BATCH} items"
            )
        if not texts:
            return ()
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise EmbeddingProviderError(
                    "embedding document text must be non-empty"
                )
        return self._embed(
            list(texts),
            expected_count=len(texts),
        )

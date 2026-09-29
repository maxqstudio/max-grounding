"""Pinned stdlib Qdrant REST adapter for Phase 11."""

from __future__ import annotations

import json
import math
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..errors import EmbeddingProviderError, VectorStoreError
from ..models import PersistentVectorHit, TextChunk
from ..semantic import MAX_RESULTS, _validated_vector
from .ollama_embedding import (
    OLLAMA_EMBEDDING_DIMENSION,
    OLLAMA_MODEL,
)

QDRANT_VERSION = "1.19.1"
QDRANT_SCHEMA_VERSION = 1
QDRANT_VECTOR_NAME = "qwen3_embedding_0_6b_v1"
DEFAULT_COLLECTION = "max_grounding_qwen3_embedding_0_6b_v1"
MAX_UPSERT_POINTS = 64

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_RESPONSE_BYTES = 8_000_000
_COLLECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalize_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise VectorStoreError(
                "Qdrant base URL must use http or https and include a host"
            )
        if parsed.username is not None or parsed.password is not None:
            raise VectorStoreError(
                "Qdrant base URL must not contain credentials"
            )
        if parsed.query or parsed.fragment:
            raise VectorStoreError(
                "Qdrant base URL must not contain query or fragment data"
            )
        _ = parsed.port
    except VectorStoreError:
        raise
    except (TypeError, ValueError) as exc:
        raise VectorStoreError("invalid Qdrant base URL") from exc

    return urlunsplit(
        (
            scheme,
            parsed.netloc,
            parsed.path.rstrip("/"),
            "",
            "",
        )
    )


def request_qdrant_json(
    base_url: str,
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    api_key: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    allow_not_found: bool = False,
) -> tuple[int, dict]:
    """Execute one bounded Qdrant REST request without redirects."""
    if timeout_seconds <= 0 or max_response_bytes <= 0:
        raise VectorStoreError("Qdrant transport bounds must be positive")

    body = None
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "User-Agent": "max-grounding/0.0.1",
    }
    if api_key is not None:
        headers["api-key"] = api_key
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
            status = getattr(response, "status", 200)
            if status != 200:
                raise VectorStoreError(
                    "Qdrant returned an unexpected HTTP status"
                )
            if response.headers.get_content_type() != "application/json":
                raise VectorStoreError(
                    "Qdrant response is not application/json"
                )
            length = response.headers.get("Content-Length")
            if length is not None:
                try:
                    if int(length) > max_response_bytes:
                        raise VectorStoreError(
                            "Qdrant response exceeds byte limit"
                        )
                except ValueError:
                    pass
            raw = response.read(max_response_bytes + 1)
            if len(raw) > max_response_bytes:
                raise VectorStoreError(
                    "Qdrant response exceeds byte limit"
                )
    except HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return 404, {}
        raise VectorStoreError("Qdrant HTTP request failed") from exc
    except VectorStoreError:
        raise
    except (URLError, TimeoutError, OSError) as exc:
        raise VectorStoreError("Qdrant transport failed") from exc

    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VectorStoreError("Qdrant returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise VectorStoreError("Qdrant JSON root must be an object")
    return 200, value


def point_id_for_chunk(chunk_id: str) -> str:
    if not isinstance(chunk_id, str) or not chunk_id:
        raise VectorStoreError("chunk_id must be non-empty")
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _vector(value) -> tuple[float, ...]:
    try:
        return _validated_vector(
            value,
            expected_dimension=OLLAMA_EMBEDDING_DIMENSION,
        )
    except EmbeddingProviderError as exc:
        raise VectorStoreError("invalid persistent vector") from exc


class QdrantVectorStore:
    """Concrete persistent vector store with pinned schema and model metadata."""

    model_name = OLLAMA_MODEL
    embedding_dimension = OLLAMA_EMBEDDING_DIMENSION
    schema_version = QDRANT_SCHEMA_VERSION

    def __init__(
        self,
        base_url: str,
        *,
        collection: str = DEFAULT_COLLECTION,
        api_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        if not isinstance(collection, str) or not _COLLECTION_RE.fullmatch(collection):
            raise VectorStoreError("invalid Qdrant collection name")
        if api_key is not None and (not isinstance(api_key, str) or not api_key):
            raise VectorStoreError("Qdrant api_key must be non-empty when set")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise VectorStoreError("Qdrant transport bounds must be positive")

        self._base_url = _normalize_base_url(base_url)
        self.collection_name = collection
        self._api_key = api_key
        self._timeout_seconds = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._runtime_verified = False
        self._collection_verified = False

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        allow_not_found: bool = False,
    ) -> tuple[int, dict]:
        return request_qdrant_json(
            self._base_url,
            method,
            path,
            payload=payload,
            api_key=self._api_key,
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=self._max_response_bytes,
            allow_not_found=allow_not_found,
        )

    def verify_runtime(self) -> None:
        if self._runtime_verified:
            return
        _, payload = self._request("GET", "/")
        if payload.get("version") != QDRANT_VERSION:
            raise VectorStoreError(
                f"Qdrant runtime must be exactly {QDRANT_VERSION}"
            )
        self._runtime_verified = True

    @property
    def _collection_path(self) -> str:
        return "/collections/" + quote(self.collection_name, safe="")

    def _validate_collection(self, payload: dict) -> None:
        try:
            config = payload["result"]["config"]
            params = config["params"]
            vectors = params["vectors"]
            vector_config = vectors[QDRANT_VECTOR_NAME]
            size = vector_config["size"]
            distance = vector_config["distance"]
            metadata = config["metadata"]["max_grounding"]
        except (KeyError, TypeError) as exc:
            raise VectorStoreError(
                "Qdrant collection schema is incomplete"
            ) from exc

        if isinstance(size, bool) or size != OLLAMA_EMBEDDING_DIMENSION:
            raise VectorStoreError(
                "Qdrant collection embedding dimension mismatch"
            )
        if not isinstance(distance, str) or distance.casefold() != "cosine":
            raise VectorStoreError(
                "Qdrant collection distance must be Cosine"
            )
        if not isinstance(metadata, dict):
            raise VectorStoreError("Qdrant collection metadata is invalid")
        if metadata.get("schema_version") != QDRANT_SCHEMA_VERSION:
            raise VectorStoreError(
                "Qdrant collection schema version mismatch"
            )
        if metadata.get("embedding_model") != OLLAMA_MODEL:
            raise VectorStoreError(
                "Qdrant collection embedding model mismatch"
            )
        if metadata.get("embedding_dimension") != OLLAMA_EMBEDDING_DIMENSION:
            raise VectorStoreError(
                "Qdrant collection metadata dimension mismatch"
            )

    def ensure_collection(self) -> None:
        if self._collection_verified:
            return
        self.verify_runtime()
        status, payload = self._request(
            "GET",
            self._collection_path,
            allow_not_found=True,
        )
        if status == 404:
            _, created = self._request(
                "PUT",
                self._collection_path,
                payload={
                    "vectors": {
                        QDRANT_VECTOR_NAME: {
                            "size": OLLAMA_EMBEDDING_DIMENSION,
                            "distance": "Cosine",
                        }
                    },
                    "metadata": {
                        "max_grounding": {
                            "schema_version": QDRANT_SCHEMA_VERSION,
                            "embedding_model": OLLAMA_MODEL,
                            "embedding_dimension": OLLAMA_EMBEDDING_DIMENSION,
                        }
                    },
                },
            )
            if created.get("result") is not True:
                raise VectorStoreError(
                    "Qdrant collection creation was not acknowledged"
                )
        else:
            self._validate_collection(payload)
        self._collection_verified = True

    def upsert_chunks(self, chunks, vectors) -> None:
        self.ensure_collection()
        chunks_tuple = tuple(chunks)
        vectors_tuple = tuple(vectors)
        if len(chunks_tuple) != len(vectors_tuple):
            raise VectorStoreError(
                "Qdrant chunk/vector counts must match"
            )
        if not chunks_tuple:
            return
        if len(chunks_tuple) > MAX_UPSERT_POINTS:
            raise VectorStoreError(
                f"Qdrant upsert must not exceed {MAX_UPSERT_POINTS} points"
            )

        points = []
        for chunk, raw_vector in zip(chunks_tuple, vectors_tuple):
            if not isinstance(chunk, TextChunk):
                raise VectorStoreError("Qdrant chunks must be TextChunk values")
            vec = _vector(raw_vector)
            points.append(
                {
                    "id": point_id_for_chunk(chunk.chunk_id),
                    "vector": {QDRANT_VECTOR_NAME: list(vec)},
                    "payload": {
                        "chunk_id": chunk.chunk_id,
                        "source_url": chunk.source_url,
                        "chunk_index": chunk.chunk_index,
                        "text": chunk.text,
                        "token_count": chunk.token_count,
                        "embedding_model": OLLAMA_MODEL,
                        "embedding_dimension": OLLAMA_EMBEDDING_DIMENSION,
                        "schema_version": QDRANT_SCHEMA_VERSION,
                    },
                }
            )

        _, payload = self._request(
            "PUT",
            self._collection_path + "/points?wait=true",
            payload={"points": points},
        )
        if payload.get("status") != "ok":
            raise VectorStoreError("Qdrant upsert response is not ok")
        result = payload.get("result")
        if not isinstance(result, dict) or result.get("status") not in {
            "completed",
            "acknowledged",
        }:
            raise VectorStoreError("Qdrant upsert was not acknowledged")

    def _parse_point(self, value: object) -> PersistentVectorHit:
        if not isinstance(value, dict):
            raise VectorStoreError("Qdrant query point must be an object")
        point_id = value.get("id")
        score = value.get("score")
        payload = value.get("payload")
        if not isinstance(point_id, str) or not isinstance(payload, dict):
            raise VectorStoreError(
                "Qdrant query point is missing identity or payload"
            )
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise VectorStoreError("Qdrant score must be finite")
        score_number = float(score)
        if not math.isfinite(score_number):
            raise VectorStoreError("Qdrant score must be finite")

        chunk_id = payload.get("chunk_id")
        source_url = payload.get("source_url")
        chunk_index = payload.get("chunk_index")
        text = payload.get("text")
        token_count = payload.get("token_count")
        if (
            not isinstance(chunk_id, str)
            or not chunk_id
            or not isinstance(source_url, str)
            or not source_url
            or isinstance(chunk_index, bool)
            or not isinstance(chunk_index, int)
            or chunk_index < 0
            or not isinstance(text, str)
            or not text
            or isinstance(token_count, bool)
            or not isinstance(token_count, int)
            or token_count <= 0
        ):
            raise VectorStoreError(
                "Qdrant point payload has invalid chunk provenance"
            )
        if point_id != point_id_for_chunk(chunk_id):
            raise VectorStoreError(
                "Qdrant point ID does not match chunk identity"
            )
        if payload.get("embedding_model") != OLLAMA_MODEL:
            raise VectorStoreError(
                "Qdrant point embedding model mismatch"
            )
        if payload.get("embedding_dimension") != OLLAMA_EMBEDDING_DIMENSION:
            raise VectorStoreError(
                "Qdrant point embedding dimension mismatch"
            )
        if payload.get("schema_version") != QDRANT_SCHEMA_VERSION:
            raise VectorStoreError(
                "Qdrant point schema version mismatch"
            )

        return PersistentVectorHit(
            point_id=point_id,
            chunk=TextChunk(
                chunk_id=chunk_id,
                source_url=source_url,
                chunk_index=chunk_index,
                text=text,
                token_count=token_count,
            ),
            score=score_number,
            embedding_model=OLLAMA_MODEL,
            embedding_dimension=OLLAMA_EMBEDDING_DIMENSION,
            schema_version=QDRANT_SCHEMA_VERSION,
        )

    def query_chunks(
        self,
        query_vector,
        *,
        limit: int,
    ) -> tuple[PersistentVectorHit, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            raise VectorStoreError(
                f"Qdrant query limit must be between 1 and {MAX_RESULTS}"
            )
        self.ensure_collection()
        vector = _vector(query_vector)
        _, payload = self._request(
            "POST",
            self._collection_path + "/points/query",
            payload={
                "query": list(vector),
                "using": QDRANT_VECTOR_NAME,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
        if payload.get("status") != "ok":
            raise VectorStoreError("Qdrant query response is not ok")
        result = payload.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise VectorStoreError("Qdrant query response has invalid result shape")
        if len(result["points"]) > limit:
            raise VectorStoreError("Qdrant returned more points than requested")
        return tuple(self._parse_point(item) for item in result["points"])

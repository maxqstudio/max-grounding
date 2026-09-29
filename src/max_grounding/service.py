"""Production service facade over accepted MAX Grounding capabilities."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Callable

from .engine import GroundingEngine
from .errors import GroundingError, ServiceConfigurationError, ServiceOperationError
from .fetcher import fetch_document
from .models import (
    EvidencePack,
    FetchedDocument,
    GroundingRequest,
    PersistentIndexResult,
    SemanticHit,
)
from .persistent import index_documents, retrieve_persistent_semantic
from .providers.ollama_embedding import OllamaEmbeddingProvider
from .providers.qdrant import QdrantVectorStore
from .providers.searxng import SearxngProvider
from .retrieval import MAX_DOCUMENTS, MAX_RESULTS

MAX_INDEX_URLS = MAX_DOCUMENTS
MIN_API_KEY_CHARS = 32


def _required(mapping: Mapping[str, str], name: str) -> str:
    value = mapping.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ServiceConfigurationError(f"{name} is required")
    return value.strip()


def _csv(value: str | None, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


@dataclass(frozen=True, slots=True)
class ProductionSettings:
    searxng_url: str
    ollama_url: str
    qdrant_url: str
    api_key: str
    qdrant_api_key: str | None = None
    allowed_hosts: tuple[str, ...] = (
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
    )
    allowed_origins: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "ProductionSettings":
        searxng_url = _required(mapping, "MAX_GROUNDING_SEARXNG_URL")
        ollama_url = _required(mapping, "MAX_GROUNDING_OLLAMA_URL")
        qdrant_url = _required(mapping, "MAX_GROUNDING_QDRANT_URL")
        api_key = _required(mapping, "MAX_GROUNDING_API_KEY")
        if len(api_key) < MIN_API_KEY_CHARS:
            raise ServiceConfigurationError(
                f"MAX_GROUNDING_API_KEY must contain at least {MIN_API_KEY_CHARS} characters"
            )
        qdrant_api_key = mapping.get("MAX_GROUNDING_QDRANT_API_KEY")
        if isinstance(qdrant_api_key, str):
            qdrant_api_key = qdrant_api_key.strip() or None
        elif qdrant_api_key is not None:
            raise ServiceConfigurationError(
                "MAX_GROUNDING_QDRANT_API_KEY must be text when set"
            )

        return cls(
            searxng_url=searxng_url,
            ollama_url=ollama_url,
            qdrant_url=qdrant_url,
            api_key=api_key,
            qdrant_api_key=qdrant_api_key,
            allowed_hosts=_csv(
                mapping.get("MAX_GROUNDING_ALLOWED_HOSTS"),
                default=("127.0.0.1:*", "localhost:*", "[::1]:*"),
            ),
            allowed_origins=_csv(mapping.get("MAX_GROUNDING_ALLOWED_ORIGINS")),
        )

    @classmethod
    def from_env(cls) -> "ProductionSettings":
        return cls.from_mapping(os.environ)


@dataclass(frozen=True, slots=True)
class ServiceReadiness:
    ready: bool
    ollama: bool
    qdrant: bool
    search_configured: bool


class GroundingService:
    """Narrow production facade; it does not synthesize final answers."""

    def __init__(
        self,
        search_provider,
        embedding_provider,
        vector_store,
        *,
        fetcher: Callable[[str], FetchedDocument] = fetch_document,
    ) -> None:
        self._search_provider = search_provider
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._fetcher = fetcher
        self._engine = GroundingEngine(search_provider)

    def search_web(
        self,
        query: str,
        *,
        language: str = "en",
        country: str | None = None,
        freshness: str | None = None,
        max_search_rounds: int = 2,
        results_per_call: int = 5,
        min_evidence_sources: int = 1,
    ) -> EvidencePack:
        return self._engine.ground(
            GroundingRequest(
                query=query,
                language=language,
                country=country,
                freshness=freshness,
                max_search_rounds=max_search_rounds,
                results_per_call=results_per_call,
                min_evidence_sources=min_evidence_sources,
            )
        )

    def fetch_evidence(self, url: str) -> FetchedDocument:
        if not isinstance(url, str) or not url.strip():
            raise ServiceOperationError("evidence URL must be non-empty")
        try:
            return self._fetcher(url.strip())
        except ServiceOperationError:
            raise
        except Exception as exc:
            raise ServiceOperationError("secure evidence fetch failed") from exc

    def index_evidence(self, urls: Sequence[str]) -> PersistentIndexResult:
        if isinstance(urls, (str, bytes)) or not isinstance(urls, tuple):
            raise ServiceOperationError("index URLs must be a bounded tuple")
        if not urls or len(urls) > MAX_INDEX_URLS:
            raise ServiceOperationError(
                f"index URLs must contain between 1 and {MAX_INDEX_URLS} items"
            )
        documents = tuple(self.fetch_evidence(url) for url in urls)
        try:
            return index_documents(
                documents,
                self._embedding_provider,
                self._vector_store,
            )
        except GroundingError as exc:
            raise ServiceOperationError("persistent evidence indexing failed") from exc
        except Exception as exc:
            raise ServiceOperationError("persistent evidence indexing failed") from exc

    def query_evidence(
        self,
        query: str,
        *,
        limit: int = 5,
    ) -> tuple[SemanticHit, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            raise ServiceOperationError(
                f"query limit must be between 1 and {MAX_RESULTS}"
            )
        try:
            return retrieve_persistent_semantic(
                query,
                self._embedding_provider,
                self._vector_store,
                limit=limit,
            )
        except GroundingError as exc:
            raise ServiceOperationError("persistent evidence query failed") from exc
        except Exception as exc:
            raise ServiceOperationError("persistent evidence query failed") from exc

    def readiness(self) -> ServiceReadiness:
        try:
            self._embedding_provider.verify_runtime()
            self._vector_store.verify_runtime()
        except Exception as exc:
            raise ServiceOperationError("runtime readiness check failed") from exc
        return ServiceReadiness(
            ready=True,
            ollama=True,
            qdrant=True,
            search_configured=True,
        )


def build_production_runtime(settings: ProductionSettings) -> GroundingService:
    """Construct the concrete accepted Phase 11 providers behind one facade."""
    if not isinstance(settings, ProductionSettings):
        raise ServiceConfigurationError("settings must be ProductionSettings")
    search = SearxngProvider(settings.searxng_url)
    embedding = OllamaEmbeddingProvider(settings.ollama_url)
    store = QdrantVectorStore(
        settings.qdrant_url,
        api_key=settings.qdrant_api_key,
    )
    return GroundingService(search, embedding, store)

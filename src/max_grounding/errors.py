"""Domain errors for deterministic grounding behavior."""


class GroundingError(Exception):
    """Base error for grounding-domain failures."""


class InvalidGroundingRequest(GroundingError, ValueError):
    """Raised when a request violates a grounding policy invariant."""


class SearchBudgetExceeded(GroundingError):
    """Raised before a provider call would exceed the request budget."""


class SearchProviderError(GroundingError):
    """Raised when a search provider cannot return a valid bounded response."""


class InvalidProviderConfiguration(SearchProviderError, ValueError):
    """Raised when trusted provider configuration is malformed or unsafe."""


class FetchError(GroundingError):
    """Raised when an untrusted result page cannot be fetched safely."""


class RetrievalError(GroundingError, ValueError):
    """Raised when retrieval input or bounds are invalid."""


class EmbeddingProviderError(GroundingError):
    """Raised when an embedding provider returns invalid or unusable vectors."""


class RerankingError(GroundingError, ValueError):
    """Raised when reranking or compression input violates policy bounds."""


class RerankProviderError(GroundingError):
    """Raised when a reranking provider fails or returns invalid scores."""


class TemporalScoringError(GroundingError, ValueError):
    """Raised when temporal evidence metadata or scoring bounds are invalid."""


class AuthorityProviderError(GroundingError):
    """Raised when an authority policy provider fails or returns invalid scores."""


class EvidenceGraphError(GroundingError, ValueError):
    """Raised when structured evidence cannot form a valid bounded graph."""


class ClaimVerificationError(GroundingError, ValueError):
    """Raised when answer claims or evidence graphs violate verification policy."""


class RuntimeProviderError(GroundingError, ValueError):
    """Raised when a concrete runtime provider is unsafe, incompatible, or unavailable."""


class VectorStoreError(GroundingError, ValueError):
    """Raised when a persistent vector store violates the accepted contract."""


class PersistentIndexError(GroundingError, ValueError):
    """Raised when persistent semantic indexing or retrieval fails closed."""


class ServiceConfigurationError(GroundingError, ValueError):
    """Raised when required production service configuration is invalid."""


class ServiceOperationError(GroundingError):
    """Raised when a production service operation cannot complete safely."""

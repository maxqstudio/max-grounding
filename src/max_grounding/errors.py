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

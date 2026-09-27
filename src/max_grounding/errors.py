"""Domain errors for deterministic grounding core behavior."""


class GroundingError(Exception):
    """Base error for grounding-domain failures."""


class InvalidGroundingRequest(GroundingError, ValueError):
    """Raised when a request violates a grounding policy invariant."""


class SearchBudgetExceeded(GroundingError):
    """Raised before a provider call would exceed the request budget."""

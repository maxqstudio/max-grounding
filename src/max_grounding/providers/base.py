"""Provider boundary with no network implementation in Phase 1."""

from __future__ import annotations

from typing import Protocol, Sequence

from ..models import SearchQuery, SourceCandidate


class SearchProvider(Protocol):
    """Minimal provider contract consumed by the grounding engine."""

    def search(self, query: SearchQuery) -> Sequence[SourceCandidate]:
        """Return untrusted source candidates for one budgeted query."""
        ...


def invoke_search(
    provider: SearchProvider,
    query: SearchQuery,
) -> tuple[SourceCandidate, ...]:
    """Invoke exactly one provider call and normalize its collection shape."""
    return tuple(provider.search(query))

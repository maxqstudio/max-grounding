"""Deterministic bounded grounding orchestration."""

from __future__ import annotations

from .budget import SearchBudget
from .evidence import build_evidence_pack, normalize_candidates
from .models import EvidencePack, GroundingRequest, SearchQuery
from .policy import GroundingPolicy
from .providers.base import SearchProvider, invoke_search


class GroundingEngine:
    """Build an evidence pack without giving a provider unbounded authority."""

    def __init__(
        self,
        provider: SearchProvider,
        *,
        policy: GroundingPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy or GroundingPolicy()

    def ground(self, request: GroundingRequest) -> EvidencePack:
        validated = self._policy.validate_request(request)
        budget = SearchBudget(max_calls=validated.max_search_rounds)
        seen_urls: set[str] = set()
        sources = []
        provider_error = False

        while (
            budget.remaining > 0
            and len(sources) < validated.min_evidence_sources
        ):
            budget.consume_search_call()
            query = SearchQuery(
                query=validated.query,
                language=validated.language,
                country=validated.country,
                freshness=validated.freshness,
                limit=validated.results_per_call,
                round_index=budget.calls_used,
            )
            try:
                candidates = invoke_search(self._provider, query)
            except Exception:
                provider_error = True
                break

            sources.extend(normalize_candidates(candidates, seen_urls))

        return build_evidence_pack(
            validated,
            tuple(sources),
            budget.calls_used,
            provider_error=provider_error,
        )

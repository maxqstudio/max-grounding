"""Validation and normalization policy for grounding requests."""

from __future__ import annotations

from dataclasses import replace

from .errors import InvalidGroundingRequest
from .models import GroundingRequest

MAX_SEARCH_CALLS = 2
MAX_RESULTS_PER_CALL = 10
MAX_QUERY_CHARACTERS = 2_000


class GroundingPolicy:
    """Fail fast before any provider call when request limits are invalid."""

    def validate_request(self, request: GroundingRequest) -> GroundingRequest:
        query = " ".join(request.query.split())
        if not query:
            raise InvalidGroundingRequest("query must not be blank")
        if len(query) > MAX_QUERY_CHARACTERS:
            raise InvalidGroundingRequest(
                f"query exceeds {MAX_QUERY_CHARACTERS} characters"
            )

        if not 1 <= request.max_search_rounds <= MAX_SEARCH_CALLS:
            raise InvalidGroundingRequest(
                f"max_search_rounds must be between 1 and {MAX_SEARCH_CALLS}"
            )
        if not 1 <= request.results_per_call <= MAX_RESULTS_PER_CALL:
            raise InvalidGroundingRequest(
                f"results_per_call must be between 1 and {MAX_RESULTS_PER_CALL}"
            )
        if request.min_evidence_sources < 1:
            raise InvalidGroundingRequest("min_evidence_sources must be at least 1")

        language = request.language.strip().lower()
        if not language:
            raise InvalidGroundingRequest("language must not be blank")

        country = request.country.strip().upper() if request.country else None
        freshness = request.freshness.strip().lower() if request.freshness else None

        return replace(
            request,
            query=query,
            language=language,
            country=country,
            freshness=freshness,
        )

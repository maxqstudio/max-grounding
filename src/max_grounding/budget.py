"""Hard request-level search-call accounting."""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import SearchBudgetExceeded

MAX_SEARCH_CALLS = 2


@dataclass(slots=True)
class SearchBudget:
    """Consumes budget before provider invocation; never exceeds two calls."""

    max_calls: int
    calls_used: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if not 1 <= self.max_calls <= MAX_SEARCH_CALLS:
            raise ValueError(f"max_calls must be between 1 and {MAX_SEARCH_CALLS}")

    @property
    def remaining(self) -> int:
        return self.max_calls - self.calls_used

    def consume_search_call(self) -> None:
        if self.remaining <= 0:
            raise SearchBudgetExceeded("search-call budget exhausted")
        self.calls_used += 1

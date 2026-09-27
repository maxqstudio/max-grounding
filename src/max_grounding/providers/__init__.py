"""Search-provider contracts and built-in providers."""

from .base import SearchProvider, invoke_search
from .searxng import SearxngProvider

__all__ = ["SearchProvider", "SearxngProvider", "invoke_search"]

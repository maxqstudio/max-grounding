"""Admission policy for untrusted search-result URLs.

Phase 2 performs no DNS resolution and does not fetch result pages. This policy
blocks obvious unsafe targets early; the later fetch boundary must independently
resolve and validate every connection target to prevent DNS rebinding.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlsplit


def is_admissible_result_url(url: str) -> bool:
    """Return whether a search-result URL is safe to admit as evidence metadata."""
    try:
        parsed = urlsplit(url.strip())
        if parsed.scheme.lower() not in {"http", "https"}:
            return False
        if not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        _ = parsed.port
    except (TypeError, ValueError):
        return False

    host = parsed.hostname.rstrip(".").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False
    if "%" in host:
        return False

    try:
        address = ip_address(host)
    except ValueError:
        # Ambiguous all-numeric host spellings such as 127.1 are deliberately
        # rejected. Named hosts are resolved and revalidated only at fetch time.
        if host.replace(".", "").isdigit():
            return False
        return True

    return address.is_global

"""Network admission and connection-target policy for untrusted web URLs.

Search-result admission blocks obvious unsafe targets before fetch. The Phase 3
fetch boundary independently resolves every hostname, rejects the entire answer
set if any target is non-public, and pins the eventual connection to one of the
validated IP addresses.
"""

from __future__ import annotations

import socket
from ipaddress import ip_address
from urllib.parse import urlsplit

from .errors import FetchError


def is_admissible_result_url(url: str) -> bool:
    """Return whether an untrusted result URL is admissible for later fetching."""
    try:
        raw = url.strip()
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in raw):
            return False
        parsed = urlsplit(raw)
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
        # rejected. Named hosts are resolved and revalidated at fetch time.
        if host.replace(".", "").isdigit():
            return False
        return True

    return address.is_global and not address.is_multicast


def resolve_public_addresses(
    host: str,
    port: int,
    *,
    resolver=socket.getaddrinfo,
) -> tuple[str, ...]:
    """Resolve a hostname and fail closed unless every answer is a public IP."""
    try:
        rows = resolver(
            host,
            port,
            type=socket.SOCK_STREAM,
            proto=socket.IPPROTO_TCP,
        )
    except (socket.gaierror, OSError) as exc:
        raise FetchError("DNS resolution failed") from exc

    if not rows:
        raise FetchError("DNS resolution returned no addresses")

    addresses: list[str] = []
    seen: set[str] = set()
    for row in rows:
        try:
            sockaddr = row[4]
            raw_address = str(sockaddr[0]).split("%", 1)[0]
            address = ip_address(raw_address)
        except (IndexError, TypeError, ValueError) as exc:
            raise FetchError("DNS resolution returned an invalid address") from exc

        if not address.is_global or address.is_multicast:
            raise FetchError("DNS resolution returned a non-public address")

        normalized = str(address)
        if normalized not in seen:
            seen.add(normalized)
            addresses.append(normalized)

    if not addresses:
        raise FetchError("DNS resolution returned no usable addresses")
    return tuple(addresses)

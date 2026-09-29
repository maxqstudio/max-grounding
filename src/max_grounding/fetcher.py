"""Secure, bounded result-page fetching and text extraction."""

from __future__ import annotations

import http.client
import socket
import ssl
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from .errors import FetchError
from .models import FetchedDocument
from .network_policy import is_admissible_result_url, resolve_public_addresses

DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
_ALLOWED_MEDIA_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}
_ALLOWED_CHARSETS = {
    "utf-8",
    "utf8",
    "us-ascii",
    "ascii",
    "iso-8859-1",
    "latin-1",
    "windows-1252",
}


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(
        self,
        host: str,
        port: int,
        pinned_ip: str,
        timeout: float,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        host: str,
        port: int,
        pinned_ip: str,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._pinned_ip = pinned_ip

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_ip, self.port),
            self.timeout,
        )
        try:
            self.sock = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
        except Exception:
            raw_socket.close()
            raise


class _TextExtractor(HTMLParser):
    _BLOCKED = {"script", "style", "noscript", "template", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocked_depth = 0
        self.parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() in self._BLOCKED:
            self._blocked_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCKED and self._blocked_depth:
            self._blocked_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._blocked_depth:
            self.parts.append(data)


def _require_public_ip(ip: str) -> str:
    try:
        address = ip_address(ip)
    except ValueError as exc:
        raise FetchError("pinned connection target is not an IP address") from exc
    if not address.is_global or address.is_multicast:
        raise FetchError("pinned connection target is not public")
    return str(address)


def open_pinned_connection(
    *,
    scheme: str,
    host: str,
    port: int,
    ip: str,
    timeout_seconds: float,
):
    """Create a connection object whose socket dials only the validated IP."""
    if timeout_seconds <= 0:
        raise FetchError("timeout_seconds must be greater than zero")
    pinned_ip = _require_public_ip(ip)

    if scheme == "http" and port == 80:
        return _PinnedHTTPConnection(host, port, pinned_ip, timeout_seconds)
    if scheme == "https" and port == 443:
        context = ssl.create_default_context()
        return _PinnedHTTPSConnection(
            host,
            port,
            pinned_ip,
            timeout_seconds,
            context,
        )
    raise FetchError("only standard HTTP(S) ports are allowed")


def read_bounded_response(
    response,
    *,
    max_response_bytes: int,
) -> tuple[bytes, str, str]:
    """Read one approved response without redirects, compression, or overflow."""
    if max_response_bytes <= 0:
        raise FetchError("max_response_bytes must be greater than zero")

    status = int(getattr(response, "status", 0))
    if status != 200:
        raise FetchError("result page returned a non-200 response")

    encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding != "identity":
        raise FetchError("compressed result pages are not accepted")

    try:
        media_type = response.headers.get_content_type().lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise FetchError("invalid result-page content type") from exc
    if media_type not in _ALLOWED_MEDIA_TYPES:
        raise FetchError("result-page content type is not approved text")

    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            declared_length = int(length)
        except ValueError as exc:
            raise FetchError("invalid result-page Content-Length") from exc
        if declared_length < 0 or declared_length > max_response_bytes:
            raise FetchError("result page exceeds byte limit")

    body = response.read(max_response_bytes + 1)
    if len(body) > max_response_bytes:
        raise FetchError("result page exceeds byte limit")

    charset = response.headers.get_content_charset() or "utf-8"
    charset = charset.strip().lower().replace("_", "-")
    if charset not in _ALLOWED_CHARSETS:
        raise FetchError("result-page charset is not approved")

    return body, media_type, charset


def extract_text(
    body: bytes,
    *,
    media_type: str,
    charset: str,
) -> str:
    """Decode approved text and strip executable/styling HTML elements."""
    try:
        decoded = body.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise FetchError("result page could not be decoded safely") from exc

    if media_type == "text/plain":
        return " ".join(decoded.split())

    if media_type not in {"text/html", "application/xhtml+xml"}:
        raise FetchError("unsupported extraction media type")

    parser = _TextExtractor()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception as exc:
        raise FetchError("result-page HTML parsing failed") from exc
    return " ".join(" ".join(parser.parts).split())


def _normalized_fetch_target(url: str) -> tuple[str, str, int, str, str]:
    if not is_admissible_result_url(url):
        raise FetchError("result URL is not admissible")

    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    default_port = 443 if scheme == "https" else 80
    port = parsed.port or default_port
    if port != default_port:
        raise FetchError("only standard HTTP(S) ports are allowed")

    path = parsed.path or "/"
    request_target = path + (("?" + parsed.query) if parsed.query else "")
    host_for_url = f"[{host}]" if ":" in host else host
    normalized_url = urlunsplit(
        (scheme, host_for_url, path, parsed.query, "")
    )
    return scheme, host, port, request_target, normalized_url


def fetch_document(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    resolver=socket.getaddrinfo,
    connection_opener=open_pinned_connection,
) -> FetchedDocument:
    """Fetch and extract one untrusted result page through a pinned public IP."""
    if timeout_seconds <= 0:
        raise FetchError("timeout_seconds must be greater than zero")
    if max_response_bytes <= 0:
        raise FetchError("max_response_bytes must be greater than zero")

    scheme, host, port, request_target, normalized_url = _normalized_fetch_target(url)
    addresses = resolve_public_addresses(
        host,
        port,
        resolver=resolver,
    )
    pinned_ip = addresses[0]
    connection = connection_opener(
        scheme=scheme,
        host=host,
        port=port,
        ip=pinned_ip,
        timeout_seconds=timeout_seconds,
    )
    host_header = f"[{host}]" if ":" in host else host

    try:
        connection.request(
            "GET",
            request_target,
            headers={
                "Host": host_header,
                "Accept": "text/html, application/xhtml+xml, text/plain;q=0.9",
                "Accept-Encoding": "identity",
                "User-Agent": "max-grounding/0.0.1",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        body, media_type, charset = read_bounded_response(
            response,
            max_response_bytes=max_response_bytes,
        )
        text = extract_text(
            body,
            media_type=media_type,
            charset=charset,
        )
    except FetchError:
        raise
    except (http.client.HTTPException, OSError, ssl.SSLError, TimeoutError) as exc:
        raise FetchError("result-page transport failed") from exc
    finally:
        connection.close()

    return FetchedDocument(
        url=normalized_url,
        fetched_from_ip=pinned_ip,
        media_type=media_type,
        charset=charset,
        byte_length=len(body),
        text=text,
    )

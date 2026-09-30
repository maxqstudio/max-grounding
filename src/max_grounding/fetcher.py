"""Secure, bounded result-page fetching and text extraction."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from html.parser import HTMLParser
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

from . import __version__
from .errors import FetchError, FetchFailureCategory
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
    _STRUCTURED_FIELDS = {
        "name": "Product name",
        "product:name": "Product name",
        "brand": "Brand",
        "product:brand": "Brand",
        "manufacturer": "Manufacturer",
        "model": "Model",
        "gtin": "Barcode",
        "gtin8": "Barcode",
        "gtin12": "Barcode",
        "gtin13": "Barcode",
        "gtin14": "Barcode",
        "ean": "Barcode",
        "upc": "Barcode",
        "sku": "Product code",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocked_depth = 0
        self._jsonld_script_depth = 0
        self._jsonld_parts: list[str] = []
        self._table_cell_count = 0
        self.parts: list[str] = []

    def _append_structured_field(self, key: str, value: str | None) -> None:
        label = self._STRUCTURED_FIELDS.get(key.strip().casefold())
        normalized_value = " ".join((value or "").split())
        if label and normalized_value:
            self.parts.append(f"; {label} | {normalized_value}; ")

    @staticmethod
    def _jsonld_text(value: object) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            name = value.get("name")
            return name if isinstance(name, str) else None
        return None

    def _append_jsonld_product_fields(self) -> None:
        try:
            payload = json.loads("".join(self._jsonld_parts))
        except (json.JSONDecodeError, TypeError):
            return

        def visit(node: object) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item)
                return
            if not isinstance(node, dict):
                return

            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            is_product = any(
                isinstance(value, str)
                and value.rsplit("/", 1)[-1].casefold() == "product"
                for value in types
            )
            if is_product:
                for key, label in (
                    ("name", "Product name"),
                    ("brand", "Brand"),
                    ("manufacturer", "Manufacturer"),
                    ("model", "Model"),
                    ("gtin", "Barcode"),
                    ("gtin8", "Barcode"),
                    ("gtin12", "Barcode"),
                    ("gtin13", "Barcode"),
                    ("gtin14", "Barcode"),
                    ("sku", "Product code"),
                ):
                    value = self._jsonld_text(node.get(key))
                    if value and value.strip():
                        self.parts.append(f"; {label} | {' '.join(value.split())}; ")

            for key, value in node.items():
                if key in {"@graph", "mainEntity", "mainEntityOfPage"}:
                    visit(value)

        visit(payload)
        self._jsonld_parts.clear()

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        lowered_tag = tag.lower()
        attributes = {key.lower(): value for key, value in attrs}
        script_type = (attributes.get("type") or "").split(";", 1)[0].strip().casefold()
        if lowered_tag == "script" and script_type == "application/ld+json":
            self._jsonld_script_depth += 1
            self._blocked_depth += 1
            self._jsonld_parts = []
            return
        if lowered_tag == "tr":
            self._table_cell_count = 0
        if lowered_tag in self._BLOCKED:
            self._blocked_depth += 1

        if lowered_tag == "meta":
            key = attributes.get("itemprop") or attributes.get("property") or attributes.get("name")
            if key:
                self._append_structured_field(key, attributes.get("content"))
        elif attributes.get("itemprop") and attributes.get("content") is not None:
            self._append_structured_field(attributes["itemprop"] or "", attributes.get("content"))

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()
        if lowered_tag == "script" and self._jsonld_script_depth:
            self._append_jsonld_product_fields()
            self._jsonld_script_depth -= 1
        if lowered_tag in {"th", "td"}:
            self._table_cell_count += 1
            if self._table_cell_count % 2 == 0:
                self.parts.append(" ; ")
            else:
                self.parts.append(" | ")
        elif lowered_tag == "dt":
            self.parts.append(" | ")
        elif lowered_tag == "dd":
            self.parts.append(" ; ")
        elif lowered_tag == "tr" and self._table_cell_count % 2:
            self.parts.append(" ; ")
        if lowered_tag in self._BLOCKED and self._blocked_depth:
            self._blocked_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._jsonld_script_depth:
            self._jsonld_parts.append(data)
        elif not self._blocked_depth:
            self.parts.append(data)


def _require_public_ip(ip: str) -> str:
    try:
        address = ip_address(ip)
    except ValueError as exc:
        raise FetchError("pinned connection target is not an IP address", category=FetchFailureCategory.NETWORK_TARGET_REJECTED) from exc
    if not address.is_global or address.is_multicast:
        raise FetchError("pinned connection target is not public", category=FetchFailureCategory.NETWORK_TARGET_REJECTED)
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
    raise FetchError("only standard HTTP(S) ports are allowed", category=FetchFailureCategory.NETWORK_TARGET_REJECTED)


def read_bounded_response(
    response,
    *,
    max_response_bytes: int,
) -> tuple[bytes, str, str]:
    """Read one approved response without redirects, compression, or overflow."""
    if max_response_bytes <= 0:
        raise FetchError("max_response_bytes must be greater than zero")

    status = int(getattr(response, "status", 0))
    if 300 <= status < 400:
        raise FetchError(
            "result page redirects are not followed",
            category=FetchFailureCategory.REDIRECT,
            status_code=status,
        )
    if status != 200:
        raise FetchError(
            "result page returned a non-200 response",
            category=FetchFailureCategory.HTTP_STATUS,
            status_code=status,
        )

    encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
    if encoding != "identity":
        raise FetchError("compressed result pages are not accepted", category=FetchFailureCategory.COMPRESSION_POLICY)

    if not response.headers.get("Content-Type"):
        raise FetchError("result-page Content-Type is required", category=FetchFailureCategory.MEDIA_TYPE)

    try:
        media_type = response.headers.get_content_type().lower()
    except (AttributeError, TypeError, ValueError) as exc:
        raise FetchError("invalid result-page content type", category=FetchFailureCategory.MEDIA_TYPE) from exc
    if media_type not in _ALLOWED_MEDIA_TYPES:
        raise FetchError("result-page content type is not approved text", category=FetchFailureCategory.MEDIA_TYPE)

    length = response.headers.get("Content-Length")
    if length is not None:
        try:
            declared_length = int(length)
        except ValueError as exc:
            raise FetchError("invalid result-page Content-Length", category=FetchFailureCategory.SIZE_LIMIT) from exc
        if declared_length < 0 or declared_length > max_response_bytes:
            raise FetchError("result page exceeds byte limit", category=FetchFailureCategory.SIZE_LIMIT)

    body = response.read(max_response_bytes + 1)
    if len(body) > max_response_bytes:
        raise FetchError("result page exceeds byte limit", category=FetchFailureCategory.SIZE_LIMIT)

    charset = response.headers.get_content_charset() or "utf-8"
    charset = charset.strip().lower().replace("_", "-")
    if charset not in _ALLOWED_CHARSETS:
        raise FetchError("result-page charset is not approved", category=FetchFailureCategory.CHARSET)

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
        raise FetchError("result page could not be decoded safely", category=FetchFailureCategory.CHARSET) from exc

    if media_type == "text/plain":
        return " ".join(decoded.split())

    if media_type not in {"text/html", "application/xhtml+xml"}:
        raise FetchError("unsupported extraction media type", category=FetchFailureCategory.MEDIA_TYPE)

    parser = _TextExtractor()
    try:
        parser.feed(decoded)
        parser.close()
    except Exception as exc:
        raise FetchError("result-page HTML parsing failed", category=FetchFailureCategory.EXTRACTION) from exc
    return " ".join(" ".join(parser.parts).split())


def _normalized_fetch_target(url: str) -> tuple[str, str, int, str, str]:
    if not is_admissible_result_url(url):
        raise FetchError("result URL is not admissible", category=FetchFailureCategory.URL_POLICY)

    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").rstrip(".").lower()
    default_port = 443 if scheme == "https" else 80
    port = parsed.port or default_port
    if port != default_port:
        raise FetchError("only standard HTTP(S) ports are allowed", category=FetchFailureCategory.NETWORK_TARGET_REJECTED)

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
    connection = open_pinned_connection(
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
                "User-Agent": f"max-grounding/{__version__}",
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
    except TimeoutError as exc:
        raise FetchError("result-page transport timed out", category=FetchFailureCategory.TIMEOUT) from exc
    except ssl.SSLError as exc:
        raise FetchError("result-page TLS failed", category=FetchFailureCategory.TLS) from exc
    except socket.gaierror as exc:
        raise FetchError("result-page DNS resolution failed", category=FetchFailureCategory.DNS) from exc
    except http.client.HTTPException as exc:
        raise FetchError("result-page HTTP transport failed", category=FetchFailureCategory.CONNECTION) from exc
    except OSError as exc:
        raise FetchError("result-page connection failed", category=FetchFailureCategory.CONNECTION) from exc
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

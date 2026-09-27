"""Evidence URL normalization, deduplication, and fail-closed packing."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import (
    EvidencePack,
    EvidenceSource,
    EvidenceStatus,
    GroundingRequest,
    SourceCandidate,
)


def canonicalize_url(url: str) -> str | None:
    """Return a conservative HTTP(S) canonical URL or reject the candidate."""
    try:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None

        host = parsed.hostname.lower()
        port = parsed.port
    except ValueError:
        return None

    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    netloc = host if port is None or default_port else f"{host}:{port}"

    path = parsed.path or ""
    if path == "/":
        path = ""
    elif path:
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def normalize_candidates(
    candidates: Iterable[SourceCandidate],
    seen_urls: set[str],
) -> tuple[EvidenceSource, ...]:
    """Keep only valid, first-seen canonical URLs."""
    normalized: list[EvidenceSource] = []
    for candidate in candidates:
        canonical_url = canonicalize_url(candidate.url)
        if canonical_url is None or canonical_url in seen_urls:
            continue
        seen_urls.add(canonical_url)
        normalized.append(
            EvidenceSource(
                canonical_url=canonical_url,
                url=candidate.url,
                title=candidate.title,
                snippet=candidate.snippet,
                provider=candidate.provider,
                rank=candidate.rank,
            )
        )
    return tuple(normalized)


def build_evidence_pack(
    request: GroundingRequest,
    sources: tuple[EvidenceSource, ...],
    search_calls_used: int,
    *,
    provider_error: bool,
) -> EvidencePack:
    """Classify evidence without upgrading partial retrieval into grounding."""
    if len(sources) >= request.min_evidence_sources:
        status = EvidenceStatus.GROUNDED
        reason = "minimum evidence requirement satisfied"
    elif provider_error:
        status = EvidenceStatus.PROVIDER_ERROR
        reason = "search provider failed before evidence became sufficient"
    else:
        status = EvidenceStatus.INSUFFICIENT_EVIDENCE
        reason = "search budget exhausted without sufficient unique evidence"

    return EvidencePack(
        query=request.query,
        status=status,
        sources=sources,
        search_calls_used=search_calls_used,
        reason=reason,
    )

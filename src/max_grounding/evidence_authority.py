"""Server-owned evidence references and deterministic verified-output binding."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from .errors import InvalidGroundingRequest
from .evidence import canonicalize_url
from .evidence_graph import MAX_GRAPH_ASSERTIONS, build_evidence_graph
from .models import (
    AnswerClaim,
    EvidenceAssertion,
    EvidenceExcerpt,
    EvidenceGraph,
    EvidenceMetadata,
    EvidenceQualityScore,
    FetchedDocument,
    SynthesisPacket,
)
from .temporal import score_temporal_components
from .verification import (
    MAX_ANSWER_CLAIMS,
    MAX_CLAIM_KEY_CHARS,
    MAX_CLAIM_VALUE_CHARS,
    MAX_REQUIRED_SOURCES,
)

MAX_EVIDENCE_REFERENCES = 8
MAX_EVIDENCE_REFERENCE_CHARS = 43
MAX_EVIDENCE_REGISTRY_RECORDS = 128
DEFAULT_EVIDENCE_REFERENCE_TTL_SECONDS = 15 * 60
DEFAULT_REQUIRED_SOURCES = 2
_EVIDENCE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_BARCODE_CLAIM_KEYS = frozenset(
    {
        "barcode",
        "barcode_binding",
        "gtin",
        "gtin8",
        "gtin12",
        "gtin13",
        "gtin14",
        "ean",
        "ean8",
        "ean13",
        "upc",
        "upca",
    }
)
# Phase 12 has no configured source-authority scorer. Keep the score neutral:
# provenance is bound, but an unrated source is not assigned full authority.
UNRATED_SOURCE_AUTHORITY_SCORE = 0.5

_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "product_name": ("product name", "name", "nama"),
    "brand": ("brand", "merk", "marca"),
    "manufacturer": ("manufacturer", "produsen"),
    "model": ("model",),
    "package_size": (
        "package size",
        "net content",
        "netto",
        "isi bersih",
        "package",
        "size",
    ),
    "product_code": ("product code", "sku", "kode"),
    "barcode": (
        "barcode",
        "gtin8",
        "gtin12",
        "gtin-13",
        "gtin13",
        "gtin 13",
        "gtin14",
        "gtin",
        "ean8",
        "ean-13",
        "ean13",
        "ean 13",
        "upca",
        "ean",
        "upc-a",
        "upc",
        "kode batang",
        "codigo de barras",
    ),
    "barcode_binding": (
        "barcode",
        "gtin8",
        "gtin12",
        "gtin-13",
        "gtin13",
        "gtin 13",
        "gtin14",
        "gtin",
        "ean8",
        "ean-13",
        "ean13",
        "ean 13",
        "upca",
        "ean",
        "upc-a",
        "upc",
        "kode batang",
        "codigo de barras",
    ),
    "ply": ("ply",),
}
_EXACT_EVIDENCE_CLAIM_KEY = "exact_evidence"
_FIELD_SEPARATOR_PATTERN = r"[:=|,\-\u2010-\u2015\u2212]"
_FIELD_COPULAS = "is|are|was|were|adalah|ialah|bernama"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_text(value: object, *, label: str, maximum: int, casefold: bool) -> str:
    if not isinstance(value, str):
        raise InvalidGroundingRequest(f"{label} must be text")
    normalized = " ".join(unicodedata.normalize("NFKC", value).split())
    if casefold:
        normalized = normalized.casefold()
    if not normalized or len(normalized) > maximum:
        raise InvalidGroundingRequest(f"{label} is empty or exceeds its limit")
    return normalized


def normalize_candidate_claims(value: object) -> tuple[AnswerClaim, ...]:
    """Normalize bounded proposals and assign server-owned claim identifiers."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidGroundingRequest("claims must be a bounded sequence")
    if not 1 <= len(value) <= MAX_ANSWER_CLAIMS:
        raise InvalidGroundingRequest("claims count is outside the accepted range")

    result: list[AnswerClaim] = []
    required_keys = {"claim_key", "value"}
    allowed_keys = required_keys | {"claim_id"}
    for index, item in enumerate(value, start=1):
        if (
            not isinstance(item, Mapping)
            or not required_keys.issubset(item.keys())
            or not set(item.keys()).issubset(allowed_keys)
        ):
            raise InvalidGroundingRequest("claim proposal fields are invalid")
        claim_key = _normalized_text(
            item["claim_key"],
            label="claim_key",
            maximum=MAX_CLAIM_KEY_CHARS,
            casefold=True,
        )
        claim_value = _normalized_text(
            item["value"],
            label="claim value",
            maximum=MAX_CLAIM_VALUE_CHARS,
            casefold=False,
        )
        claim_id = f"claim-{index:04d}"
        result.append(
            AnswerClaim(
                claim_id=claim_id,
                text=f"{claim_key}: {claim_value}",
                claim_key=claim_key,
                value=claim_value,
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    document: FetchedDocument
    issued_at: datetime
    expires_at_monotonic: float


class EvidenceAuthority:
    """Bounded process-local registry; refs fail closed after expiry or restart."""

    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_EVIDENCE_REFERENCE_TTL_SECONDS,
        capacity: int = MAX_EVIDENCE_REGISTRY_RECORDS,
        monotonic_clock: Callable[[], float] = time.monotonic,
        utc_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise ValueError("evidence reference TTL must be a positive integer")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("evidence registry capacity must be a positive integer")
        self._ttl_seconds = ttl_seconds
        self._capacity = capacity
        self._monotonic_clock = monotonic_clock
        self._utc_clock = utc_clock
        self._records: OrderedDict[str, EvidenceRecord] = OrderedDict()
        self._lock = threading.RLock()

    def _purge_expired(self, now: float) -> None:
        expired = [
            reference
            for reference, record in self._records.items()
            if record.expires_at_monotonic <= now
        ]
        for reference in expired:
            self._records.pop(reference, None)

    def issue(self, document: FetchedDocument) -> FetchedDocument:
        if not isinstance(document, FetchedDocument):
            raise InvalidGroundingRequest("secure fetch result is invalid")
        canonical_url = canonicalize_url(document.url)
        if canonical_url is None:
            raise InvalidGroundingRequest("secure fetch result has no canonical HTTP URL")
        normalized_document = replace(document, url=canonical_url, evidence_ref=None)
        now = self._monotonic_clock()
        issued_at = self._utc_clock()
        if issued_at.tzinfo is None or issued_at.utcoffset() != timezone.utc.utcoffset(issued_at):
            raise InvalidGroundingRequest("evidence registry clock must be UTC")

        with self._lock:
            self._purge_expired(now)
            while len(self._records) >= self._capacity:
                self._records.popitem(last=False)
            reference = secrets.token_urlsafe(32)
            while reference in self._records:
                reference = secrets.token_urlsafe(32)
            record = EvidenceRecord(
                document=normalized_document,
                issued_at=issued_at,
                expires_at_monotonic=now + self._ttl_seconds,
            )
            self._records[reference] = record
        return replace(normalized_document, evidence_ref=reference)

    def resolve(self, references: object) -> tuple[EvidenceRecord, ...]:
        if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
            raise InvalidGroundingRequest("evidence_refs must be a bounded sequence")
        if not 1 <= len(references) <= MAX_EVIDENCE_REFERENCES:
            raise InvalidGroundingRequest("evidence_refs count is outside the accepted range")
        if any(
            not isinstance(reference, str)
            or not _EVIDENCE_REFERENCE_PATTERN.fullmatch(reference)
            for reference in references
        ):
            raise InvalidGroundingRequest("evidence_refs contains an invalid reference")
        if len(set(references)) != len(references):
            raise InvalidGroundingRequest("evidence_refs must be unique")

        now = self._monotonic_clock()
        with self._lock:
            self._purge_expired(now)
            records: list[EvidenceRecord] = []
            for reference in references:
                record = self._records.get(reference)
                if record is None:
                    raise InvalidGroundingRequest("unknown or expired evidence reference")
                records.append(record)
            return tuple(records)


def _field_labels(claim_key: str) -> tuple[str, ...]:
    aliases = _FIELD_LABELS.get(claim_key)
    if aliases is not None:
        return aliases
    readable = re.sub(r"[_-]+", " ", claim_key).strip()
    return (readable,) if readable else ()


def _segments(text: str) -> tuple[tuple[str, int], ...]:
    return tuple(
        (segment.strip(), index)
        for index, segment in enumerate(
            re.split(r"(?<=[.!?])\s+|[;\r\n]+", text)
        )
        if segment.strip()
    )


def _normalize_evidence_span(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _is_word_character(value: str) -> bool:
    return value == "_" or value.isalnum()


def _contains_exact_span(source: str, proposal: str) -> bool:
    start = source.find(proposal)
    while start >= 0:
        end = start + len(proposal)
        left_is_boundary = start == 0 or not _is_word_character(source[start - 1])
        right_is_boundary = end == len(source) or not _is_word_character(source[end])
        if left_is_boundary and right_is_boundary:
            return True
        start = source.find(proposal, start + 1)
    return False


def _exact_evidence_values(
    text: str,
    proposed_values: set[str],
) -> tuple[tuple[str, str, int], ...]:
    """Match literal normalized spans and retain the enclosing server source segment."""
    if not proposed_values:
        return ()
    wanted = {_normalize_evidence_span(value) for value in proposed_values}
    found: list[tuple[str, str, int]] = []
    for segment, index in _segments(text):
        normalized_segment = _normalize_evidence_span(segment)
        for proposal in wanted:
            if _contains_exact_span(normalized_segment, proposal):
                found.append((proposal, segment, index))
    return tuple(found)


def _labeled_values(text: str, claim_key: str) -> tuple[tuple[str, str, int], ...]:
    labels = sorted(_field_labels(claim_key), key=len, reverse=True)
    if not labels:
        return ()
    label_pattern = "|".join(re.escape(label) for label in labels)
    label_prefix = (
        rf"^\s*(?:\|\s*)?(?:(?:the|this|that)\s+)?"
        rf"(?:product\s+)?(?:{label_pattern})"
        rf"(?:\s+of\s+(?:(?:the|this|that)\s+)?product)?"
    )
    field_pattern = re.compile(
        label_prefix
        + rf"\s*(?P<separator>{_FIELD_SEPARATOR_PATTERN})\s*"
        + r"(?P<value>.*?)(?:\s*\|)?\s*$",
        re.IGNORECASE,
    )
    prose_pattern = re.compile(
        label_prefix
        + rf"\s+(?:{_FIELD_COPULAS})\s+(?P<value>.*?)\s*$",
        re.IGNORECASE,
    )
    inverted_pattern = re.compile(
        rf"^\s*(?P<value>.+?)\s+(?:is|was|are|were)\s+"
        rf"(?:(?:a|an|the)\s+)?(?:product\s+)?(?:{label_pattern})\s*[.!?]?$",
        re.IGNORECASE,
    )
    found: list[tuple[str, str, int]] = []
    for stripped, index in _segments(text):
        match = field_pattern.match(stripped) or prose_pattern.match(stripped)
        if match is None:
            match = inverted_pattern.match(stripped)
        if match is None:
            continue
        raw_value = match.group("value").strip().strip("\"'“”‘’")
        raw_value = raw_value.rstrip(" \t.,;:!?|")
        if not raw_value or len(raw_value) > MAX_CLAIM_VALUE_CHARS:
            continue
        normalized_value = " ".join(
            unicodedata.normalize("NFKC", raw_value).split()
        ).casefold()
        found.append((normalized_value, stripped, index))
    return tuple(found)


def build_server_owned_evidence_graph(
    claims: Sequence[AnswerClaim],
    evidence_records: Sequence[EvidenceRecord],
    *,
    now: datetime | None = None,
) -> EvidenceGraph:
    """Build assertions only from structured fields or literal spans in fetched evidence."""
    if not claims or not evidence_records:
        return build_evidence_graph(())
    evaluated_at = now or _utc_now()
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() != timezone.utc.utcoffset(evaluated_at):
        raise InvalidGroundingRequest("verification clock must be UTC")

    claim_keys = tuple(dict.fromkeys(claim.claim_key.casefold() for claim in claims))
    proposed_values_by_key: dict[str, set[str]] = {}
    for claim in claims:
        key = claim.claim_key.casefold()
        proposed_values_by_key.setdefault(key, set()).add(claim.value)
    barcode_values = {
        claim.value.casefold()
        for claim in claims
        if claim.claim_key.casefold() in _BARCODE_CLAIM_KEYS
    }
    if len(barcode_values) > 1:
        raise InvalidGroundingRequest("one verification request cannot mix barcode identities")
    expected_barcode = next(iter(barcode_values), None)

    candidates: dict[tuple[str, str, str], tuple[EvidenceRecord, str, int]] = {}
    for record in evidence_records:
        canonical_url = canonicalize_url(record.document.url)
        if canonical_url is None:
            raise InvalidGroundingRequest("evidence record URL is invalid")
        document_barcode_values = {
            value
            for value, _excerpt, _index in _labeled_values(record.document.text, "barcode_binding")
        }
        binds_requested_product = (
            expected_barcode is None or expected_barcode in document_barcode_values
        )
        for claim_key in claim_keys:
            if (
                expected_barcode is not None
                and claim_key not in _BARCODE_CLAIM_KEYS
                and not binds_requested_product
            ):
                continue
            if claim_key == _EXACT_EVIDENCE_CLAIM_KEY:
                values = _exact_evidence_values(
                    record.document.text,
                    proposed_values_by_key.get(claim_key, set()),
                )
            else:
                values = _labeled_values(record.document.text, claim_key)
            for value, excerpt_text, segment_index in values:
                key = (canonical_url, claim_key, value)
                # Identical canonical source bytes are one independent assertion.
                candidates.setdefault(key, (record, excerpt_text, segment_index))

    if len(candidates) > MAX_GRAPH_ASSERTIONS:
        raise InvalidGroundingRequest(
            f"verified evidence exceeds the {MAX_GRAPH_ASSERTIONS}-assertion cap"
        )

    assertions: list[EvidenceAssertion] = []
    for rank, ((source_url, claim_key, value), (record, excerpt_text, segment_index)) in enumerate(
        sorted(candidates.items()),
        start=1,
    ):
        identity = hashlib.sha256(
            "\0".join((source_url, record.document.text, claim_key, value, excerpt_text)).encode("utf-8")
        ).hexdigest()
        chunk_id = f"sha256:{identity}"
        assertion_id = f"assert:{identity[:32]}"
        excerpt = EvidenceExcerpt(
            source_url=source_url,
            chunk_id=chunk_id,
            chunk_index=segment_index,
            rerank_rank=rank,
            text=excerpt_text,
            char_count=len(excerpt_text),
        )
        metadata = EvidenceMetadata(
            source_url=source_url,
            chunk_id=chunk_id,
            retrieved_at=record.issued_at,
            source_type=record.document.media_type,
        )
        temporal = score_temporal_components((metadata,), now=evaluated_at)[0]
        authority_score = UNRATED_SOURCE_AUTHORITY_SCORE
        quality = EvidenceQualityScore(
            excerpt=excerpt,
            metadata=metadata,
            authority_score=authority_score,
            freshness_score=temporal.freshness_score,
            temporal_validity=temporal.temporal_validity,
            score=authority_score * temporal.freshness_score * temporal.temporal_validity,
            rank=rank,
        )
        assertions.append(
            EvidenceAssertion(
                assertion_id=assertion_id,
                claim_key=claim_key,
                value=value,
                exclusive=True,
                evidence=quality,
            )
        )
    return build_evidence_graph(tuple(assertions))


def build_authoritative_verification_packet(
    packet: SynthesisPacket,
    claims: Sequence[AnswerClaim],
) -> SynthesisPacket:
    """Serialize Phase 10 output while restoring identifiers and input value casing."""
    if not isinstance(packet, SynthesisPacket):
        raise InvalidGroundingRequest("synthesis packet is invalid")
    originals = {claim.claim_id.casefold(): claim for claim in claims}

    def preserve(items):
        return tuple(
            replace(item, claim=originals[item.claim.claim_id.casefold()])
            for item in items
        )

    return SynthesisPacket(
        verifications=preserve(packet.verifications),
        synthesis_claims=preserve(packet.synthesis_claims),
        blocked_claims=preserve(packet.blocked_claims),
    )

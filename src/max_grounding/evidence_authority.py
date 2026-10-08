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
    EvidenceSpan,
    FetchedDocument,
    SynthesisPacket,
    VerificationCorrection,
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
MAX_EVIDENCE_SPANS_PER_SELECTION = 8
MAX_EVIDENCE_SPANS_PER_CLAIM = 8
MAX_EVIDENCE_SPAN_REFERENCES = MAX_ANSWER_CLAIMS * MAX_EVIDENCE_SPANS_PER_CLAIM
MAX_EVIDENCE_SPAN_REGISTRY_RECORDS = MAX_EVIDENCE_REGISTRY_RECORDS * MAX_EVIDENCE_SPANS_PER_SELECTION
MAX_EVIDENCE_SPAN_CHARS = 2048
MAX_EVIDENCE_SPAN_QUERY_CHARS = 512
DEFAULT_EVIDENCE_REFERENCE_TTL_SECONDS = 15 * 60
DEFAULT_REQUIRED_SOURCES = 2
_EVIDENCE_REFERENCE_PATTERN = re.compile(r"^ev_[A-Za-z0-9_-]{40}$")
_EVIDENCE_SPAN_REFERENCE_PATTERN = re.compile(r"^sp_[A-Za-z0-9_-]{40}$")
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
    "product_name": ("product name", "product heading", "name", "nama"),
    "brand": ("brand", "merk", "merek", "marca"),
    "product_type": (
        "product type",
        "type",
        "product category",
        "category",
        "kategori produk",
        "kategori",
        "tipe produk",
    ),
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
        "kode",
        "kode batang",
        "product code",
        "kode produk",
        "sku",
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
        "kode",
        "kode batang",
        "product code",
        "kode produk",
        "sku",
        "codigo de barras",
    ),
    "ply": ("ply",),
}

_INLINE_FIELD_TERMINATORS = (
    "beranda",
    "home",
    "promosi",
    "promotion",
    "flashdeal",
    "flash deal",
    "keranjang",
    "cart",
    "masuk",
    "login",
    "daftar",
    "stok",
    "stock",
    "harga",
    "price",
    "qty",
    "quantity",
    "deskripsi",
    "description",
    "produk terkait",
    "related products",
    "tambah ke keranjang",
    "add to cart",
)
_EXACT_EVIDENCE_CLAIM_KEY = "exact_evidence"
_FIELD_SEPARATOR_PATTERN = r"[:=|,\-\u2010-\u2015\u2212]"
_FIELD_COPULAS = "is|are|was|were|adalah|ialah|bernama"
_PACKAGE_MEASURE_PATTERN = re.compile(
    r"(?<![\w])\d+(?:[.,]\d+)?\s*(?:"
    r"ml|millilit(?:er|re)s?|l|lit(?:er|re)s?|"
    r"mg|kg|g|gr|gram(?:s)?|"
    r"sheet(?:s)?|sht|lembar|"
    r"pcs|pc|pieces?|sachets?|packs?|"
    r"(?:['\u2019]s)|ply"
    r")(?![a-z])",
    re.IGNORECASE,
)
_GTIN_TOKEN_PATTERN = re.compile(
    r"(?<![0-9])(?:[0-9]{14}|[0-9]{13}|[0-9]{12}|[0-9]{8})(?![0-9])"
)


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


@dataclass(frozen=True, slots=True)
class NormalizedClaimProposal:
    """One normalized proposal plus server-issued evidence-span references."""

    claim: AnswerClaim
    verification_mode: str | None
    evidence_span_refs: tuple[str, ...]


def normalize_candidate_proposals(value: object) -> tuple[NormalizedClaimProposal, ...]:
    """Normalize bounded proposals while keeping caller bookkeeping non-authoritative."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise InvalidGroundingRequest("claims must be a bounded sequence")
    if not 1 <= len(value) <= MAX_ANSWER_CLAIMS:
        raise InvalidGroundingRequest("claims count is outside the accepted range")

    result: list[NormalizedClaimProposal] = []
    required_keys = {"claim_key", "value"}
    allowed_keys = required_keys | {
        "claim_id",
        "verification_mode",
        "evidence_span_refs",
    }
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
        bookkeeping_id = item.get("claim_id")
        if bookkeeping_id is not None and (
            isinstance(bookkeeping_id, bool)
            or not isinstance(bookkeeping_id, (str, int))
        ):
            raise InvalidGroundingRequest("claim_id is invalid")

        raw_mode = item.get("verification_mode")
        if raw_mode is None:
            verification_mode = None
        elif isinstance(raw_mode, str):
            verification_mode = raw_mode.strip().upper()
            if verification_mode not in {"STRUCTURED_FIELD", "EXTRACTIVE_STATEMENT"}:
                raise InvalidGroundingRequest("verification_mode is invalid")
        else:
            raise InvalidGroundingRequest("verification_mode must be text")
        if (
            verification_mode == "EXTRACTIVE_STATEMENT"
            and claim_key not in {_EXACT_EVIDENCE_CLAIM_KEY, "extractive_statement"}
        ):
            raise InvalidGroundingRequest(
                "EXTRACTIVE_STATEMENT is reserved for literal evidence claims"
            )

        raw_span_refs = item.get("evidence_span_refs", ())
        if isinstance(raw_span_refs, (str, bytes)) or not isinstance(
            raw_span_refs, Sequence
        ):
            raise InvalidGroundingRequest("evidence_span_refs must be a bounded sequence")
        if len(raw_span_refs) > MAX_EVIDENCE_SPANS_PER_CLAIM:
            raise InvalidGroundingRequest("evidence_span_refs count exceeds its limit")
        span_refs = tuple(raw_span_refs)
        if any(
            not isinstance(reference, str)
            or not _EVIDENCE_SPAN_REFERENCE_PATTERN.fullmatch(reference)
            for reference in span_refs
        ):
            raise InvalidGroundingRequest("evidence_span_refs contains an invalid reference")
        if len(set(span_refs)) != len(span_refs):
            raise InvalidGroundingRequest("evidence_span_refs must be unique")
        if bool(verification_mode) != bool(span_refs):
            raise InvalidGroundingRequest(
                "explicit verification modes require server-issued evidence spans"
            )

        claim_id = f"claim-{index:04d}"
        result.append(
            NormalizedClaimProposal(
                claim=AnswerClaim(
                    claim_id=claim_id,
                    text=f"{claim_key}: {claim_value}",
                    claim_key=claim_key,
                    value=claim_value,
                ),
                verification_mode=verification_mode,
                evidence_span_refs=span_refs,
            )
        )
    has_spans = any(item.evidence_span_refs for item in result)
    if has_spans and any(not item.evidence_span_refs for item in result):
        raise InvalidGroundingRequest(
            "all claims in a span-scoped request must use explicit evidence spans"
        )
    if sum(len(item.evidence_span_refs) for item in result) > MAX_EVIDENCE_SPAN_REFERENCES:
        raise InvalidGroundingRequest("evidence_span_refs count exceeds its limit")
    return tuple(result)


def normalize_candidate_claims(value: object) -> tuple[AnswerClaim, ...]:
    """Normalize bounded proposals and assign server-owned claim identifiers."""
    return tuple(
        proposal.claim for proposal in normalize_candidate_proposals(value)
    )


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    document: FetchedDocument
    issued_at: datetime
    expires_at_monotonic: float


@dataclass(frozen=True, slots=True)
class ResolvedEvidenceSpan:
    span: EvidenceSpan
    evidence_record: EvidenceRecord


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
        self._spans: OrderedDict[str, ResolvedEvidenceSpan] = OrderedDict()
        self._lock = threading.RLock()

    def _purge_orphaned_spans(self) -> None:
        for span_ref, resolved in tuple(self._spans.items()):
            evidence_ref = resolved.span.evidence_ref
            if self._records.get(evidence_ref) is not resolved.evidence_record:
                self._spans.pop(span_ref, None)

    def _purge_expired(self, now: float) -> None:
        expired = [
            reference
            for reference, record in self._records.items()
            if record.expires_at_monotonic <= now
        ]
        for reference in expired:
            self._records.pop(reference, None)
        self._purge_orphaned_spans()

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
            self._purge_orphaned_spans()
            reference = "ev_" + secrets.token_urlsafe(30)
            while reference in self._records or reference in self._spans:
                reference = "ev_" + secrets.token_urlsafe(30)
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

    def select_spans(
        self,
        evidence_ref: str,
        *,
        query: str,
        limit: int = 4,
    ) -> tuple[EvidenceSpan, ...]:
        """Select exact, bounded spans from one currently resolvable fetched document."""
        if (
            not isinstance(evidence_ref, str)
            or not _EVIDENCE_REFERENCE_PATTERN.fullmatch(evidence_ref)
        ):
            raise InvalidGroundingRequest("evidence_ref is invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_EVIDENCE_SPANS_PER_SELECTION:
            raise InvalidGroundingRequest("span limit is outside the accepted range")
        normalized_query = _normalized_text(
            query,
            label="span query",
            maximum=MAX_EVIDENCE_SPAN_QUERY_CHARS,
            casefold=True,
        )

        now = self._monotonic_clock()
        with self._lock:
            self._purge_expired(now)
            evidence_record = self._records.get(evidence_ref)
            if evidence_record is None:
                raise InvalidGroundingRequest("unknown or expired evidence reference")
            candidates = _ranked_evidence_segments(
                evidence_record.document.text,
                normalized_query,
            )
            selected: list[EvidenceSpan] = []
            for start, end in candidates[:limit]:
                while len(self._spans) >= MAX_EVIDENCE_SPAN_REGISTRY_RECORDS:
                    self._spans.popitem(last=False)
                reference = "sp_" + secrets.token_urlsafe(30)
                while reference in self._records or reference in self._spans:
                    reference = "sp_" + secrets.token_urlsafe(30)
                span = EvidenceSpan(
                    evidence_span_ref=reference,
                    evidence_ref=evidence_ref,
                    source_url=evidence_record.document.url,
                    excerpt=evidence_record.document.text[start:end],
                    start_offset=start,
                    end_offset=end,
                )
                self._spans[reference] = ResolvedEvidenceSpan(
                    span=span,
                    evidence_record=evidence_record,
                )
                selected.append(span)
            return tuple(selected)

    def resolve_spans(self, references: object) -> tuple[ResolvedEvidenceSpan, ...]:
        """Resolve only unique, unexpired span tokens issued by this process."""
        if isinstance(references, (str, bytes)) or not isinstance(references, Sequence):
            raise InvalidGroundingRequest("evidence_span_refs must be a bounded sequence")
        if not 1 <= len(references) <= MAX_EVIDENCE_SPAN_REFERENCES:
            raise InvalidGroundingRequest(
                "evidence_span_refs count is outside the accepted range"
            )
        if any(
            not isinstance(reference, str)
            or not _EVIDENCE_SPAN_REFERENCE_PATTERN.fullmatch(reference)
            for reference in references
        ):
            raise InvalidGroundingRequest(
                "evidence_span_refs contains an invalid reference"
            )
        if len(set(references)) != len(references):
            raise InvalidGroundingRequest("evidence_span_refs must be unique")

        now = self._monotonic_clock()
        with self._lock:
            self._purge_expired(now)
            resolved: list[ResolvedEvidenceSpan] = []
            for reference in references:
                item = self._spans.get(reference)
                if item is None:
                    raise InvalidGroundingRequest(
                        "unknown or expired evidence span reference"
                    )
                if self._records.get(item.span.evidence_ref) is not item.evidence_record:
                    self._spans.pop(reference, None)
                    raise InvalidGroundingRequest(
                        "unknown or expired evidence span reference"
                    )
                resolved.append(item)
            return tuple(resolved)


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


def _segments_with_offsets(text: str) -> tuple[tuple[int, int], ...]:
    """Return bounded sentence-like spans with offsets into the untouched source text."""
    boundaries = re.finditer(r"(?<=[.!?])\s+|[;\r\n]+", text)
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for boundary in boundaries:
        ranges.append((cursor, boundary.start()))
        cursor = boundary.end()
    ranges.append((cursor, len(text)))

    result: list[tuple[int, int]] = []
    for raw_start, raw_end in ranges:
        start = raw_start
        end = raw_end
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        while start < end:
            piece_end = min(start + MAX_EVIDENCE_SPAN_CHARS, end)
            if piece_end < end:
                whitespace = text.rfind(" ", start + 1, piece_end + 1)
                if whitespace > start:
                    piece_end = whitespace
            result.append((start, piece_end))
            start = piece_end
            while start < end and text[start].isspace():
                start += 1
    return tuple(result)


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


def _ranked_evidence_segments(text: str, query: str) -> tuple[tuple[int, int], ...]:
    normalized_query = _normalize_evidence_span(query)
    query_tokens = tuple(dict.fromkeys(re.findall(r"[^\W_]+", normalized_query)))
    if not query_tokens:
        return ()

    ranked: list[tuple[int, int, int, int, int]] = []
    for position, (start, end) in enumerate(_segments_with_offsets(text)):
        normalized_text = _normalize_evidence_span(text[start:end])
        exact_phrase = _contains_exact_span(normalized_text, normalized_query)
        source_tokens = set(re.findall(r"[^\W_]+", normalized_text))
        matched_tokens = sum(token in source_tokens for token in query_tokens)
        if not exact_phrase and matched_tokens == 0:
            continue
        ranked.append((-int(exact_phrase), -matched_tokens, position, start, end))

    ranked.sort()
    return tuple((start, end) for _phrase, _tokens, _position, start, end in ranked)


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


def _normalized_field_value(value: str) -> str | None:
    raw_value = value.strip().strip("\"'“”‘’")
    raw_value = raw_value.rstrip(" \t.,;:!?|")
    if not raw_value or len(raw_value) > MAX_CLAIM_VALUE_CHARS:
        return None
    return " ".join(unicodedata.normalize("NFKC", raw_value).split()).casefold()


def _is_valid_gtin(value: str) -> bool:
    """Accept only checksum-valid GTIN-8/12/13/14 values as barcode evidence."""
    if len(value) not in {8, 12, 13, 14} or not value.isascii() or not value.isdigit():
        return False
    weighted_sum = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(value[:-1]))
    )
    return (weighted_sum + int(value[-1])) % 10 == 0


def _pipe_labeled_values(
    segment: str,
    claim_key: str,
    index: int,
) -> tuple[tuple[tuple[str, str, int], ...], bool]:
    """Parse label/value pairs from flattened catalog rows without crossing labels."""
    if "|" not in segment:
        return (), False

    labels = {_normalize_evidence_span(label) for label in _field_labels(claim_key)}
    all_labels = {
        _normalize_evidence_span(label)
        for aliases in _FIELD_LABELS.values()
        for label in aliases
    }
    all_labels.update(labels)
    parts = [part.strip() for part in segment.split("|")]
    found: list[tuple[str, str, int]] = []
    recognized = False

    def add_value(raw_value: str, matched_label: str) -> None:
        if matched_label not in labels:
            return
        normalized = _normalized_field_value(raw_value)
        if normalized is not None:
            found.append((normalized, segment.strip(), index))

    for part_index, part in enumerate(parts):
        if not part:
            continue
        normalized_part = _normalize_evidence_span(part)
        if normalized_part in all_labels:
            recognized = True
            if part_index + 1 < len(parts):
                next_value = parts[part_index + 1]
                if _normalize_evidence_span(next_value) not in all_labels:
                    add_value(next_value, normalized_part)
            continue

        match = re.match(r"^\s*(?P<label>[^:=]+?)\s*[:=]\s*(?P<value>.+?)\s*$", part)
        if match is None:
            continue
        normalized_label = _normalize_evidence_span(match.group("label"))
        if normalized_label in all_labels:
            recognized = True
            add_value(match.group("value"), normalized_label)

    return tuple(found), recognized


def _inline_labeled_values(
    segment: str,
    claim_key: str,
    index: int,
) -> tuple[tuple[tuple[str, str, int], ...], bool]:
    """Read adjacent marketplace labels from flattened HTML text without crossing fields."""
    normalized = _normalize_evidence_span(segment)
    target_labels = {
        _normalize_evidence_span(label) for label in _field_labels(claim_key)
    }
    all_labels = {
        _normalize_evidence_span(label)
        for aliases in _FIELD_LABELS.values()
        for label in aliases
    }
    all_labels.update(target_labels)
    label_alternation = "|".join(
        re.escape(label) for label in sorted(all_labels, key=len, reverse=True)
    )
    label_pattern = re.compile(
        rf"(?<![\w])(?P<label>{label_alternation})(?![\w])"
    )
    labels = tuple(label_pattern.finditer(normalized))
    if not labels:
        return (), False

    terminator_alternation = "|".join(
        re.escape(_normalize_evidence_span(value))
        for value in sorted(_INLINE_FIELD_TERMINATORS, key=len, reverse=True)
    )
    terminator_pattern = re.compile(
        rf"(?<![\w])(?:{terminator_alternation})(?![\w])"
    )
    terminators = tuple(terminator_pattern.finditer(normalized))
    found: list[tuple[str, str, int]] = []
    for label_position, match in enumerate(labels):
        if match.group("label") not in target_labels:
            continue
        label_context = normalized[max(0, match.start() - 20) : match.start()]
        if re.search(
            r"(?:menu|navigation|nav|filter|browse|tanpa|all|semua)\s+$",
            label_context,
        ):
            continue
        start = match.end()
        while start < len(normalized) and normalized[start] in (
            " \t|:=;,-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
        ):
            start += 1
        next_label = next(
            (
                item.start()
                for item in labels[label_position + 1 :]
                if item.start() >= start
            ),
            len(normalized),
        )
        next_terminator = next(
            (item.start() for item in terminators if item.start() >= start),
            len(normalized),
        )
        end = min(next_label, next_terminator)
        raw_value = normalized[start:end].strip(
            " \t|:=;,-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
        )
        raw_value = re.sub(r"^(?:is|was|are|were)\s+", "", raw_value)
        if "{{" in raw_value or "}}" in raw_value:
            continue
        value = _normalized_field_value(raw_value)
        if value is not None:
            if value in {"all", "semua", "none", "unknown", "n/a", "not applicable"}:
                continue
            found.append((value, segment.strip(), index))
    return tuple(found), True


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
        pipe_values, pipe_fields_found = _pipe_labeled_values(
            stripped,
            claim_key,
            index,
        )
        if pipe_fields_found:
            found.extend(pipe_values)
            continue
        inline_values, inline_fields_found = _inline_labeled_values(
            stripped,
            claim_key,
            index,
        )
        if inline_fields_found:
            found.extend(inline_values)
            if inline_values:
                continue
        match = field_pattern.match(stripped) or prose_pattern.match(stripped)
        if match is None:
            match = inverted_pattern.match(stripped)
        if match is None:
            continue
        normalized_value = _normalized_field_value(match.group("value"))
        if normalized_value is None:
            continue
        found.append((normalized_value, stripped, index))

    if claim_key.casefold() in _BARCODE_CLAIM_KEYS:
        verified: list[tuple[str, str, int]] = []
        for value, excerpt, index in found:
            match = _GTIN_TOKEN_PATTERN.match(value)
            if match is not None and _is_valid_gtin(match.group()):
                verified.append((match.group(), excerpt, index))
        return tuple(verified)

    if claim_key.casefold() == "package_size":
        # Product pages commonly put a literal size token in their structured
        # name (for example, "ABC Coffee 200ml"). Derive only that exact
        # number/unit substring; do not infer size from unrelated page text.
        for name_value, excerpt, index in _labeled_values(text, "product_name"):
            for match in _PACKAGE_MEASURE_PATTERN.finditer(name_value):
                normalized_measure = _normalized_field_value(match.group(0))
                if normalized_measure is not None:
                    found.append((normalized_measure, excerpt, index))
    return tuple(found)


def build_server_owned_evidence_graph(
    claims: Sequence[AnswerClaim],
    evidence_records: Sequence[EvidenceRecord],
    *,
    selected_spans_by_claim: Mapping[
        str,
        Sequence[ResolvedEvidenceSpan],
    ] | None = None,
    verification_modes_by_claim: Mapping[str, str] | None = None,
    expected_barcode: str | None = None,
    now: datetime | None = None,
) -> EvidenceGraph:
    """Build assertions only from structured fields or exact selected source spans."""
    if not claims or not evidence_records:
        return build_evidence_graph(())
    evaluated_at = now or _utc_now()
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() != timezone.utc.utcoffset(evaluated_at):
        raise InvalidGroundingRequest("verification clock must be UTC")

    barcode_values = {
        claim.value.casefold()
        for claim in claims
        if claim.claim_key.casefold() in _BARCODE_CLAIM_KEYS
    }
    if len(barcode_values) > 1:
        raise InvalidGroundingRequest("one verification request cannot mix barcode identities")
    claim_barcode = next(iter(barcode_values), None)
    if expected_barcode is not None:
        if not isinstance(expected_barcode, str) or not expected_barcode.strip():
            raise InvalidGroundingRequest("expected barcode context is invalid")
        expected_barcode = expected_barcode.strip().casefold()
        if claim_barcode is not None and claim_barcode != expected_barcode:
            raise InvalidGroundingRequest("barcode claim conflicts with its verification context")
    else:
        expected_barcode = claim_barcode

    spans_by_key: dict[str, list[ResolvedEvidenceSpan]] = {}
    source_barcode_values: dict[str, set[str]] = {}
    if selected_spans_by_claim is None:
        for record in evidence_records:
            canonical_url = canonicalize_url(record.document.url)
            if canonical_url is None:
                raise InvalidGroundingRequest("evidence record URL is invalid")
            source_barcode_values.setdefault(canonical_url, set()).update(
                value
                for value, _excerpt, _index in _labeled_values(
                    record.document.text,
                    "barcode_binding",
                )
            )
    else:
        for claim in claims:
            key = claim.claim_key.casefold()
            for resolved in selected_spans_by_claim.get(claim.claim_id, ()):
                span = resolved.span
                record = resolved.evidence_record
                canonical_url = canonicalize_url(record.document.url)
                if canonical_url is None or span.source_url != canonical_url:
                    raise InvalidGroundingRequest("selected evidence span source is invalid")
                spans_by_key.setdefault(key, []).append(resolved)
                source_barcode_values.setdefault(canonical_url, set()).update(
                    value
                    for value, _excerpt, _index in _labeled_values(
                        span.excerpt,
                        "barcode_binding",
                    )
                )

    candidates: dict[
        tuple[str, str, str],
        tuple[EvidenceRecord, str, int, bool],
    ] = {}
    for claim in claims:
        claim_key = claim.claim_key.casefold()
        mode = (verification_modes_by_claim or {}).get(claim.claim_id)
        if selected_spans_by_claim is None:
            sources = tuple(
                (record, record.document.text, 0)
                for record in evidence_records
            )
        elif mode == "EXTRACTIVE_STATEMENT":
            sources = tuple(
                (
                    resolved.evidence_record,
                    resolved.span.excerpt,
                    resolved.span.start_offset,
                )
                for resolved in selected_spans_by_claim.get(claim.claim_id, ())
            )
        else:
            sources = tuple(
                (
                    resolved.evidence_record,
                    resolved.span.excerpt,
                    resolved.span.start_offset,
                )
                for resolved in spans_by_key.get(claim_key, ())
            )

        for record, source_text, source_offset in sources:
            canonical_url = canonicalize_url(record.document.url)
            if canonical_url is None:
                raise InvalidGroundingRequest("evidence record URL is invalid")
            if (
                expected_barcode is not None
                and claim_key not in _BARCODE_CLAIM_KEYS
                and expected_barcode
                not in source_barcode_values.get(canonical_url, set())
            ):
                continue
            if mode == "EXTRACTIVE_STATEMENT":
                normalized_source = _normalize_evidence_span(source_text)
                normalized_value = _normalize_evidence_span(claim.value)
                if not _contains_exact_span(normalized_source, normalized_value):
                    values = ()
                else:
                    values = ((
                        normalized_value,
                        source_text,
                        source_offset,
                    ),)
            elif claim_key == _EXACT_EVIDENCE_CLAIM_KEY:
                values = _exact_evidence_values(source_text, {claim.value})
            else:
                values = _labeled_values(source_text, claim_key)
            for value, excerpt_text, segment_index in values:
                key = (canonical_url, claim_key, value)
                # Identical canonical source bytes are one independent assertion.
                candidates.setdefault(
                    key,
                    (
                        record,
                        excerpt_text,
                        segment_index,
                        mode != "EXTRACTIVE_STATEMENT",
                    ),
                )

    if len(candidates) > MAX_GRAPH_ASSERTIONS:
        raise InvalidGroundingRequest(
            f"verified evidence exceeds the {MAX_GRAPH_ASSERTIONS}-assertion cap"
        )

    assertions: list[EvidenceAssertion] = []
    for rank, (
        (source_url, claim_key, value),
        (record, excerpt_text, segment_index, exclusive),
    ) in enumerate(
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
                exclusive=exclusive,
                evidence=quality,
            )
        )
    return build_evidence_graph(tuple(assertions))


def build_authoritative_verification_packet(
    packet: SynthesisPacket,
    claims: Sequence[AnswerClaim],
    *,
    correction: VerificationCorrection | None = None,
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
        correction=correction,
    )

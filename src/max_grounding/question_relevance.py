"""Deterministic, conservative question-to-evidence sufficiency checks.

Claim citation correctness is not the same as answering the user's question.
This module never treats model-authored source labels as authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from .errors import InvalidGroundingRequest
from .models import ClaimVerificationStatus, SynthesisPacket
from .verification import MAX_REQUIRED_SOURCES

MAX_QUESTION_CHARS = 2048
MAX_QUESTION_KEYS = 16
MAX_SUBJECT_ANCHORS = 8
MAX_ANCHOR_CHARS = 128

_IDENTIFIER = re.compile(r"(?<!\d)(?:\d{14}|\d{13}|\d{12}|\d{8})(?!\d)")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DATE_AFTER = re.compile(r"\b(?:published|updated|released)\s+after\s+(\d{4}-\d{2}-\d{2})\b", re.I)
_FIELD_TERMS = {
    "brand": re.compile(r"\b(?:brand|merek|merk)\b", re.I),
    "product_name": re.compile(r"\b(?:product\s+name|nama\s+produk)\b", re.I),
    "package_size": re.compile(r"\b(?:package\s+size|net\s+(?:weight|content)|isi\s+bersih)\b", re.I),
}
_QUOTE = re.compile(r"\b(?:quote|quoted|quotation|verbatim|kutip|kutipan)\b", re.I)
_LATEST = re.compile(r"\b(?:latest|newest|most\s+recent|terbaru|terkini|current)\b", re.I)
_STANDARD = re.compile(r"\b(?:standards?\s+(?:body|record)|standards?\s+registry|gs1)\b", re.I)
_MANUFACTURER = re.compile(r"\b(?:manufacturer|produsen|pabrikan)\b", re.I)
_OFFICIAL = re.compile(r"\b(?:official|resmi|authoritative|otoritatif)\b", re.I)


@dataclass(frozen=True, slots=True)
class QuestionContract:
    question: str
    requested_claim_keys: tuple[str, ...]
    exact_identifiers: tuple[str, ...]
    subject_anchors: tuple[str, ...]
    required_authority_roles: tuple[str, ...]
    required_sources: int
    quotation_requested: bool
    latest_requested: bool
    published_after: datetime | None


@dataclass(frozen=True, slots=True)
class QuestionAssessment:
    status: str
    reason_codes: tuple[str, ...]
    requested_claim_keys: tuple[str, ...]


def _contains_token(text: str, token: str, *, digit_only: bool = False) -> bool:
    boundary = r"\d" if digit_only else r"\w"
    return re.search(
        rf"(?<!{boundary}){re.escape(token)}(?!{boundary})",
        text,
        re.IGNORECASE,
    ) is not None


def compile_question_contract(
    scope: object, *, required_sources: int = 1
) -> QuestionContract:
    """Validate caller scope against independently observable question constraints."""
    if not isinstance(scope, Mapping) or set(scope) != {
        "question", "requested_claim_keys", "subject_anchors"
    }:
        raise InvalidGroundingRequest("question scope fields are missing or invalid")
    question = scope["question"]
    keys = scope["requested_claim_keys"]
    anchors = scope["subject_anchors"]
    if (
        not isinstance(question, str)
        or not 8 <= len(question.strip()) <= MAX_QUESTION_CHARS
        or any(ord(char) < 32 and char not in "\n\t" for char in question)
    ):
        raise InvalidGroundingRequest("question must be bounded readable text")
    question = question.strip()
    if isinstance(required_sources, bool) or not isinstance(required_sources, int) or not 1 <= required_sources <= MAX_REQUIRED_SOURCES:
        raise InvalidGroundingRequest("required_sources is invalid")
    if (
        not isinstance(keys, (tuple, list))
        or not 1 <= len(keys) <= MAX_QUESTION_KEYS
        or any(not isinstance(key, str) or not _KEY.fullmatch(key.strip().casefold()) for key in keys)
    ):
        raise InvalidGroundingRequest("requested claim keys are invalid")
    normalized_keys = tuple(key.strip().casefold() for key in keys)
    if len(set(normalized_keys)) != len(normalized_keys):
        raise InvalidGroundingRequest("requested claim keys must be unique")
    if (
        not isinstance(anchors, (tuple, list))
        or len(anchors) > MAX_SUBJECT_ANCHORS
        or any(not isinstance(anchor, str) or not 1 <= len(anchor.strip()) <= MAX_ANCHOR_CHARS for anchor in anchors)
    ):
        raise InvalidGroundingRequest("subject anchors are invalid")
    subjects = tuple(anchor.strip() for anchor in anchors)
    if len({x.casefold() for x in subjects}) != len(subjects):
        raise InvalidGroundingRequest("subject anchors must be unique")
    if any(not _contains_token(question, anchor) for anchor in subjects):
        raise InvalidGroundingRequest("subject anchors must be present in the original question")

    identifiers = tuple(dict.fromkeys(match.group() for match in _IDENTIFIER.finditer(question)))
    if not identifiers and not subjects:
        raise InvalidGroundingRequest("question requires a verifiable subject anchor")

    expected_keys = {
        key for key, pattern in _FIELD_TERMS.items() if pattern.search(question)
    }
    if not expected_keys.issubset(normalized_keys):
        raise InvalidGroundingRequest("requested claim keys omit explicit question fields")

    roles: list[str] = []
    if _MANUFACTURER.search(question):
        roles.append("manufacturer")
    if _STANDARD.search(question):
        roles.append("standards_body")
    if not roles and _OFFICIAL.search(question):
        roles.append("official")

    after_match = _DATE_AFTER.search(question)
    published_after = None
    if after_match:
        try:
            published_after = datetime.fromisoformat(after_match.group(1)).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise InvalidGroundingRequest("question has an invalid publication date") from exc

    return QuestionContract(
        question=question,
        requested_claim_keys=normalized_keys,
        exact_identifiers=identifiers,
        subject_anchors=subjects,
        required_authority_roles=tuple(roles),
        required_sources=required_sources,
        quotation_requested=bool(_QUOTE.search(question)),
        latest_requested=bool(_LATEST.search(question)),
        published_after=published_after,
    )


def assess_question_sufficiency(
    contract: QuestionContract,
    packet: SynthesisPacket,
    *,
    trusted_source_roles: Mapping[str, str] | None = None,
    trusted_publication_dates: Mapping[str, datetime] | None = None,
    now: datetime | None = None,
) -> QuestionAssessment:
    """Block answer synthesis unless every requested field and constraint is proven.

    These metadata mappings must be injected by a trusted server operator, never
    forwarded from a caller or inferred from a model's description of a URL.
    """
    if not isinstance(contract, QuestionContract) or not isinstance(packet, SynthesisPacket):
        raise InvalidGroundingRequest("question assessment inputs are invalid")

    reasons: list[str] = []
    observed = {item.claim.claim_key.casefold() for item in packet.verifications}
    required = set(contract.requested_claim_keys)
    if not required.issubset(observed):
        reasons.append("INCOMPLETE_COVERAGE")
    if observed - required:
        reasons.append("UNREQUESTED_CLAIM")
    if (
        "exact_evidence" in observed or "extractive_statement" in observed
    ) and not contract.quotation_requested:
        reasons.append("EXTRACTIVE_NOT_REQUESTED")
    if contract.latest_requested:
        # A recent publication does not prove that no newer authoritative
        # publication exists; this requires a separate exhaustive corpus proof.
        reasons.append("TEMPORAL_NOT_PROVEN")

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise InvalidGroundingRequest("assessment clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    roles = trusted_source_roles or {}
    publications = trusted_publication_dates or {}

    for verification in packet.verifications:
        if verification.status is not ClaimVerificationStatus.SUPPORTED:
            reasons.append("CLAIM_NOT_SUPPORTED")
            continue
        matched: set[str] = set()
        for citation in verification.citations:
            if not citation.source_url or not citation.chunk_id or not citation.text:
                reasons.append("CITATION_MISSING")
                continue
            if (
                contract.exact_identifiers
                and not all(_contains_token(citation.text, i, digit_only=True) for i in contract.exact_identifiers)
            ):
                reasons.append("IDENTIFIER_NOT_BOUND")
                continue
            if contract.subject_anchors and not all(
                _contains_token(citation.text, name) for name in contract.subject_anchors
            ):
                reasons.append("SUBJECT_NOT_BOUND")
                continue
            if contract.required_authority_roles and roles.get(citation.source_url) not in contract.required_authority_roles:
                reasons.append("SOURCE_AUTHORITY_MISSING")
                continue
            if contract.published_after is not None:
                date = publications.get(citation.source_url)
                if not isinstance(date, datetime) or date.tzinfo is None:
                    reasons.append("TEMPORAL_NOT_PROVEN")
                    continue
                date = date.astimezone(timezone.utc)
                if not contract.published_after < date <= now:
                    reasons.append("TEMPORAL_NOT_PROVEN")
                    continue
            matched.add(citation.source_url)
        if len(matched) < contract.required_sources:
            reasons.append("SOURCE_COUNT_INSUFFICIENT")

    if not packet.verifications:
        reasons.append("INCOMPLETE_COVERAGE")
    if reasons:
        return QuestionAssessment(
            status="INSUFFICIENT",
            reason_codes=tuple(dict.fromkeys(reasons)),
            requested_claim_keys=contract.requested_claim_keys,
        )
    return QuestionAssessment(
        status="SUFFICIENT",
        reason_codes=(),
        requested_claim_keys=contract.requested_claim_keys,
    )

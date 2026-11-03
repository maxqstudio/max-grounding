"""Bounded question scope for legacy claim-level regression fixtures.

These tests exercise per-claim support; separate question-sufficiency tests use
the real intended question verbatim. Never reconstitute synthesis output here.
"""

from __future__ import annotations

import copy
import re

from fastapi.testclient import TestClient


def fixture_question_scope(claims: object) -> dict:
    keys: list[str] = []
    anchor = "fixture record"
    if isinstance(claims, (list, tuple)):
        for claim in claims:
            if not isinstance(claim, dict):
                continue
            key = claim.get("claim_key")
            if isinstance(key, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", key):
                normalized = key.strip().casefold()
                if normalized not in keys:
                    keys.append(normalized)
            value = claim.get("value")
            if anchor == "fixture record" and isinstance(value, str):
                words = value.strip().split()
                candidate = " ".join(words[:5])[:128].strip()
                if candidate:
                    anchor = candidate
    return {
        "question": f"Which evidence record concerns {anchor}?",
        "requested_claim_keys": keys or ["exact_evidence"],
        "subject_anchors": [anchor],
    }


def verified_claims(packet: dict) -> list[dict]:
    """Per-claim evidence assertions; never a synthesis-level approval."""
    return [
        item for item in packet["verifications"]
        if item["status"] == "supported"
    ]


class ClaimLevelTestClient(TestClient):
    """Add a valid question for legacy tests that assert claim-level evidence.

    This does NOT change production behavior or bypass the required API scope.
    To test missing scope, use a regular TestClient (not this fixture).
    """

    def post(self, url: str, *args, **kwargs):
        data = kwargs.get("json")
        if (
            url == "/v1/verify"
            and isinstance(data, dict)
            and "question_scope" not in data
        ):
            data = copy.deepcopy(data)
            data["question_scope"] = fixture_question_scope(data.get("claims"))
            kwargs["json"] = data
        return super().post(url, *args, **kwargs)

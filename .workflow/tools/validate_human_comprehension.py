#!/usr/bin/env python3
"""Validate the structural side of the Human Comprehension Gate.

This validator checks whether SYSTEM_OVERVIEW.md covers the required human-first
topics and whether its explicit comprehension checklist is complete.

It cannot prove prose correctness or actual human understanding. Semantic review
against current authority docs remains required.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from project_profile import (
    PROFILE_FILE,
    documentation_settings,
    parse_profile,
)

REQUIRED_HEADINGS = [
    "## One-minute summary",
    "## System at a glance",
    "## Major components",
    "## Main data flow",
    "## Main user workflows",
    "## Lifecycle and state",
    "## Authority model",
    "## Mutable vs immutable",
    "## Failure and recovery",
    "## Current project state",
    "## Proven vs not proven",
    "## Important limitations",
    "## Where to read deeper",
    "## Human comprehension gate",
]

REQUIRED_QUESTIONS = [
    "What is the project and what problem does it solve?",
    "Who uses it and what are the primary outcomes?",
    "What are the major components and how do they relate?",
    "How does important data flow through the system?",
    "What are the main user/domain workflows?",
    "What are the important lifecycle states and transitions?",
    "Who/what is authoritative for important decisions?",
    "What is mutable and what is immutable?",
    "How does failure/recovery behave?",
    "What is the current project state?",
    "What is proven and what is not proven?",
    "What may happen next and what is blocked?",
]

ALLOWED = {"PASS", "FAIL", "NOT_PROVEN", "NOT_APPLICABLE"}


def git_root(start: Path) -> Path:
    try:
        value = subprocess.check_output(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return Path(value)
    except Exception:
        return start.resolve()


def parse_gate_rows(text: str) -> dict[str, str]:
    pos = text.find("## Human comprehension gate")
    if pos < 0:
        return {}
    chunk = text[pos:]
    rows: dict[str, str] = {}
    for line in chunk.splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 2:
            continue
        question, status = cols[0], cols[1]
        if question == "Question" or set(question) <= {"-", ":"}:
            continue
        rows[question] = status
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument(
        "--require-pass",
        action="store_true",
        help="Require semantic gate statuses to be PASS/NOT_APPLICABLE.",
    )
    args = ap.parse_args()

    root = git_root(Path(args.root).resolve())
    profile_path = root / PROFILE_FILE
    failures: list[str] = []
    warnings: list[str] = []

    if not profile_path.is_file():
        print("FAIL MISSING_PROJECT_PROFILE")
        print("RESULT=FAIL")
        return 1

    try:
        documentation = documentation_settings(parse_profile(profile_path))
    except Exception as exc:
        print("FAIL PROJECT_PROFILE_INVALID:" + str(exc))
        print("RESULT=FAIL")
        return 1

    docs_root = root / str(documentation.get("docs_root", "docs"))
    path = docs_root / "SYSTEM_OVERVIEW.md"
    if not path.is_file():
        print("FAIL MISSING_SYSTEM_OVERVIEW")
        print("RESULT=FAIL")
        return 1

    text = path.read_text(encoding="utf-8", errors="ignore")

    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            failures.append("MISSING_OVERVIEW_SECTION:" + heading)

    m = re.search(
        r"^Human comprehension status:\s*(.*?)\s*$",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    overall = m.group(1).strip().upper() if m else ""
    if not overall:
        failures.append("HUMAN_COMPREHENSION_STATUS_MISSING")
    elif overall not in ALLOWED:
        failures.append("HUMAN_COMPREHENSION_STATUS_INVALID:" + overall)

    rows = parse_gate_rows(text)
    for question in REQUIRED_QUESTIONS:
        status = rows.get(question)
        if status is None:
            failures.append("MISSING_HUMAN_GATE_QUESTION:" + question)
        elif status not in ALLOWED:
            failures.append(
                "INVALID_HUMAN_GATE_STATUS:" + question + ":" + status
            )

    if args.require_pass:
        for question in REQUIRED_QUESTIONS:
            status = rows.get(question)
            if status not in {"PASS", "NOT_APPLICABLE"}:
                failures.append(
                    "HUMAN_GATE_QUESTION_NOT_PROVEN:"
                    + question
                    + ":"
                    + str(status)
                )
        if overall != "PASS":
            failures.append(
                "HUMAN_COMPREHENSION_GATE_NOT_PASS:" + (overall or "MISSING")
            )
    elif overall != "PASS":
        warnings.append(
            "HUMAN_COMPREHENSION_SEMANTIC_REVIEW_PENDING:"
            + (overall or "MISSING")
        )

    # Detect obvious empty template sections. This is intentionally conservative.
    for heading in REQUIRED_HEADINGS[:-1]:
        start = text.find(heading)
        if start < 0:
            continue
        tail = text[start + len(heading):]
        next_heading = tail.find("\n## ")
        body = tail[:next_heading] if next_heading >= 0 else tail
        meaningful = [
            line.strip()
            for line in body.splitlines()
            if line.strip()
            and not line.strip().startswith("<!--")
        ]
        if not meaningful:
            failures.append("EMPTY_OVERVIEW_SECTION:" + heading)

    for warning in warnings:
        print("WARN " + warning)
    for failure in failures:
        print("FAIL " + failure)

    if failures:
        print(
            f"RESULT=FAIL failures={len(failures)} warnings={len(warnings)} "
            f"semantic_status={overall or 'MISSING'}"
        )
        return 1

    print(
        f"RESULT=PASS failures=0 warnings={len(warnings)} "
        f"semantic_status={overall}"
    )
    print(
        "SCOPE=Structural coverage only; semantic correctness and actual human "
        "understanding require review against current authority docs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

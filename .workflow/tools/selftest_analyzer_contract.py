#!/usr/bin/env python3
"""Regression coverage for the SW2-06 language-independent analyzer contract."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from analyzer_contract import AnalyzerContractError, AnalyzerResult, validate_result


def run(root: Path, *args: str) -> str:
    proc = subprocess.run(
        list(args),
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("command failed\n" + " ".join(args) + "\n" + proc.stdout)
    return proc.stdout


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def by_id(records: list[dict], analyzer_id: str) -> dict:
    matches = [record for record in records if record.get("analyzer_id") == analyzer_id]
    if len(matches) != 1:
        raise RuntimeError(f"coverage analyzer mismatch:{analyzer_id}:{len(matches)}")
    return matches[0]


def main() -> int:
    skill_root = Path(__file__).resolve().parent.parent
    extractor = skill_root / "scripts" / "extract_project_facts.py"
    generator = skill_root / "scripts" / "generate_sequence_actual.py"

    bad_inventory = AnalyzerResult(
        analyzer_id="bad_inventory",
        languages=(),
        claimed_extensions=(),
        semantic_level="inventory_only",
        proof_status="NOT_PROVEN",
        sequence_nodes=[{"id": "fabricated"}],
    )
    try:
        validate_result(bad_inventory)
    except AnalyzerContractError as exc:
        require(
            "INVENTORY_ONLY_SEMANTIC_EVIDENCE_FORBIDDEN" in str(exc),
            "inventory-only failure was not fail-safe",
        )
    else:
        raise RuntimeError("inventory-only analyzer fabricated semantic evidence")

    bad_dynamic = AnalyzerResult(
        analyzer_id="bad_dynamic",
        languages=("Example",),
        claimed_extensions=(".example",),
        semantic_level="static",
        dynamic_behavior_status="PROVEN",
    )
    try:
        validate_result(bad_dynamic)
    except AnalyzerContractError as exc:
        require(
            "ANALYZER_DYNAMIC_BEHAVIOR_MUST_REMAIN_NOT_PROVEN" in str(exc),
            "dynamic behavior failure was not conservative",
        )
    else:
        raise RuntimeError("dynamic behavior was allowed to become false static proof")

    with tempfile.TemporaryDirectory(prefix="skill-workflow-analyzer-contract-") as td:
        root = Path(td)
        run(root, "git", "init", "--quiet")
        run(root, "git", "config", "user.email", "skill-workflow-selftest@example.invalid")
        run(root, "git", "config", "user.name", "Skill Workflow Selftest")

        (root / "app.py").write_text(
            "class Api:\n"
            "    def get(self, path):\n"
            "        def deco(fn):\n"
            "            return fn\n"
            "        return deco\n\n"
            "api = Api()\n\n"
            "def helper():\n"
            "    return 1\n\n"
            "@api.get('/items')\n"
            "def entry():\n"
            "    helper()\n"
            "    return getattr(api, 'missing', None)\n",
            encoding="utf-8",
        )
        (root / "client.ts").write_text(
            "fetch('/items?from=ts');\n"
            "axios.post('/write', {ok: true});\n",
            encoding="utf-8",
        )
        (root / "worker.rs").write_text(
            "fn main() { println!(\"inventory only\"); }\n",
            encoding="utf-8",
        )
        run(root, "git", "add", ".")
        run(root, "git", "commit", "--quiet", "-m", "analyzer fixture")

        facts_json = root / "facts.json"
        run(
            root,
            sys.executable,
            str(extractor),
            "--root",
            str(root),
            "--output",
            str(facts_json),
        )
        facts = json.loads(facts_json.read_text(encoding="utf-8"))
        require(
            any(item.get("locator") == "app.py::entry" for item in facts["python_symbols"]),
            "python symbol regression",
        )
        require(
            any(
                item.get("caller") == "app.py::entry"
                and item.get("target_token") == "helper"
                for item in facts["python_calls"]
            ),
            "python call-token regression",
        )
        facts_coverage = facts["coverage"]["analyzers"]
        python_facts = by_id(facts_coverage, "python_facts")
        require(
            python_facts.get("proof_status") == "PROVEN_STATIC_WITH_LIMITATIONS",
            "python facts proof status regression",
        )
        facts_fallback = by_id(facts_coverage, "generic_inventory")
        require(facts_fallback.get("proof_status") == "NOT_PROVEN", "facts fallback not fail-safe")
        require("client.ts" in facts_fallback.get("unsupported_files", []), "JS/TS semantic gap hidden")
        require("worker.rs" in facts_fallback.get("unsupported_files", []), "Rust semantic gap hidden")
        require(facts_fallback.get("dynamic_behavior") == "NOT_PROVEN", "facts dynamic proof drift")

        actual_json = root / "actual.json"
        actual_mmd = root / "actual.mmd"
        run(
            root,
            sys.executable,
            str(generator),
            "--root",
            str(root),
            "--output-json",
            str(actual_json),
            "--output-mermaid",
            str(actual_mmd),
        )
        actual = json.loads(actual_json.read_text(encoding="utf-8"))
        require(
            any(
                edge.get("from") == "app.py::entry"
                and edge.get("to") == "app.py::helper"
                for edge in actual["edges"]
            ),
            "python sequence call regression",
        )
        require(
            any(
                edge.get("from") == "client.ts::<module>"
                and edge.get("to") == "HTTP /items"
                and edge.get("resolver") == "js_http_scan"
                for edge in actual["edges"]
            ),
            "JS/TS fetch sequence regression",
        )
        require(
            any(
                edge.get("from") == "client.ts::<module>"
                and edge.get("to") == "HTTP /write"
                for edge in actual["edges"]
            ),
            "JS/TS axios sequence regression",
        )
        sequence_coverage = actual["coverage"]["analyzers"]
        require(
            by_id(sequence_coverage, "python_sequence").get("proof_status")
            == "PROVEN_STATIC_WITH_LIMITATIONS",
            "python sequence analyzer contract regression",
        )
        require(
            by_id(sequence_coverage, "js_ts_http_sequence").get("proof_status")
            == "PROVEN_STATIC_WITH_LIMITATIONS",
            "JS/TS sequence analyzer contract regression",
        )
        sequence_fallback = by_id(sequence_coverage, "generic_inventory")
        require(sequence_fallback.get("proof_status") == "NOT_PROVEN", "sequence fallback not fail-safe")
        require("worker.rs" in sequence_fallback.get("unsupported_files", []), "unsupported language hidden")
        require("client.ts" not in sequence_fallback.get("unsupported_files", []), "supported JS/TS misclassified")
        require(sequence_fallback.get("dynamic_behavior") == "NOT_PROVEN", "sequence dynamic proof drift")
        require(
            not any(str(node.get("id", "")).startswith("worker.rs::") for node in actual["nodes"]),
            "generic fallback fabricated Rust semantic node",
        )

    print("ANALYZER_CONTRACT_INVENTORY_FAIL_SAFE=PASS")
    print("ANALYZER_DYNAMIC_BEHAVIOR_NOT_PROVEN=PASS")
    print("PYTHON_FACTS_REGRESSION=PASS")
    print("PYTHON_SEQUENCE_REGRESSION=PASS")
    print("JS_TS_SEQUENCE_REGRESSION=PASS")
    print("GENERIC_FALLBACK_REGRESSION=PASS")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

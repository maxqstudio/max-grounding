#!/usr/bin/env python3
"""Render a frozen machine-readable sequence plan to Mermaid.

The Mermaid file is generated output and must not be edited manually.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sequence_contract import load_json, render_graph_mermaid, sha256_file


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    output = Path(args.output).resolve()
    plan = load_json(plan_path)

    if str(plan.get("status", "")).upper() != "FROZEN":
        print("FAIL PLAN_NOT_FROZEN")
        return 1

    digest = sha256_file(plan_path)
    body = render_graph_mermaid(plan)
    content = (
        "%% GENERATED FILE - DO NOT EDIT\n"
        f"%% PLAN_SHA256: {digest}\n"
        f"%% FLOW_ID: {plan.get('flow_id', '')}\n"
        + body
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")

    print(f"PLAN={plan_path}")
    print(f"PLAN_SHA256={digest}")
    print(f"OUTPUT={output}")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

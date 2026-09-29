from __future__ import annotations

import json
from pathlib import Path

PRODUCT_MAIN = "1b193080922fc95a7955123e0d4c6c950f83c081"
PR_HEAD = "9ec4814be35641a6f2e643d4216fecd09cf008b0"
PR_RUN = "36632963021"
MAIN_RUN = "36633188935"


def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: str, value) -> None:
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


state = load(".workflow/state.json")
state["status"] = "ACCEPTED_PENDING_CLOSURE"
state["working_branch"] = "work/phase-11-closure"
state["last_accepted_sha"] = PRODUCT_MAIN
state["last_accepted_branch"] = "main"
for proof in (
    "Phase 11 exact final pull-request head " + PR_HEAD + " passed Acceptance run " + PR_RUN + " with 13/13 required jobs.",
    "Phase 11 merged product main SHA " + PRODUCT_MAIN + " passed post-merge Acceptance run " + MAIN_RUN + " with 13/13 required jobs.",
):
    if proof not in state["proven"]:
        state["proven"].append(proof)
state["not_proven"] = [
    "Phase 11 closure is not final until the exact closure pull-request head passes Acceptance, is merged, and closure main is revalidated.",
    "Phase 12 REST/MCP, multi-arch containers, final E2E/security/load, and production acceptance have not started.",
    "The Phase 11 real-service retrieval probe proves the pinned integration path, not general retrieval-quality superiority or calibrated semantic relevance."
]
state["blockers"] = []
state["next_authorized_actions"] = [
    "Open the Phase 11 closure pull request from the validated closure head.",
    "Merge the closure only after exact-head full Acceptance passes.",
    "Revalidate closure main; only then atomically advance state.phase and roadmap.current_phase to PHASE_12_PRODUCTION_API_MCP."
]
state["blocked_actions"] = [
    "Do not advance roadmap.current_phase to Phase 12 before Phase 11 closure main is revalidated.",
    "Do not start Phase 12 implementation before its BEFORE sequence plan is frozen.",
    "Do not claim REST/MCP or production deployment from accepted Phase 11."
]
save(".workflow/state.json", state)

acceptance = load(".workflow/acceptance.json")
for check in (
    "Phase 11 exact final PR head " + PR_HEAD + " passed Acceptance run " + PR_RUN + " with 13/13 required jobs.",
    "Phase 11 merged product main " + PRODUCT_MAIN + " passed post-merge Acceptance run " + MAIN_RUN + " with 13/13 required jobs.",
    "Phase 11 closure exact-head Acceptance and closure-main revalidation remain mandatory before roadmap advancement to Phase 12.",
):
    if check not in acceptance["runtime_checks"]:
        acceptance["runtime_checks"].append(check)
save(".workflow/acceptance.json", acceptance)

changelog = load(".workflow/changelog.json")
marker = "Phase 11 product accepted on main"
if not any(str(item.get("change", "")).startswith(marker) for item in changelog["entries"]):
    changelog["entries"].append({
        "date": "2026-09-30",
        "change": "Phase 11 product accepted on main after exact final PR-head Acceptance and post-merge main revalidation both passed 13/13 under STRICT governance; closure remains the final gate before Phase 12 advancement."
    })
save(".workflow/changelog.json", changelog)

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
start = readme.index("## Current phase")
end = readme.index("## Platform policy")
current = """## Current phase

Phase 11 product behavior is accepted on main at 1b193080922fc95a7955123e0d4c6c950f83c081 after exact-head pull-request Acceptance and post-merge main revalidation both passed 13/13.

Accepted Phase 11 uses pinned Ollama 0.34.0 with qwen3-embedding:0.6b and validated 1024-dimensional embeddings, plus Qdrant 1.19.1 with a versioned named-vector schema, deterministic chunk point IDs, provenance validation, and real persistence evidence across Qdrant restart.

Phase 11 remains the sole CURRENT roadmap phase while its closure is being accepted. Phase 12 REST/MCP, multi-arch containers, final E2E/security/load, and production acceptance do not start until closure main is revalidated.

"""
readme_path.write_text(readme[:start] + current + readme[end:], encoding="utf-8")

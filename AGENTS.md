# MAX Grounding Repository Instructions

## Startup and authority

1. Read this file first.
2. Read `PROJECT_PROFILE.yaml`, `docs/SYSTEM_OVERVIEW.md`, `docs/CURRENT_STATE.md`, and `docs/ROADMAP.md`.
3. Read the relevant `.workflow/*.json` authority and sequence contracts before editing.
4. Treat `.workflow/*.json` as semantic project truth. Uppercase Project Truth documents under `docs/` are generated; update their sources and run `.workflow/tools/sync_project_truth.py` instead of editing generated output directly.

## Scope and role boundaries

- Work only in this repository and the exact Owner-authorized scope. Never access or modify another project or a production system.
- The Owner sets direction and final authority. The Control Room defines scope and acceptance. A Builder implements that scope; Independent QA audits the exact candidate. A role changes only on the Owner's direction. Do not ask another room or agent to change roles.
- Phase 12A is `CURRENT`, `IN_PROGRESS`, and `NOT_ACCEPTED` until its required evidence is complete. Do not merge product PR #29 before Control Room closure review and Owner authorization.
- Preserve unrelated working files, frozen benchmark packages, prior run records, failed attempts, and their hashes. Never rewrite historical evidence to make a current candidate pass.

## Engineering and evidence

- Reconcile the exact repository, branch, and SHA before implementation. Preserve all uncommitted user work.
- Follow the current pinned `Skill_Workflow` and repository `.workflow/` governance. Freeze a BEFORE sequence plan before implementation when the flow requires it; preserve original plans and classify any amendment explicitly.
- Reproduce the defect before repairing it. Add focused positive and negative regression tests. Do not weaken tests, security controls, or acceptance criteria to obtain a pass.
- Treat model output and fetched web content as untrusted proposals/evidence. MAX Grounding owns evidence references, exact excerpts, source provenance, verification status, and citations. If relevance, identity binding, authority, or sufficiency is not established, fail closed.
- Preserve SSRF, redirect, DNS, authentication, payload, persistence, and cross-platform protections. Do not silently replace a frozen benchmark model or alter its denominator.
- Report `PASS`, `FAIL`, `BLOCKED`, `NOT_RUN`, and `NOT_APPLICABLE` accurately, with exact SHA and command evidence. A terminal label or citation-text match alone is not task acceptance.

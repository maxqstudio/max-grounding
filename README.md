# MAX Grounding

MAX Grounding is a self-hostable, evidence-first grounding engine for LLMs and agents.

The project is built in governed phases. Every phase follows:

```text
branch
→ implement
→ GitHub Actions acceptance
→ all required checks PASS
→ merge to main
→ revalidate main
```

No Owner-PC execution is part of the project acceptance authority.

## Target capabilities

- bounded live-web search orchestration;
- secure fetching and extraction of untrusted web content;
- deterministic lexical retrieval over fetched evidence;
- bounded dense semantic retrieval with role-separated embedding contracts;
- hybrid lexical + semantic retrieval;
- reranking and evidence compression;
- freshness and source-authority scoring;
- contradiction handling;
- claim-level citations and verification;
- REST and MCP interfaces;
- Linux, Windows, and macOS core-runtime support;
- Linux container images for `amd64` and `arm64`.

## Current phase

Phase 8 is accepted on main at `af3bd6bb578861de802a972d11e8a2a2c2368955`.

Accepted Phase 8 binds at most 8 evidence excerpts to explicit timezone-aware UTC metadata, computes freshness from a caller-supplied evaluation time and bounded horizon, applies inclusive validity windows, obtains source authority through one injected bounded policy, and ranks deterministically by `authority × freshness × temporal validity`. No universal source-authority hierarchy is hardcoded into the core.

Phase 9 is the next planned phase: contradiction/corroboration handling and an evidence graph. Automatic metadata extraction/verification, claim verification, persistent vector storage, REST, MCP, and production deployment remain later work.

## Platform policy

Native core-runtime acceptance runs on GitHub-hosted Linux, Windows, and macOS runners.
Service-heavy dependencies such as search engines, vector databases, model runtimes, and caches are isolated behind contracts and validated separately before they can become accepted capabilities.

## Governance

Project governance follows `maxqstudio/Skill_Workflow` pinned at:

`9e22feddb8f94e8c0f1af6a33e14b64de5068f8f`

Canonical human-facing governance documentation is generated under `docs/` from structured specs under `.workflow/`.

## Support

- Saweria: https://saweria.co/maxq
- PayPal: https://paypal.me/JacksonJackson1501

## Canonical documentation

- [Roadmap](docs/ROADMAP.md)
- [System overview](docs/SYSTEM_OVERVIEW.md)
- [Current state](docs/CURRENT_STATE.md)
- [Project manifest](docs/PROJECT_MANIFEST.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Acceptance matrix](docs/TEST_ACCEPTANCE_MATRIX.md)

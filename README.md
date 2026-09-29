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

Phase 5 is accepted on main at `f29bb162ea49f24a78091231706e829f286bf8e4`.

Accepted Phase 5 adds a bounded provider-agnostic dense semantic retrieval core with distinct query/document embedding roles, strict vector validation, bounded embedding calls and batches, positive cosine ranking, immutable results, and deterministic provenance tie ordering.

Phase 6 is authorized for planning only until its BEFORE sequence plan is frozen. A concrete embedding model/runtime, Qdrant or other persistent vector database, hybrid fusion, reranking, claim verification, REST, MCP, and production deployment are not yet accepted capabilities.

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

- [System overview](docs/SYSTEM_OVERVIEW.md)
- [Current state](docs/CURRENT_STATE.md)
- [Project manifest](docs/PROJECT_MANIFEST.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Acceptance matrix](docs/TEST_ACCEPTANCE_MATRIX.md)

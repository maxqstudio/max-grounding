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

Phase 11 is accepted on main at `1b193080922fc95a7955123e0d4c6c950f83c081`.

Accepted Phase 11 uses pinned Ollama 0.34.0 with `qwen3-embedding:0.6b` and validates 1024-dimensional embeddings. Qdrant 1.19.1 provides a versioned persistent vector schema with deterministic provenance-bound point identity. GitHub Actions real-service evidence proved indexing/query and persistence across Qdrant restart, while the portable core/adapters pass Python 3.11-3.14 on Linux, Windows, and macOS.

Phase 12 is now the current planning phase: REST, MCP, multi-architecture service containers, end-to-end/security/load validation, and production acceptance. None of those Phase 12 production-service claims are accepted yet.

## Platform policy

Native core-runtime acceptance runs on GitHub-hosted Linux, Windows, and macOS runners.
Service-heavy dependencies such as search engines, vector databases, model runtimes, and caches are isolated behind contracts and validated separately before they can become accepted capabilities.

## Governance

Project governance follows `maxqstudio/Skill_Workflow` pinned at:

`c1d7e58a0fcadc606c8cf75c6283a17278f99259`

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

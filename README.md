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

Phase 11 is a candidate for the concrete self-hosted embedding runtime and persistent semantic index.

The candidate uses pinned Ollama 0.34.0 with qwen3-embedding:0.6b and validates exactly 1024 embedding dimensions. It uses a stdlib Qdrant REST adapter pinned to Qdrant 1.19.1 with a versioned named-vector collection schema, deterministic chunk point IDs, and provenance payload validation.

GitHub Actions real-service evidence proves indexing and semantic query against the pinned services and proves the same expected evidence remains queryable after a Qdrant service restart using the persistent volume. The portable Python core/adapters also pass Python 3.11-3.14 on Linux, Windows, and macOS.

Phase 11 does not expose public REST/MCP endpoints or claim production deployment; those remain Phase 12.

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

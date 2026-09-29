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
- hybrid lexical + semantic retrieval;
- reranking and evidence compression;
- freshness and source-authority scoring;
- contradiction handling;
- claim-level citations and verification;
- REST and MCP interfaces;
- Linux, Windows, and macOS core-runtime support;
- Linux container images for `amd64` and `arm64`.

## Current phase

Phase 3 is a candidate for secure result-page fetching and extraction.

The candidate resolves every result-page hostname at the fetch boundary, rejects the complete DNS answer set if any target is non-public, pins the socket to a validated IP, preserves the original HTTPS server name for TLS, refuses redirects/compression/disallowed media/oversized responses, and extracts visible text while discarding executable/styling HTML content.

The exact candidate still requires full pull-request Acceptance and post-merge main revalidation before Phase 3 is accepted. JavaScript/browser crawling, hybrid retrieval, embeddings, vector databases, reranking, claim verification, REST, and MCP remain later work.

## Platform policy

Native core-runtime acceptance runs on GitHub-hosted Linux, Windows, and macOS runners.
Service-heavy dependencies such as search engines, vector databases, and caches are isolated behind network/service contracts and validated on Linux containers.

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

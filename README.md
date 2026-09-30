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

Production V1 is accepted through Phase 12 on merged main `a858cf2117412f42c2784ca31440f3b459416283`.

The accepted service facade uses SearXNG discovery, secure fetching/extraction, Ollama `qwen3-embedding:0.6b`, Qdrant persistence, deterministic retrieval/reranking/evidence scoring, contradiction handling, claim-level verification, authenticated REST, and authenticated MCP Streamable HTTP.

Permanent acceptance passed on the exact Phase 12 pull-request head and again after merge on Linux, Windows, and macOS with Python 3.11-3.14 service dependencies. Real Linux container E2E, REST/MCP security checks, bounded-load smoke, and linux/amd64 plus linux/arm64 OCI builds are accepted evidence. The bounded load smoke is not a production SLA.

Phase 13-16 remain OPTIONAL and are not started automatically.

## Platform policy

Native core-runtime acceptance runs on GitHub-hosted Linux, Windows, and macOS runners.
Service-heavy dependencies such as search engines, vector databases, model runtimes, and caches are isolated behind contracts and validated separately before they can become accepted capabilities.

## Governance

Project governance follows `maxqstudio/Skill_Workflow` pinned at:

`024e2ea458b25ad9dfb401d3fdeaa994a4cbe1b8`

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

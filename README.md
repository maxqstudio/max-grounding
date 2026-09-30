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

## Current phase\n\nPhase 12 is the Production V1 candidate.\n\nThe candidate exposes a production service facade backed by the accepted SearXNG, secure-fetch, Ollama \`qwen3-embedding:0.6b\`, and Qdrant capabilities. REST provides public \`/healthz\`, authenticated \`/readyz\`, and authenticated bounded \`/v1/search\`, \`/v1/fetch\`, \`/v1/index\`, and \`/v1/query\` operations. MCP Streamable HTTP exposes exactly four structured evidence tools: \`search_web\`, \`fetch_evidence\`, \`index_evidence\`, and \`query_evidence\`.\n\nFinal branch evidence passes all three frozen sequence contracts, the full Python 3.11-3.14 service suite on Linux/Windows/macOS, real Linux container REST/MCP/security E2E, a bounded 16-request concurrency-4 query smoke, and linux/amd64 + linux/arm64 OCI image builds. The load measurements are CI smoke evidence, not a production SLA.\n\nPhase 12 still requires exact-head pull-request Acceptance and post-merge main revalidation before Production V1 is accepted. Optional Phase 13-16 work is not auto-started.\n\n## Platform policy

Native core-runtime acceptance runs on GitHub-hosted Linux, Windows, and macOS runners.
Service-heavy dependencies such as search engines, vector databases, model runtimes, and caches are isolated behind contracts and validated separately before they can become accepted capabilities.

## Governance

Project governance follows `maxqstudio/Skill_Workflow` pinned at:

`440bcc6b750f7338738903b04a8eb7ac59b6630e`

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

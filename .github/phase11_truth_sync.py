from __future__ import annotations

import json
from pathlib import Path

CANDIDATE = "b20322e074755960b29a4504c95e08a35946c6e2"
SKILL = "c1d7e58a0fcadc606c8cf75c6283a17278f99259"
REAL_RUN = "36631528065"
MATRIX_RUN = "36631528126"
BASE_MAIN = "15bff377b920e6cf7e9198af554b8f7dc31f2119"


def load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path: str, value) -> None:
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def add_unique(items: list, value: dict, key: str) -> None:
    if not any(item.get(key) == value.get(key) for item in items):
        items.append(value)


state = load(".workflow/state.json")
state["status"] = "CANDIDATE_PENDING_GITHUB_ACTIONS"
state["working_branch"] = "work/phase-11-concrete-runtime-index"
state["last_accepted_sha"] = BASE_MAIN
state["last_accepted_branch"] = "main"
state["proven"] = [
    "Phase 10 closure main SHA " + BASE_MAIN + " passed Acceptance run 36609977448 with 13/13 required jobs.",
    "Skill Workflow authority is maxqstudio/Skill_Workflow@" + SKILL + " with ROADMAP_SYNC enforced.",
    "Both Phase 11 BEFORE sequence plans were frozen before implementation at ancestor 7aca8f3886f0698260d2f572782ea30a956be83b.",
    "Phase 11 TDD RED run 36611863446 failed before the concrete persistent runtime contract existed; GREEN run 36612347152 passed after implementation.",
    "Phase 11 real-service run " + REAL_RUN + " passed on exact source candidate " + CANDIDATE + " using Ollama 0.34.0, qwen3-embedding:0.6b, validated 1024-dimensional embeddings, and Qdrant 1.19.1.",
    "Real-service integration indexed two evidence documents, ranked the gold-reserve evidence first, restarted Qdrant with the same persistent volume, and ranked the same evidence first after restart.",
    "Phase 11 candidate verification run " + MATRIX_RUN + " passed both frozen PLAN-to-ACTUAL sequence contracts and all 12 Ubuntu/Windows/macOS Python 3.11-3.14 runtime jobs.",
    "The initial Phase 11 sequence mismatch was static symbol ambiguity from type/test fixtures; the frozen plans were preserved and fixtures were repaired without changing runtime behavior."
]
state["not_proven"] = [
    "Final Phase 11 acceptance is not proven until the exact pull-request head passes the full Acceptance workflow and merged main is revalidated.",
    "Phase 11 does not expose public REST or MCP endpoints, multi-arch production containers, production load/security acceptance, browser rendering, multimodal grounding, GraphRAG, or learned-ranking optimization.",
    "The real-service retrieval probe proves one deterministic integration case, not general retrieval-quality superiority or calibrated semantic relevance."
]
state["blockers"] = []
state["next_authorized_actions"] = [
    "Synchronize the two Phase 11 ACTUAL sequence graphs and deterministic Project Truth documentation.",
    "Run full STRICT GitHub Actions pull-request acceptance on the exact Phase 11 candidate head.",
    "Merge Phase 11 only if every required job passes, then revalidate merged main before closure."
]
state["blocked_actions"] = [
    "Do not claim REST/MCP or production deployment from Phase 11.",
    "Do not replace the pinned model/runtime/store schema without a new governed contract and acceptance evidence.",
    "Do not bypass exact-head pull-request Acceptance or post-merge main revalidation."
]
save(".workflow/state.json", state)

project = load(".workflow/project.json")
project["technology"]["persistence"] = ["Qdrant v1.19.1 persistent vector index in Phase 11 candidate"]
for item in ["Ollama v0.34.0", "qwen3-embedding:0.6b", "Qdrant v1.19.1"]:
    if item not in project["technology"]["external_systems"]:
        project["technology"]["external_systems"].append(item)
for entry in [
    {
        "name": "Ollama embedding adapter",
        "path": "src/max_grounding/providers/ollama_embedding.py",
        "purpose": "pinned stdlib HTTP adapter for Ollama 0.34.0 and qwen3-embedding:0.6b with validated 1024-dimensional query/document embeddings",
    },
    {
        "name": "Qdrant vector store",
        "path": "src/max_grounding/providers/qdrant.py",
        "purpose": "pinned stdlib REST adapter for Qdrant 1.19.1 collection validation, provenance-bound upsert, and bounded vector query",
    },
    {
        "name": "Persistent semantic retrieval",
        "path": "src/max_grounding/persistent.py",
        "purpose": "orchestrate deterministic chunking, concrete embedding, persistent indexing, and validated Qdrant semantic retrieval",
    },
]:
    add_unique(project["entry_points"], entry, "path")
save(".workflow/project.json", project)

architecture = load(".workflow/architecture.json")
models = next(item for item in architecture["components"] if item["id"] == "models")
for name in ["PersistentVectorHit", "PersistentIndexResult"]:
    if name not in models["owns"]:
        models["owns"].append(name)
for component in [
    {
        "id": "ollama-embedding-runtime",
        "name": "Concrete Ollama Embedding Runtime",
        "purpose": "Call pinned Ollama 0.34.0 through bounded stdlib HTTP and validate qwen3-embedding:0.6b output as exactly 1024-dimensional finite vectors.",
        "owns": ["OllamaEmbeddingProvider", "runtime version check", "pinned model identity", "query instruction", "bounded /api/embed transport"],
        "depends_on": ["models", "semantic-retrieval"],
    },
    {
        "id": "qdrant-vector-store",
        "name": "Persistent Qdrant Vector Store",
        "purpose": "Create or validate a versioned named-vector schema, persist deterministic chunk provenance, and return bounded validated vector matches.",
        "owns": ["QdrantVectorStore", "Qdrant 1.19.1 runtime check", "named-vector collection schema", "deterministic point identity", "provenance payload validation"],
        "depends_on": ["models", "ollama-embedding-runtime"],
    },
    {
        "id": "persistent-semantic",
        "name": "Persistent Semantic Index and Retrieval",
        "purpose": "Connect accepted deterministic chunks to the concrete embedding runtime and Qdrant store without weakening semantic-hit validation.",
        "owns": ["index_documents", "retrieve_persistent_semantic", "embed_documents_concrete", "embed_query_concrete", "build_semantic_hits"],
        "depends_on": ["models", "semantic-retrieval", "ollama-embedding-runtime", "qdrant-vector-store"],
    },
]:
    add_unique(architecture["components"], component, "id")
for flow in [
    {"from": "FetchedDocument", "to": "Persistent Semantic Index and Retrieval", "meaning": "bounded fetched evidence is deterministically chunked before concrete document embedding"},
    {"from": "Persistent Semantic Index and Retrieval", "to": "Concrete Ollama Embedding Runtime", "meaning": "bounded document batches and one query role are embedded with the pinned model/runtime contract"},
    {"from": "Concrete Ollama Embedding Runtime", "to": "Persistent Qdrant Vector Store", "meaning": "validated 1024-dimensional vectors are stored with immutable chunk provenance"},
    {"from": "Persistent Qdrant Vector Store", "to": "Persistent Semantic Index and Retrieval", "meaning": "bounded query matches are revalidated for point identity, model, dimension, schema, payload provenance, and finite score"},
    {"from": "Persistent Semantic Index and Retrieval", "to": "SemanticHit", "meaning": "positive persistent matches are returned through the existing immutable SemanticHit contract"},
]:
    if flow not in architecture["data_flows"]:
        architecture["data_flows"].append(flow)
for boundary in [
    {
        "name": "Ollama service",
        "contract": "Phase 11 accepts exactly Ollama 0.34.0 with qwen3-embedding:0.6b through bounded non-redirecting stdlib HTTP. Query/document output must be exactly 1024 finite dimensions; model/runtime mismatch fails closed.",
    },
    {
        "name": "Qdrant service",
        "contract": "Phase 11 accepts Qdrant 1.19.1 through bounded stdlib REST. Collection vector name/model/dimension/schema and returned provenance payload are revalidated; persistent-volume restart recovery is proven by real-service integration.",
    },
]:
    add_unique(architecture["external_boundaries"], boundary, "name")
save(".workflow/architecture.json", architecture)

contracts = load(".workflow/contracts.json")
for api in [
    {
        "method": "PYTHON",
        "path": "OllamaEmbeddingProvider.embed_query(text) / embed_documents(texts)",
        "purpose": "Produce pinned concrete query/document embeddings through Ollama 0.34.0 and qwen3-embedding:0.6b.",
        "authority": "src/max_grounding/providers/ollama_embedding.py::OllamaEmbeddingProvider",
        "mutation": "bounded network compute to trusted operator-configured Ollama service; no project persistence",
        "error_behavior": "Runtime-version/model/count/vector/dimension/transport defects fail closed with RuntimeProviderError or EmbeddingProviderError.",
    },
    {
        "method": "PYTHON",
        "path": "QdrantVectorStore.ensure_collection() / upsert_chunks() / query_chunks()",
        "purpose": "Validate/create the pinned Qdrant collection schema, persist provenance-bound chunk vectors, and retrieve bounded vector matches.",
        "authority": "src/max_grounding/providers/qdrant.py::QdrantVectorStore",
        "mutation": "persistent Qdrant collection/point mutation for ensure/upsert; query is read-only",
        "error_behavior": "Version/schema/vector/payload/transport/response defects fail closed with VectorStoreError.",
    },
    {
        "method": "PYTHON",
        "path": "index_documents(documents, provider, store)",
        "purpose": "Deterministically chunk fetched evidence, embed through the concrete provider, and persist compatible vectors with provenance.",
        "authority": "src/max_grounding/persistent.py::index_documents",
        "mutation": "persistent vector upsert through PersistentVectorStore",
        "error_behavior": "Provider/store/schema/vector defects fail closed with PersistentIndexError without claiming partial indexing success.",
    },
    {
        "method": "PYTHON",
        "path": "retrieve_persistent_semantic(query, provider, store)",
        "purpose": "Embed one bounded query, query the persistent compatible vector collection, revalidate provenance, and emit SemanticHit values.",
        "authority": "src/max_grounding/persistent.py::retrieve_persistent_semantic",
        "mutation": "read-only query plus collection compatibility check",
        "error_behavior": "Invalid query/store/provider/match provenance fails closed with PersistentIndexError.",
    },
]:
    add_unique(contracts["api_contracts"], api, "path")
for data in [
    {
        "name": "PersistentVectorHit",
        "source_of_truth": "src/max_grounding/models.py::PersistentVectorHit",
        "mutability": "immutable",
        "legal_writes": ["src/max_grounding/providers/qdrant.py query result construction"],
        "retention": "caller-defined after return; persisted source is Qdrant point payload/vector",
        "invariants": ["point_id deterministically binds chunk identity", "embedding model is qwen3-embedding:0.6b", "embedding dimension is 1024", "schema version is 1", "chunk/source/text/token provenance is validated before construction"],
    },
    {
        "name": "PersistentIndexResult",
        "source_of_truth": "src/max_grounding/models.py::PersistentIndexResult",
        "mutability": "immutable",
        "legal_writes": ["src/max_grounding/persistent.py::index_documents result construction"],
        "retention": "caller-defined after return",
        "invariants": ["reports deterministic chunk/vector counts for the completed call", "retains concrete model/dimension/schema/collection identity", "does not imply transactional rollback beyond Qdrant request semantics"],
    },
    {
        "name": "Qdrant Phase 11 point payload",
        "source_of_truth": "src/max_grounding/providers/qdrant.py",
        "mutability": "idempotently replaceable by deterministic point ID",
        "legal_writes": ["QdrantVectorStore.upsert_chunks"],
        "retention": "persistent Qdrant storage until operator deletion/reset",
        "invariants": ["chunk_id/source_url/chunk_index/text/token_count retained", "embedding_model is qwen3-embedding:0.6b", "embedding_dimension is 1024", "schema_version is 1", "point ID is deterministically derived from chunk identity"],
    },
]:
    add_unique(contracts["data_contracts"], data, "name")
save(".workflow/contracts.json", contracts)

claims = load(".workflow/claims.json")
for claim in [
    {
        "id": "TRUTH-P11-OLLAMA-001",
        "claim": "Phase 11 provides a concrete stdlib Ollama embedding adapter pinned to runtime 0.34.0 and qwen3-embedding:0.6b; real-service acceptance proves validated 1024-dimensional embeddings.",
        "documents": ["PROJECT_TRUTH_SYNC.md"],
        "source_owners": ["src/max_grounding/providers/ollama_embedding.py::OllamaEmbeddingProvider"],
        "tests": ["tests/test_ollama_embedding.py", "integration/phase11_services.py"],
        "runtime_evidence": ["GitHub Actions Phase 11 Real Services run " + REAL_RUN],
        "status": "PASS",
    },
    {
        "id": "TRUTH-P11-QDRANT-001",
        "claim": "Phase 11 provides a Qdrant 1.19.1 REST store with pinned named-vector schema, deterministic point identity, provenance payload validation, and persistence proven across a real service restart.",
        "documents": ["PROJECT_TRUTH_SYNC.md"],
        "source_owners": ["src/max_grounding/providers/qdrant.py::QdrantVectorStore"],
        "tests": ["tests/test_qdrant_store.py", "integration/phase11_services.py"],
        "runtime_evidence": ["GitHub Actions Phase 11 Real Services run " + REAL_RUN],
        "status": "PASS",
    },
    {
        "id": "TRUTH-P11-PERSISTENT-001",
        "claim": "Phase 11 indexes deterministic fetched-document chunks into the concrete vector store and reconstructs positive validated persistent matches as the existing SemanticHit contract without bypassing provenance checks.",
        "documents": ["PROJECT_TRUTH_SYNC.md"],
        "source_owners": ["src/max_grounding/persistent.py::index_documents", "src/max_grounding/persistent.py::retrieve_persistent_semantic"],
        "tests": ["tests/test_persistent_semantic.py", "integration/phase11_services.py"],
        "runtime_evidence": ["GitHub Actions Phase 11 Real Services run " + REAL_RUN, "GitHub Actions Candidate Verify run " + MATRIX_RUN],
        "status": "PASS",
    },
    {
        "id": "TRUTH-P11-CROSS-OS-001",
        "claim": "Phase 11 portable Python core and stdlib adapters pass the complete suite on Python 3.11-3.14 across Ubuntu, Windows, and macOS; service-heavy Ollama/Qdrant runtime acceptance is isolated to a real Ubuntu container lane.",
        "documents": ["PROJECT_TRUTH_SYNC.md"],
        "source_owners": ["src/max_grounding/persistent.py", ".github/workflows/ci.yml"],
        "tests": ["tests/test_ollama_embedding.py", "tests/test_qdrant_store.py", "tests/test_persistent_semantic.py"],
        "runtime_evidence": ["GitHub Actions Candidate Verify run " + MATRIX_RUN, "GitHub Actions Phase 11 Real Services run " + REAL_RUN],
        "status": "PASS",
    },
]:
    add_unique(claims["claims"], claim, "id")
save(".workflow/claims.json", claims)

acceptance = load(".workflow/acceptance.json")
acceptance["evidence_boundary"] = (
    "Phase 11 candidate proves a concrete self-hosted embedding/runtime and persistent semantic index boundary: "
    "Ollama 0.34.0 + qwen3-embedding:0.6b produces validated 1024-dimensional vectors, Qdrant 1.19.1 persists "
    "provenance-bound vectors, and retrieval remains valid after a real Qdrant restart. The portable stdlib core "
    "passes Linux/Windows/macOS. This does not prove REST/MCP public service exposure, production container/load/security "
    "acceptance, general retrieval-quality superiority, browser rendering, multimodal grounding, GraphRAG, or learned ranking."
)
acceptance["requirements"] = [
    {"id": "P11-PLAN", "requirement": "Both Phase 11 BEFORE sequence plans are frozen before implementation and generated ACTUAL flows conform to them.", "evidence": "Frozen ancestor 7aca8f3886f0698260d2f572782ea30a956be83b; Candidate Verify run " + MATRIX_RUN, "status": "PASS"},
    {"id": "P11-OLLAMA", "requirement": "Pinned Ollama 0.34.0 with qwen3-embedding:0.6b returns validated 1024-dimensional embeddings through the concrete stdlib adapter.", "evidence": "tests/test_ollama_embedding.py; Real Services run " + REAL_RUN, "status": "PASS"},
    {"id": "P11-QDRANT", "requirement": "Pinned Qdrant 1.19.1 accepts the versioned named-vector schema and provenance-bound upsert/query contract.", "evidence": "tests/test_qdrant_store.py; Real Services run " + REAL_RUN, "status": "PASS"},
    {"id": "P11-PERSISTENCE", "requirement": "A real persistent Qdrant volume retains indexed evidence across service restart and returns the expected top evidence after restart.", "evidence": "integration/phase11_services.py; Real Services run " + REAL_RUN, "status": "PASS"},
    {"id": "P11-SEMANTIC", "requirement": "Persistent semantic orchestration preserves bounded concrete embedding roles, deterministic chunk provenance, store compatibility, and accepted SemanticHit reconstruction.", "evidence": "tests/test_persistent_semantic.py; Candidate Verify run " + MATRIX_RUN + "; Real Services run " + REAL_RUN, "status": "PASS"},
    {"id": "P11-CROSS-OS", "requirement": "Portable Phase 11 core/adapters pass Python 3.11-3.14 on Linux, Windows, and macOS.", "evidence": "Candidate Verify run " + MATRIX_RUN + ": sequence plus 12/12 runtime matrix jobs PASS", "status": "PASS"},
]
acceptance["runtime_checks"] = [
    "TDD RED run 36611863446 proved the Phase 11 concrete runtime/index contract absent before implementation.",
    "GREEN run 36612347152 passed full unit and compile regression.",
    "Real Services run " + REAL_RUN + " passed exact Ollama/Qwen3/Qdrant version, embedding dimension, index/query, and restart-persistence assertions on candidate " + CANDIDATE + ".",
    "Candidate Verify run " + MATRIX_RUN + " passed both frozen sequence contracts and all 12 Linux/Windows/macOS Python 3.11-3.14 jobs.",
    "Final pull-request Acceptance and post-merge main revalidation remain mandatory before Phase 11 closure.",
]
acceptance["runtime_status"] = "PASS"
acceptance["sequence_mode"] = "BEFORE"
acceptance["sequence_session"] = "docs/sequence/sessions/phase-11-persistent-index.json"
acceptance["sequence_sync_status"] = "PASS"
save(".workflow/acceptance.json", acceptance)

decisions = load(".workflow/decisions.json")
add_unique(
    decisions["decisions"],
    {
        "id": "DEC-0007",
        "title": "Use pinned Ollama and Qdrant service boundaries for Phase 11",
        "status": "ACCEPTED",
        "date": "2026-09-30",
        "decision": "Use stdlib HTTP adapters around Ollama 0.34.0 with qwen3-embedding:0.6b (1024 dimensions) and Qdrant 1.19.1 rather than adding heavy ML/vector Python dependencies to the cross-platform core.",
        "rationale": "This provides a concrete self-hosted model and persistent vector runtime while keeping the Python core portable across Linux, Windows, and macOS and making service runtime compatibility independently testable.",
    },
    "id",
)
save(".workflow/decisions.json", decisions)

changelog = load(".workflow/changelog.json")
marker = "Phase 11 candidate adds"
if not any(str(item.get("change", "")).startswith(marker) for item in changelog["entries"]):
    changelog["entries"].append(
        {
            "date": "2026-09-30",
            "change": "Phase 11 candidate adds pinned Ollama 0.34.0 + qwen3-embedding:0.6b concrete embeddings, Qdrant 1.19.1 persistent vector storage, provenance-preserving persistent semantic indexing/query, real restart-persistence evidence, frozen-plan conformance, and full cross-platform core evidence.",
        }
    )
save(".workflow/changelog.json", changelog)

readme_path = Path("README.md")
readme = readme_path.read_text(encoding="utf-8")
start = readme.index("## Current phase")
end = readme.index("## Platform policy")
current = """## Current phase

Phase 11 is a candidate for the concrete self-hosted embedding runtime and persistent semantic index.

The candidate uses pinned Ollama 0.34.0 with qwen3-embedding:0.6b and validates exactly 1024 embedding dimensions. It uses a stdlib Qdrant REST adapter pinned to Qdrant 1.19.1 with a versioned named-vector collection schema, deterministic chunk point IDs, and provenance payload validation.

GitHub Actions real-service evidence proves indexing and semantic query against the pinned services and proves the same expected evidence remains queryable after a Qdrant service restart using the persistent volume. The portable Python core/adapters also pass Python 3.11-3.14 on Linux, Windows, and macOS.

Phase 11 does not expose public REST/MCP endpoints or claim production deployment; those remain Phase 12.

"""
readme = readme[:start] + current + readme[end:]
tick = chr(96)
readme = readme.replace(
    tick + "9e22feddb8f94e8c0f1af6a33e14b64de5068f8f" + tick,
    tick + "c1d7e58a0fcadc606c8cf75c6283a17278f99259" + tick,
)
readme_path.write_text(readme, encoding="utf-8")

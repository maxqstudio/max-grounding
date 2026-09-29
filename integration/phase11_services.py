from __future__ import annotations

import argparse
import json
import os

from max_grounding.models import FetchedDocument
from max_grounding.persistent import index_documents, retrieve_persistent_semantic
from max_grounding.providers.ollama_embedding import (
    OLLAMA_EMBEDDING_DIMENSION,
    OLLAMA_MODEL,
    OLLAMA_VERSION,
    OllamaEmbeddingProvider,
)
from max_grounding.providers.qdrant import (
    DEFAULT_COLLECTION,
    QDRANT_VERSION,
    QdrantVectorStore,
)


def document(url: str, text: str) -> FetchedDocument:
    return FetchedDocument(
        url=url,
        fetched_from_ip="93.184.216.34",
        media_type="text/plain",
        charset="utf-8",
        byte_length=len(text.encode("utf-8")),
        text=text,
    )


def services():
    ollama = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
    qdrant = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
    provider = OllamaEmbeddingProvider(ollama, timeout_seconds=120)
    store = QdrantVectorStore(qdrant, timeout_seconds=30)
    provider.verify_runtime()
    store.verify_runtime()
    return provider, store


def run_index() -> None:
    provider, store = services()
    probe = provider.embed_query("central bank gold reserves")
    if len(probe) != OLLAMA_EMBEDDING_DIMENSION:
        raise AssertionError("real Ollama embedding dimension mismatch")

    docs = (
        document(
            "https://integration.example/gold",
            (
                "Central banks increased gold reserves as a defensive reserve asset. "
                "Official monetary authorities discussed gold allocation and inflation risk."
            ),
        ),
        document(
            "https://integration.example/sport",
            (
                "The football club trained for the evening match. "
                "Players discussed tactics, substitutions, and stadium conditions."
            ),
        ),
    )
    result = index_documents(
        docs,
        provider,
        store,
        max_words=32,
        overlap_words=4,
        max_chunks_per_document=4,
        batch_size=4,
    )
    if result.chunks_indexed < 2:
        raise AssertionError("real persistent index did not write enough chunks")

    hits = retrieve_persistent_semantic(
        "central bank gold reserve policy",
        provider,
        store,
        limit=2,
    )
    if not hits:
        raise AssertionError("real persistent query returned no hits")
    if hits[0].chunk.source_url != "https://integration.example/gold":
        raise AssertionError(
            "real semantic query did not rank the gold evidence first"
        )

    print(
        json.dumps(
            {
                "mode": "index",
                "ollama_version": OLLAMA_VERSION,
                "model": OLLAMA_MODEL,
                "embedding_dimension": OLLAMA_EMBEDDING_DIMENSION,
                "qdrant_version": QDRANT_VERSION,
                "collection": DEFAULT_COLLECTION,
                "chunks_indexed": result.chunks_indexed,
                "top_url": hits[0].chunk.source_url,
                "top_score": hits[0].score,
            },
            sort_keys=True,
        )
    )


def run_query() -> None:
    provider, store = services()
    hits = retrieve_persistent_semantic(
        "central bank gold reserve policy",
        provider,
        store,
        limit=2,
    )
    if not hits:
        raise AssertionError("persistent query after restart returned no hits")
    if hits[0].chunk.source_url != "https://integration.example/gold":
        raise AssertionError(
            "persistent query after restart lost expected evidence ordering"
        )
    print(
        json.dumps(
            {
                "mode": "query_after_qdrant_restart",
                "top_url": hits[0].chunk.source_url,
                "top_score": hits[0].score,
            },
            sort_keys=True,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("index", "query"), required=True)
    args = parser.parse_args()
    if args.mode == "index":
        run_index()
    else:
        run_query()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

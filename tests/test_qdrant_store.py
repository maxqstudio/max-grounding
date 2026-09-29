from __future__ import annotations

import math
import unittest
import uuid
from unittest.mock import patch

from max_grounding.errors import VectorStoreError
from max_grounding.models import TextChunk
from max_grounding.providers.ollama_embedding import (
    OLLAMA_EMBEDDING_DIMENSION,
    OLLAMA_MODEL,
)
from max_grounding.providers.qdrant import (
    DEFAULT_COLLECTION,
    QDRANT_SCHEMA_VERSION,
    QDRANT_VECTOR_NAME,
    QDRANT_VERSION,
    QdrantVectorStore,
    point_id_for_chunk,
)


def chunk(chunk_id: str = "a" * 64) -> TextChunk:
    return TextChunk(
        chunk_id=chunk_id,
        source_url="https://example.com/a",
        chunk_index=0,
        text="central bank gold evidence",
        token_count=4,
    )


def vector() -> tuple[float, ...]:
    return (1.0,) + (0.0,) * (OLLAMA_EMBEDDING_DIMENSION - 1)


def collection_payload(*, size: int = OLLAMA_EMBEDDING_DIMENSION, model: str = OLLAMA_MODEL):
    return {
        "status": "ok",
        "result": {
            "config": {
                "params": {
                    "vectors": {
                        QDRANT_VECTOR_NAME: {
                            "size": size,
                            "distance": "Cosine",
                        }
                    }
                },
                "metadata": {
                    "max_grounding": {
                        "schema_version": QDRANT_SCHEMA_VERSION,
                        "embedding_model": model,
                        "embedding_dimension": size,
                    }
                },
            }
        },
    }


class QdrantVectorStoreTests(unittest.TestCase):
    def test_pinned_runtime_and_schema_constants(self) -> None:
        self.assertEqual(QDRANT_VERSION, "1.19.1")
        self.assertEqual(QDRANT_SCHEMA_VERSION, 1)
        self.assertEqual(QDRANT_VECTOR_NAME, "qwen3_embedding_0_6b_v1")
        self.assertEqual(DEFAULT_COLLECTION, "max_grounding_qwen3_embedding_0_6b_v1")

    def test_point_id_is_deterministic_uuid_from_full_chunk_identity(self) -> None:
        first = point_id_for_chunk("chunk-identity")
        second = point_id_for_chunk("chunk-identity")
        other = point_id_for_chunk("other-identity")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        uuid.UUID(first)

    def test_ensure_collection_creates_named_vector_with_metadata_when_missing(self) -> None:
        store = QdrantVectorStore("http://127.0.0.1:6333")
        with patch(
            "max_grounding.providers.qdrant.request_qdrant_json",
            side_effect=[
                (200, {"version": QDRANT_VERSION}),
                (404, {}),
                (200, {"status": "ok", "result": True}),
            ],
        ) as request:
            store.ensure_collection()

        self.assertEqual(request.call_count, 3)
        create_call = request.call_args_list[2]
        body = create_call.kwargs["payload"]
        self.assertEqual(
            body["vectors"][QDRANT_VECTOR_NAME],
            {"size": OLLAMA_EMBEDDING_DIMENSION, "distance": "Cosine"},
        )
        self.assertEqual(
            body["metadata"]["max_grounding"]["embedding_model"],
            OLLAMA_MODEL,
        )
        self.assertEqual(
            body["metadata"]["max_grounding"]["schema_version"],
            QDRANT_SCHEMA_VERSION,
        )

    def test_existing_collection_must_match_vector_and_model_schema(self) -> None:
        invalid = [
            collection_payload(size=512),
            collection_payload(model="different-model"),
            {
                "status": "ok",
                "result": {
                    "config": {
                        "params": {"vectors": {}},
                        "metadata": {},
                    }
                },
            },
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                store = QdrantVectorStore("http://localhost:6333")
                with patch(
                    "max_grounding.providers.qdrant.request_qdrant_json",
                    side_effect=[
                        (200, {"version": QDRANT_VERSION}),
                        (200, payload),
                    ],
                ):
                    with self.assertRaises(VectorStoreError):
                        store.ensure_collection()

    def test_upsert_uses_named_vector_and_complete_provenance_payload(self) -> None:
        store = QdrantVectorStore("http://localhost:6333")
        store._runtime_verified = True
        store._collection_verified = True
        item = chunk()

        with patch(
            "max_grounding.providers.qdrant.request_qdrant_json",
            return_value=(
                200,
                {"status": "ok", "result": {"status": "completed"}},
            ),
        ) as request:
            store.upsert_chunks((item,), (vector(),))

        body = request.call_args.kwargs["payload"]
        point = body["points"][0]
        self.assertEqual(point["id"], point_id_for_chunk(item.chunk_id))
        self.assertEqual(point["vector"][QDRANT_VECTOR_NAME], list(vector()))
        self.assertEqual(point["payload"]["chunk_id"], item.chunk_id)
        self.assertEqual(point["payload"]["source_url"], item.source_url)
        self.assertEqual(point["payload"]["chunk_index"], item.chunk_index)
        self.assertEqual(point["payload"]["text"], item.text)
        self.assertEqual(point["payload"]["token_count"], item.token_count)
        self.assertEqual(point["payload"]["embedding_model"], OLLAMA_MODEL)
        self.assertEqual(
            point["payload"]["embedding_dimension"],
            OLLAMA_EMBEDDING_DIMENSION,
        )
        self.assertEqual(
            point["payload"]["schema_version"],
            QDRANT_SCHEMA_VERSION,
        )

    def test_query_validates_point_identity_and_reconstructs_provenance(self) -> None:
        store = QdrantVectorStore("http://localhost:6333")
        store._runtime_verified = True
        store._collection_verified = True
        item = chunk()
        point_id = point_id_for_chunk(item.chunk_id)
        response = {
            "status": "ok",
            "result": {
                "points": [
                    {
                        "id": point_id,
                        "score": 0.91,
                        "payload": {
                            "chunk_id": item.chunk_id,
                            "source_url": item.source_url,
                            "chunk_index": item.chunk_index,
                            "text": item.text,
                            "token_count": item.token_count,
                            "embedding_model": OLLAMA_MODEL,
                            "embedding_dimension": OLLAMA_EMBEDDING_DIMENSION,
                            "schema_version": QDRANT_SCHEMA_VERSION,
                        },
                    }
                ]
            },
        }
        with patch(
            "max_grounding.providers.qdrant.request_qdrant_json",
            return_value=(200, response),
        ) as request:
            hits = store.query_chunks(vector(), limit=3)

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].point_id, point_id)
        self.assertEqual(hits[0].chunk, item)
        self.assertAlmostEqual(hits[0].score, 0.91)
        body = request.call_args.kwargs["payload"]
        self.assertEqual(body["using"], QDRANT_VECTOR_NAME)
        self.assertEqual(body["limit"], 3)
        self.assertTrue(body["with_payload"])

    def test_query_rejects_tampered_identity_model_schema_and_nonfinite_score(self) -> None:
        item = chunk()
        good_payload = {
            "chunk_id": item.chunk_id,
            "source_url": item.source_url,
            "chunk_index": item.chunk_index,
            "text": item.text,
            "token_count": item.token_count,
            "embedding_model": OLLAMA_MODEL,
            "embedding_dimension": OLLAMA_EMBEDDING_DIMENSION,
            "schema_version": QDRANT_SCHEMA_VERSION,
        }
        bad_points = [
            {
                "id": str(uuid.uuid4()),
                "score": 0.9,
                "payload": good_payload,
            },
            {
                "id": point_id_for_chunk(item.chunk_id),
                "score": 0.9,
                "payload": {**good_payload, "embedding_model": "other"},
            },
            {
                "id": point_id_for_chunk(item.chunk_id),
                "score": 0.9,
                "payload": {**good_payload, "schema_version": 2},
            },
            {
                "id": point_id_for_chunk(item.chunk_id),
                "score": float("nan"),
                "payload": good_payload,
            },
        ]
        for point in bad_points:
            with self.subTest(point=point):
                store = QdrantVectorStore("http://localhost:6333")
                store._runtime_verified = True
                store._collection_verified = True
                with patch(
                    "max_grounding.providers.qdrant.request_qdrant_json",
                    return_value=(
                        200,
                        {"status": "ok", "result": {"points": [point]}},
                    ),
                ):
                    with self.assertRaises(VectorStoreError):
                        store.query_chunks(vector(), limit=1)

    def test_invalid_base_collection_vector_and_limits_fail_closed(self) -> None:
        for url in (
            "ftp://localhost:6333",
            "http://user:pass@localhost:6333",
            "http://localhost:6333/?x=1",
        ):
            with self.subTest(url=url):
                with self.assertRaises(VectorStoreError):
                    QdrantVectorStore(url)

        with self.assertRaises(VectorStoreError):
            QdrantVectorStore("http://localhost:6333", collection="../bad")

        store = QdrantVectorStore("http://localhost:6333")
        store._runtime_verified = True
        store._collection_verified = True
        with self.assertRaises(VectorStoreError):
            store.query_chunks((1.0, 2.0), limit=1)
        with self.assertRaises(VectorStoreError):
            store.query_chunks(vector(), limit=21)
        with self.assertRaises(VectorStoreError):
            store.upsert_chunks((chunk(),), (tuple(float("nan") for _ in range(1024)),))


if __name__ == "__main__":
    unittest.main()

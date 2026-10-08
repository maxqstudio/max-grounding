from __future__ import annotations

import unittest
from unittest.mock import patch

from max_grounding.errors import EmbeddingProviderError, RuntimeProviderError
from max_grounding.providers.ollama_embedding import (
    OLLAMA_EMBEDDING_DIMENSION,
    OLLAMA_MODEL,
    OLLAMA_VERSION,
    QUERY_INSTRUCTION,
    OllamaEmbeddingProvider,
)


def vector(value: float = 0.0) -> list[float]:
    data = [value] * OLLAMA_EMBEDDING_DIMENSION
    data[0] = 1.0
    return data


class OllamaEmbeddingProviderTests(unittest.TestCase):
    def test_pinned_runtime_and_model_constants(self) -> None:
        self.assertEqual(OLLAMA_VERSION, "0.35.1")
        self.assertEqual(OLLAMA_MODEL, "qwen3-embedding:0.6b")
        self.assertEqual(OLLAMA_EMBEDDING_DIMENSION, 1024)

    def test_current_local_ollama_runtime_is_explicitly_supported(self) -> None:
        provider = OllamaEmbeddingProvider("http://127.0.0.1:11434")
        with patch(
            "max_grounding.providers.ollama_embedding.request_ollama_json",
            side_effect=[
                {"version": "0.40.0"},
                {"model": OLLAMA_MODEL, "embeddings": [vector()]},
            ],
        ):
            actual = provider.embed_query("product barcode evidence")
        self.assertEqual(len(actual), 1024)
        self.assertEqual(provider.detected_runtime_version, "0.40.0")
        self.assertEqual(provider.runtime_version, "0.40.0")

    def test_unverified_future_ollama_runtime_is_rejected(self) -> None:
        provider = OllamaEmbeddingProvider("http://127.0.0.1:11434")
        with patch(
            "max_grounding.providers.ollama_embedding.request_ollama_json",
            return_value={"version": "0.41.0"},
        ):
            with self.assertRaises(RuntimeProviderError):
                provider.embed_query("product barcode evidence")

    def test_query_uses_fixed_instruction_and_exact_dimension(self) -> None:
        provider = OllamaEmbeddingProvider("http://127.0.0.1:11434")
        with patch(
            "max_grounding.providers.ollama_embedding.request_ollama_json",
            side_effect=[
                {"version": OLLAMA_VERSION},
                {"model": OLLAMA_MODEL, "embeddings": [vector()]},
            ],
        ) as request:
            result = provider.embed_query("gold central bank")

        self.assertEqual(len(result), OLLAMA_EMBEDDING_DIMENSION)
        self.assertEqual(request.call_count, 2)
        version_call = request.call_args_list[0]
        embed_call = request.call_args_list[1]
        self.assertEqual(version_call.args[1:3], ("GET", "/api/version"))
        payload = embed_call.kwargs["payload"]
        self.assertEqual(payload["model"], OLLAMA_MODEL)
        self.assertEqual(payload["dimensions"], OLLAMA_EMBEDDING_DIMENSION)
        self.assertFalse(payload["truncate"])
        self.assertEqual(payload["input"], QUERY_INSTRUCTION + "gold central bank")

    def test_document_batch_preserves_raw_document_texts(self) -> None:
        provider = OllamaEmbeddingProvider("http://127.0.0.1:11434")
        provider._runtime_verified = True
        texts = ("alpha evidence", "beta evidence")
        with patch(
            "max_grounding.providers.ollama_embedding.request_ollama_json",
            return_value={
                "model": OLLAMA_MODEL,
                "embeddings": [vector(0.1), vector(0.2)],
            },
        ) as request:
            result = provider.embed_documents(texts)

        self.assertEqual(len(result), 2)
        self.assertEqual(request.call_args.kwargs["payload"]["input"], list(texts))

    def test_runtime_version_model_count_and_dimension_fail_closed(self) -> None:
        cases = [
            [{"version": "0.33.3"}],
            [
                {"version": OLLAMA_VERSION},
                {"model": "other-model", "embeddings": [vector()]},
            ],
            [
                {"version": OLLAMA_VERSION},
                {"model": OLLAMA_MODEL, "embeddings": []},
            ],
            [
                {"version": OLLAMA_VERSION},
                {"model": OLLAMA_MODEL, "embeddings": [[1.0, 2.0]]},
            ],
        ]
        for responses in cases:
            with self.subTest(responses=responses):
                provider = OllamaEmbeddingProvider("http://127.0.0.1:11434")
                with patch(
                    "max_grounding.providers.ollama_embedding.request_ollama_json",
                    side_effect=responses,
                ):
                    with self.assertRaises((RuntimeProviderError, EmbeddingProviderError)):
                        provider.embed_query("query")

    def test_invalid_base_url_and_batch_bounds_fail_closed(self) -> None:
        for url in (
            "ftp://localhost:11434",
            "http://user:pass@localhost:11434",
            "http://localhost:11434/?x=1",
            "http://localhost:11434/#fragment",
        ):
            with self.subTest(url=url):
                with self.assertRaises(RuntimeProviderError):
                    OllamaEmbeddingProvider(url)

        provider = OllamaEmbeddingProvider("http://localhost:11434")
        provider._runtime_verified = True
        with self.assertRaises(EmbeddingProviderError):
            provider.embed_documents(tuple("x" for _ in range(65)))

    def test_empty_document_batch_returns_empty_without_transport(self) -> None:
        provider = OllamaEmbeddingProvider("http://localhost:11434")
        with patch(
            "max_grounding.providers.ollama_embedding.request_ollama_json"
        ) as request:
            self.assertEqual(provider.embed_documents(()), ())
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()

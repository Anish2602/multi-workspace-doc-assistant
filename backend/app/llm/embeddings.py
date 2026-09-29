"""Gemini embeddings (free tier), 768-d, with document/query task types."""

from typing import Protocol

from app.config import get_settings
from app.llm.http import ProviderError, post_json
from app.models import EMBEDDING_DIM

_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_BATCH = 100  # batchEmbedContents limit


class Embedder(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    async def embed_query(self, text: str) -> list[float]: ...


class GeminiEmbedder:
    def __init__(self) -> None:
        settings = get_settings()
        if settings.gemini_api_key is None:
            raise ProviderError("gemini", "GEMINI_API_KEY is not configured")
        self._headers = {"x-goog-api-key": settings.gemini_api_key.get_secret_value()}
        self._model = settings.embedding_model
        self._timeout = settings.llm_timeout_seconds

    async def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _BATCH):
            batch = texts[start : start + _BATCH]
            body = {
                "requests": [
                    {
                        "model": f"models/{self._model}",
                        "content": {"parts": [{"text": text}]},
                        "taskType": task_type,
                        "outputDimensionality": EMBEDDING_DIM,
                    }
                    for text in batch
                ]
            }
            data = await post_json(
                "gemini",
                f"{_BASE}/{self._model}:batchEmbedContents",
                headers=self._headers,
                json=body,
                request_timeout=self._timeout,
            )
            embeddings = data.get("embeddings", [])
            if len(embeddings) != len(batch):
                raise ProviderError("gemini", "embedding count mismatch")
            vectors += [e["values"] for e in embeddings]
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        [vector] = await self._embed([text], "RETRIEVAL_QUERY")
        return vector


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """FastAPI dependency; tests override it with a deterministic fake."""
    global _embedder
    if _embedder is None:
        _embedder = GeminiEmbedder()
    return _embedder

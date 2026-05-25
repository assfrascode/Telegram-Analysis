from __future__ import annotations

import hashlib
from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()


def _mock_embedding(text: str, dimensions: int) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < dimensions:
        for byte in digest:
            values.append((byte / 127.5) - 1.0)
            if len(values) >= dimensions:
                break
        digest = hashlib.sha256(digest).digest()
    return values


class EmbeddingClient:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        if settings.llm_mock_enabled:
            return [_mock_embedding(text, settings.mock_embedding_dimensions) for text in texts]

        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(
                f"{settings.vllm_embedding_base_url.rstrip('/')}/embeddings",
                headers={"Authorization": f"Bearer {settings.vllm_api_key}"},
                json={"model": settings.embedding_model, "input": texts},
            )
            response.raise_for_status()
            data: dict[str, Any] = response.json()
            return [item["embedding"] for item in data["data"]]

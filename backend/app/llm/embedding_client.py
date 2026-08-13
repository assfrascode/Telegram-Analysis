
import hashlib
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.llm.prompt_limits import TextSegment, resolve_prompt_budget, split_texts_by_tokens
from app.observability.metrics import observe_model_call

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


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    segment: TextSegment
    vector: list[float]


def _average_vectors(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        return []
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise RuntimeError("Embedding endpoint returned vectors with inconsistent dimensions")
    return [sum(vector[index] for vector in vectors) / len(vectors) for index in range(dimensions)]


class EmbeddingClient:
    async def _input_token_budget(self) -> int:
        budget = await resolve_prompt_budget(
            base_url=settings.vllm_embedding_base_url,
            model=settings.embedding_model,
            output_reservation=0,
        )
        return budget.input_tokens

    async def split_inputs(self, texts: list[str]) -> list[TextSegment]:
        max_tokens = await self._input_token_budget()
        return split_texts_by_tokens(
            texts,
            max_tokens=max_tokens,
            model=settings.embedding_model,
        )

    async def _embed_raw(self, texts: list[str]) -> list[list[float]]:
        if settings.llm_mock_enabled:
            return [_mock_embedding(text, settings.mock_embedding_dimensions) for text in texts]

        async with observe_model_call("vllm", "embedding", settings.embedding_model):
            async with httpx.AsyncClient(timeout=180.0) as client:
                response = await client.post(
                    f"{settings.vllm_embedding_base_url.rstrip('/')}/embeddings",
                    headers={"Authorization": f"Bearer {settings.vllm_api_key}"},
                    json={"model": settings.embedding_model, "input": texts},
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
                return [item["embedding"] for item in data["data"]]

    async def embed_parts(self, texts: list[str]) -> list[EmbeddingVector]:
        segments = await self.split_inputs(texts)
        vectors = await self._embed_raw([segment.text for segment in segments])
        if len(vectors) != len(segments):
            raise RuntimeError(
                f"Embedding endpoint returned {len(vectors)} vectors for {len(segments)} input segments"
            )
        return [
            EmbeddingVector(segment=segment, vector=vector)
            for segment, vector in zip(segments, vectors, strict=True)
        ]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        part_vectors = await self.embed_parts(texts)
        grouped: list[list[list[float]]] = [[] for _ in texts]
        for item in part_vectors:
            grouped[item.segment.parent_index].append(item.vector)
        return [_average_vectors(vectors) for vectors in grouped]

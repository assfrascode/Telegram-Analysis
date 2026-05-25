from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import get_settings
from app.models import MessageChunk

settings = get_settings()


class QdrantIndexError(RuntimeError):
    pass


@dataclass(slots=True)
class QdrantPoint:
    point_id: str
    vector: list[float]
    payload: dict[str, Any]


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def chunk_payload(chunk: MessageChunk, *, embedding_model: str) -> dict[str, Any]:
    payload = dict(chunk.payload or {})
    payload.update(
        {
            "job_id": str(chunk.job_id),
            "chunk_id": str(chunk.id),
            "chunk_index": chunk.chunk_index,
            "chunk_hash": chunk.chunk_hash,
            "message_ids": list(chunk.message_ids or []),
            "start_timestamp": _iso_or_none(chunk.start_timestamp),
            "end_timestamp": _iso_or_none(chunk.end_timestamp),
            "has_media": bool(chunk.has_media),
            "embedding_model": embedding_model,
            # Useful for Qdrant-only inspection; Postgres remains source of truth.
            "text_preview": chunk.text[:800],
        }
    )
    return payload


class QdrantIndex:
    """Small async REST client for the Qdrant operations used by the MVP.

    The project already depends on ``qdrant-client``, but this REST wrapper keeps
    the worker independent from SDK API changes and mirrors exactly the payloads
    we need for create/upsert/delete/search.
    """

    def __init__(self, *, base_url: str | None = None, collection: str | None = None) -> None:
        self.base_url = (base_url or settings.qdrant_url).rstrip("/")
        self.collection = collection or settings.qdrant_collection

    async def ensure_collection(self, *, vector_size: int) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.base_url}/collections/{self.collection}")
            if response.status_code == 404:
                create = await client.put(
                    f"{self.base_url}/collections/{self.collection}",
                    json={"vectors": {"size": vector_size, "distance": "Cosine"}},
                )
                create.raise_for_status()
                await self._ensure_payload_indexes(client)
                return

            response.raise_for_status()
            current_size = self._extract_vector_size(response.json())
            if current_size is not None and current_size != vector_size:
                raise QdrantIndexError(
                    f"Qdrant collection '{self.collection}' has vector size {current_size}, "
                    f"but current embeddings have size {vector_size}. Use a separate "
                    "QDRANT_COLLECTION or recreate the collection."
                )
            await self._ensure_payload_indexes(client)

    def _extract_vector_size(self, collection_response: dict[str, Any]) -> int | None:
        vectors = (
            collection_response.get("result", {})
            .get("config", {})
            .get("params", {})
            .get("vectors")
        )
        if isinstance(vectors, dict):
            if isinstance(vectors.get("size"), int):
                return vectors["size"]
            # Named vector collection. The MVP uses an unnamed single vector, but
            # accepting this shape makes diagnostics clearer if the collection was
            # created manually.
            for value in vectors.values():
                if isinstance(value, dict) and isinstance(value.get("size"), int):
                    return value["size"]
        return None

    async def _ensure_payload_indexes(self, client: httpx.AsyncClient) -> None:
        # Index creation is best-effort. Qdrant can search without these indexes;
        # they simply make job_id and common filters faster on larger datasets.
        indexes = {
            "job_id": "keyword",
            "chunk_id": "keyword",
            "embedding_model": "keyword",
            "has_media": "bool",
        }
        for field_name, field_schema in indexes.items():
            try:
                response = await client.put(
                    f"{self.base_url}/collections/{self.collection}/index",
                    json={"field_name": field_name, "field_schema": field_schema},
                )
                if response.status_code not in {200, 201, 202, 409}:
                    response.raise_for_status()
            except httpx.HTTPError:
                # Do not fail embedding because an optional payload index failed.
                continue

    async def delete_job_points(self, job_id: uuid.UUID) -> None:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/collections/{self.collection}/points/delete",
                params={"wait": "true"},
                json={
                    "filter": {
                        "must": [
                            {"key": "job_id", "match": {"value": str(job_id)}},
                        ]
                    }
                },
            )
            if response.status_code == 404:
                return
            response.raise_for_status()

    async def upsert_points(self, points: list[QdrantPoint]) -> None:
        if not points:
            return
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.put(
                f"{self.base_url}/collections/{self.collection}/points",
                params={"wait": "true"},
                json={
                    "points": [
                        {"id": point.point_id, "vector": point.vector, "payload": point.payload}
                        for point in points
                    ]
                },
            )
            response.raise_for_status()

    async def search(
        self,
        *,
        vector: list[float],
        job_id: uuid.UUID,
        limit: int,
        with_payload: bool = True,
    ) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/collections/{self.collection}/points/search",
                json={
                    "vector": vector,
                    "limit": limit,
                    "with_payload": with_payload,
                    "filter": {
                        "must": [
                            {"key": "job_id", "match": {"value": str(job_id)}},
                        ]
                    },
                },
            )
            response.raise_for_status()
            return response.json().get("result", [])

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.embedding_client import EmbeddingClient
from app.models import Job, MessageChunk
from app.services.qdrant_index import QdrantIndex, QdrantPoint, chunk_payload
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()


def _embedding_hash(*, model_name: str, chunk_hash: str, text: str) -> str:
    return hashlib.sha256(f"{model_name}\n{chunk_hash}\n{text}".encode("utf-8")).hexdigest()


def _batched(items: list[MessageChunk], batch_size: int) -> list[list[MessageChunk]]:
    if batch_size <= 0:
        raise ValueError("embedding_batch_size must be greater than zero")
    return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]


class EmbeddingWorker(Worker):
    subject = subjects.EMBED_CREATE
    durable = "embedding-worker"
    queue = "embedding"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        chunks = list(
            (
                await session.execute(
                    select(MessageChunk)
                    .where(MessageChunk.job_id == job.id)
                    .order_by(MessageChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )

        await self.emit_event(
            session,
            job=job,
            event_type="embedding.started",
            message="Embedding-Erstellung gestartet",
            payload={
                "chunks_total": len(chunks),
                "model": settings.embedding_model,
                "qdrant_collection": settings.qdrant_collection,
                "mock_enabled": settings.llm_mock_enabled,
            },
        )
        await session.commit()

        if not chunks:
            await self.emit_event(
                session,
                job=job,
                event_type="embedding.completed",
                message="Keine Chunks vorhanden; Embedding-Schritt übersprungen",
                payload={"chunks_total": 0, "chunks_done": 0},
            )
            await session.commit()
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="embedding.cancelled",
                message="Embedding-Erstellung wegen Job-Abbruch nicht weitergeführt",
                payload={"chunks_total": 0},
            )
            await self.enqueue(
                subjects.QUESTION_RETRIEVE,
                {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"retrieve:{job.id}"},
            )
            return

        client = EmbeddingClient()
        qdrant = QdrantIndex()
        total = len(chunks)
        done = 0
        collection_ready = False
        job_points_deleted = False

        for batch in _batched(chunks, settings.embedding_batch_size):
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="embedding.cancelled",
                    message="Embedding-Erstellung wegen Job-Abbruch beendet",
                    payload={"chunks_done": done, "chunks_total": total},
                    level="warning",
                )
                await session.commit()
                return

            vectors = await client.embed([chunk.text for chunk in batch])
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="embedding.cancelled",
                message="Embedding-Erstellung nach Modellaufruf wegen Job-Abbruch beendet",
                payload={"chunks_done": done, "chunks_total": total},
            )
            if len(vectors) != len(batch):
                raise RuntimeError(
                    f"Embedding endpoint returned {len(vectors)} vectors for {len(batch)} chunks"
                )
            if not vectors or not vectors[0]:
                raise RuntimeError("Embedding endpoint returned empty vectors")

            vector_size = len(vectors[0])
            if any(len(vector) != vector_size for vector in vectors):
                raise RuntimeError("Embedding endpoint returned vectors with inconsistent dimensions")

            if not collection_ready:
                await qdrant.ensure_collection(vector_size=vector_size)
                collection_ready = True

            # Idempotent job rebuild: before writing current chunk vectors, remove
            # stale points for this job that may have been created by an earlier
            # failed or cancelled run.
            if not job_points_deleted:
                await qdrant.delete_job_points(job.id)
                job_points_deleted = True

            points: list[QdrantPoint] = []
            embedded_at = datetime.now(timezone.utc)
            for chunk, vector in zip(batch, vectors, strict=True):
                point_id = str(chunk.id)
                embedding_hash = _embedding_hash(
                    model_name=settings.embedding_model,
                    chunk_hash=chunk.chunk_hash,
                    text=chunk.text,
                )
                points.append(
                    QdrantPoint(
                        point_id=point_id,
                        vector=vector,
                        payload=chunk_payload(chunk, embedding_model=settings.embedding_model),
                    )
                )
                chunk.embedding_model = settings.embedding_model
                chunk.embedding_hash = embedding_hash
                chunk.qdrant_point_id = point_id
                chunk.embedded_at = embedded_at

            await qdrant.upsert_points(points)
            done += len(batch)

            await self.emit_event(
                session,
                job=job,
                event_type="embedding.progress",
                message=f"{done}/{total} Chunks eingebettet und in Qdrant gespeichert",
                payload={
                    "chunks_done": done,
                    "chunks_total": total,
                    "vector_size": vector_size,
                    "qdrant_collection": settings.qdrant_collection,
                },
            )
            await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="embedding.cancelled",
            message="Embedding-Erstellung wegen Job-Abbruch beendet",
            payload={"chunks_done": done, "chunks_total": total},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="embedding.completed",
            message="Embedding-Erstellung abgeschlossen",
            payload={
                "chunks_done": done,
                "chunks_total": total,
                "model": settings.embedding_model,
                "qdrant_collection": settings.qdrant_collection,
            },
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="embedding.cancelled",
            message="Embedding-Erstellung nach Abschluss wegen Job-Abbruch nicht weitergeführt",
            payload={"chunks_done": done, "chunks_total": total},
        )

        await self.enqueue(
            subjects.QUESTION_RETRIEVE,
            {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"retrieve:{job.id}"},
        )

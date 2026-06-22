
import uuid
from collections import defaultdict

from sqlalchemy import delete, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Job,
    MediaAnalysis,
    MediaTranscript,
    MessageChunk,
    MessageTranslation,
    TelegramMedia,
    TelegramMessage,
)
from app.services.chunking import MediaAttachment, build_chunks, render_message_block
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()


class ChunkWorker(Worker):
    subject = subjects.CHUNK_CREATE
    durable = "chunk-worker"
    queue = "chunk"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        await self.emit_event(
            session,
            job=job,
            event_type="chunking.started",
            message="Chunk-Erstellung gestartet",
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="chunking.cancelled",
            message="Chunk-Erstellung wegen Job-Abbruch beendet",
        )

        messages = list(
            (
                await session.execute(
                    select(TelegramMessage)
                    .where(TelegramMessage.job_id == job.id)
                    .order_by(
                        nullslast(TelegramMessage.timestamp),
                        TelegramMessage.telegram_message_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        attachments_by_message = await self._load_attachments(session, job.id)
        translations_by_message = await self._load_translations(session, job.id)

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="chunking.cancelled",
            message="Chunk-Erstellung wegen Job-Abbruch beendet",
            payload={"messages_total": len(messages)},
        )

        blocks = [
            render_message_block(
                message,
                attachments_by_message.get(message.id, []),
                translation=translations_by_message.get(message.id),
            )
            for message in messages
        ]
        chunks = build_chunks(
            blocks,
            target_chars=settings.chunk_target_chars,
            overlap_messages=settings.chunk_overlap_messages,
        )

        # Idempotent MVP regeneration: chunking is deterministic for the current
        # message/media-analysis state. Downstream embedding workers can later use
        # chunk_hash to skip unchanged rows.
        await session.execute(delete(MessageChunk).where(MessageChunk.job_id == job.id))
        await session.flush()

        for chunk in chunks:
            session.add(
                MessageChunk(
                    job_id=job.id,
                    chunk_index=chunk.chunk_index,
                    chunk_hash=chunk.chunk_hash,
                    text=chunk.text,
                    message_ids=chunk.message_db_ids,
                    start_timestamp=chunk.start_timestamp,
                    end_timestamp=chunk.end_timestamp,
                    has_media=chunk.has_media,
                    payload={
                        "telegram_message_ids": chunk.telegram_message_ids,
                        "sender_ids": chunk.sender_ids,
                        "sender_names": chunk.sender_names,
                        "message_types": chunk.message_types,
                        "media_ids": chunk.media_ids,
                        "media_types": chunk.media_types,
                        "media_paths": chunk.media_paths,
                        "messages_count": len(chunk.message_db_ids),
                        "chunk_target_chars": settings.chunk_target_chars,
                        "chunk_overlap_messages": settings.chunk_overlap_messages,
                    },
                )
            )

            if (chunk.chunk_index + 1) % 25 == 0:
                await self.emit_event(
                    session,
                    job=job,
                    event_type="chunking.progress",
                    message=f"{chunk.chunk_index + 1} Chunks erstellt",
                    payload={
                        "chunks_done": chunk.chunk_index + 1,
                        "messages_total": len(messages),
                    },
                )
                await session.flush()
                await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="chunking.cancelled",
            message="Chunk-Erstellung wegen Job-Abbruch beendet",
            payload={"chunks_total": len(chunks)},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="chunking.completed",
            message="Chunk-Erstellung abgeschlossen",
            payload={
                "messages_total": len(messages),
                "chunks_total": len(chunks),
                "chunks_with_media": sum(1 for chunk in chunks if chunk.has_media),
            },
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="chunking.cancelled",
            message="Chunk-Erstellung nach Abschluss wegen Job-Abbruch nicht weitergeführt",
            payload={"chunks_total": len(chunks)},
        )

        await self.enqueue(
            subjects.EMBED_CREATE,
            {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"embed:{job.id}"},
        )

    async def _load_attachments(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> dict[uuid.UUID, list[MediaAttachment]]:
        rows = (
            await session.execute(
                select(TelegramMedia, MediaAnalysis, MediaTranscript)
                .outerjoin(
                    MediaAnalysis,
                    (MediaAnalysis.media_id == TelegramMedia.id)
                    & (MediaAnalysis.model_name == settings.vision_model)
                    & (MediaAnalysis.prompt_version == settings.media_analysis_prompt_version),
                )
                .outerjoin(
                    MediaTranscript,
                    (MediaTranscript.media_id == TelegramMedia.id)
                    & (MediaTranscript.provider == "openai")
                    & (MediaTranscript.model_name == settings.openai_transcription_model)
                    & (MediaTranscript.response_format == "text"),
                )
                .where(TelegramMedia.job_id == job_id, TelegramMedia.message_id.is_not(None))
                .order_by(TelegramMedia.original_path)
            )
        ).all()

        grouped: dict[uuid.UUID, list[MediaAttachment]] = defaultdict(list)
        for media, analysis, transcript in rows:
            if media.message_id is None:
                continue
            grouped[media.message_id].append(
                MediaAttachment(media=media, analysis=analysis, transcript=transcript)
            )
        return grouped

    async def _load_translations(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
    ) -> dict[uuid.UUID, MessageTranslation]:
        target_language = (settings.libretranslate_target_language or "en").strip() or "en"
        rows = list(
            (
                await session.execute(
                    select(MessageTranslation).where(
                        MessageTranslation.job_id == job_id,
                        MessageTranslation.provider == "libretranslate",
                        MessageTranslation.target_language == target_language,
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.message_id: row for row in rows if row.translated_text.strip()}

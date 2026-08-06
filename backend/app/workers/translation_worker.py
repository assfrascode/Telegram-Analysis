import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, TypeVar

from sqlalchemy import nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Job,
    MediaTranscript,
    MediaTranscriptTranslation,
    MessageTranslation,
    StepStatus,
    TelegramMessage,
)
from app.services.libretranslate import LibreTranslateClient
from app.services.worker_control import PermanentWorkerError
from app.workers import subjects
from app.workers.base import Worker
from app.workers.pipeline import next_tasks_after_translation

settings = get_settings()

PROVIDER = "libretranslate"
TRANSCRIPT_PROVIDER = "openai"
RESPONSE_FORMAT = "text"
TARGET_LANGUAGE = "en"
T = TypeVar("T")


@dataclass(slots=True)
class TranslationItem:
    kind: Literal["message", "transcript"]
    source: TelegramMessage | MediaTranscript
    text: str
    text_hash: str


def source_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_text(value: str | None) -> str:
    return (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _batched(items: list[T], batch_size: int) -> list[list[T]]:
    size = max(1, batch_size)
    return [items[index : index + size] for index in range(0, len(items), size)]


class TranslateWorker(Worker):
    subject = subjects.MESSAGES_TRANSLATE
    durable = "translation-worker"
    queue = "translation"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        if not settings.libretranslate_base_url.strip():
            await self.emit_event(
                session,
                job=job,
                event_type="translation.failed",
                message="LibreTranslate ist nicht konfiguriert",
                level="error",
                payload={"missing_setting": "LIBRETRANSLATE_BASE_URL"},
            )
            await session.commit()
            raise PermanentWorkerError(
                "Translation is enabled but LIBRETRANSLATE_BASE_URL is not configured"
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
        transcripts = list(
            (
                await session.execute(
                    select(MediaTranscript)
                    .where(
                        MediaTranscript.job_id == job.id,
                        MediaTranscript.provider == TRANSCRIPT_PROVIDER,
                        MediaTranscript.model_name == settings.openai_transcription_model,
                        MediaTranscript.response_format == RESPONSE_FORMAT,
                        MediaTranscript.status == StepStatus.completed,
                    )
                    .order_by(MediaTranscript.media_id, MediaTranscript.id)
                )
            )
            .scalars()
            .all()
        )

        message_ids = [message.id for message in messages]
        if message_ids:
            existing_rows = list(
                (
                    await session.execute(
                        select(MessageTranslation).where(
                            MessageTranslation.job_id == job.id,
                            MessageTranslation.message_id.in_(message_ids),
                            MessageTranslation.provider == PROVIDER,
                            MessageTranslation.target_language == TARGET_LANGUAGE,
                        )
                    )
                )
                .scalars()
                .all()
            )
        else:
            existing_rows = []
        translations_by_message = {row.message_id: row for row in existing_rows}

        transcript_ids = [transcript.id for transcript in transcripts]
        if transcript_ids:
            existing_transcript_rows = list(
                (
                    await session.execute(
                        select(MediaTranscriptTranslation).where(
                            MediaTranscriptTranslation.job_id == job.id,
                            MediaTranscriptTranslation.transcript_id.in_(transcript_ids),
                            MediaTranscriptTranslation.provider == PROVIDER,
                            MediaTranscriptTranslation.target_language == TARGET_LANGUAGE,
                        )
                    )
                )
                .scalars()
                .all()
            )
        else:
            existing_transcript_rows = []
        translations_by_transcript = {
            row.transcript_id: row for row in existing_transcript_rows
        }

        pending: list[TranslationItem] = []
        message_skipped_empty = 0
        message_skipped_existing = 0
        for message in messages:
            text = _source_text(message.text)
            if not text:
                message_skipped_empty += 1
                continue
            text_hash = source_text_hash(text)
            existing = translations_by_message.get(message.id)
            if (
                existing
                and existing.source_text_hash == text_hash
                and existing.translated_text.strip()
            ):
                message_skipped_existing += 1
                continue
            pending.append(TranslationItem("message", message, text, text_hash))

        transcript_skipped_empty = 0
        transcript_skipped_existing = 0
        for transcript in transcripts:
            text = _source_text(transcript.transcript_text)
            if not text:
                transcript_skipped_empty += 1
                continue
            text_hash = source_text_hash(text)
            existing = translations_by_transcript.get(transcript.id)
            if (
                existing
                and existing.source_text_hash == text_hash
                and existing.translated_text.strip()
            ):
                transcript_skipped_existing += 1
                continue
            pending.append(TranslationItem("transcript", transcript, text, text_hash))

        message_texts_total = (
            len(messages) - message_skipped_empty
        )
        transcript_texts_total = (
            len(transcripts) - transcript_skipped_empty
        )
        skipped_empty = message_skipped_empty + transcript_skipped_empty
        skipped_existing = message_skipped_existing + transcript_skipped_existing
        texts_total = message_texts_total + transcript_texts_total

        await self.emit_event(
            session,
            job=job,
            event_type="translation.started",
            message="LibreTranslate-Inhaltsübersetzung gestartet",
            payload={
                "messages_total": len(messages),
                "message_texts_total": message_texts_total,
                "message_texts_pending": sum(item.kind == "message" for item in pending),
                "transcripts_total": len(transcripts),
                "transcript_texts_total": transcript_texts_total,
                "transcript_texts_pending": sum(item.kind == "transcript" for item in pending),
                "texts_total": texts_total,
                "texts_pending": len(pending),
                "skipped_empty": skipped_empty,
                "skipped_existing": skipped_existing,
                "target_language": TARGET_LANGUAGE,
                "provider": PROVIDER,
            },
        )
        await session.commit()

        done = skipped_existing
        translated = 0
        client = LibreTranslateClient()
        for batch in _batched(pending, settings.libretranslate_batch_size):
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="translation.cancelled",
                    message="Übersetzung wegen Job-Abbruch beendet",
                    level="warning",
                    payload={
                        "texts_done": done,
                        "texts_total": texts_total,
                        "target_language": TARGET_LANGUAGE,
                    },
                )
                await session.commit()
                return

            try:
                results = await client.translate_texts(
                    [item.text for item in batch],
                    target_language=TARGET_LANGUAGE,
                    source_language="auto",
                )
            except ValueError as exc:
                raise PermanentWorkerError(str(exc)) from exc

            if any(not result.translated_text.strip() for result in results):
                raise PermanentWorkerError("LibreTranslate returned a blank translation")

            now = datetime.now(timezone.utc)
            for item, result in zip(batch, results, strict=True):
                if item.kind == "message":
                    row = translations_by_message.get(item.source.id)
                    if row is None:
                        row = MessageTranslation(
                            job_id=job.id,
                            message_id=item.source.id,
                            provider=PROVIDER,
                            target_language=TARGET_LANGUAGE,
                            source_text_hash=item.text_hash,
                            translated_text=result.translated_text.strip(),
                        )
                        session.add(row)
                        translations_by_message[item.source.id] = row
                else:
                    row = translations_by_transcript.get(item.source.id)
                    if row is None:
                        row = MediaTranscriptTranslation(
                            job_id=job.id,
                            transcript_id=item.source.id,
                            provider=PROVIDER,
                            target_language=TARGET_LANGUAGE,
                            source_text_hash=item.text_hash,
                            translated_text=result.translated_text.strip(),
                        )
                        session.add(row)
                        translations_by_transcript[item.source.id] = row
                row.source_text_hash = item.text_hash
                row.detected_source_language = result.detected_language
                row.detected_source_confidence = result.detected_confidence
                row.target_language = TARGET_LANGUAGE
                row.translated_text = result.translated_text.strip()
                row.raw_response = result.raw_response
                row.updated_at = now

            translated += len(batch)
            done += len(batch)
            await self.emit_event(
                session,
                job=job,
                event_type="translation.progress",
                message=f"{done}/{texts_total} Inhalte übersetzt",
                payload={
                    "texts_done": done,
                    "texts_total": texts_total,
                    "translated": translated,
                    "skipped_existing": skipped_existing,
                    "target_language": TARGET_LANGUAGE,
                },
            )
            await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="translation.cancelled",
            message="Übersetzung wegen Job-Abbruch beendet",
            payload={"texts_done": done, "texts_total": texts_total},
        )

        if done != texts_total:
            raise PermanentWorkerError(
                f"Translation incomplete: expected {texts_total} English texts, completed {done}"
            )

        await self.emit_event(
            session,
            job=job,
            event_type="translation.completed",
            message="LibreTranslate-Inhaltsübersetzung abgeschlossen",
            payload={
                "messages_total": len(messages),
                "message_texts_total": message_texts_total,
                "message_texts_translated": sum(item.kind == "message" for item in pending),
                "transcripts_total": len(transcripts),
                "transcript_texts_total": transcript_texts_total,
                "transcript_texts_translated": sum(item.kind == "transcript" for item in pending),
                "texts_total": texts_total,
                "texts_done": done,
                "translated": translated,
                "skipped_empty": skipped_empty,
                "skipped_existing": skipped_existing,
                "target_language": TARGET_LANGUAGE,
                "provider": PROVIDER,
            },
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="translation.cancelled",
            message="Übersetzung nach Abschluss wegen Job-Abbruch nicht weitergeführt",
        )

        for next_subject, next_key in next_tasks_after_translation():
            await self.enqueue(
                next_subject,
                {
                    "job_id": str(job.id),
                    "owner_user_id": str(job.owner_user_id),
                    "task_key": f"{next_key}:{job.id}",
                },
            )

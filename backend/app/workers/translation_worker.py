import hashlib
import uuid
from datetime import datetime, timezone
from typing import TypeVar

from sqlalchemy import nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job, MessageTranslation, TelegramMessage
from app.services.libretranslate import LibreTranslateClient
from app.services.worker_control import PermanentWorkerError
from app.workers import subjects
from app.workers.base import Worker
from app.workers.pipeline import next_tasks_after_translation

settings = get_settings()

PROVIDER = "libretranslate"
T = TypeVar("T")


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

        target_language = (settings.libretranslate_target_language or "en").strip() or "en"
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
        message_ids = [message.id for message in messages]
        if message_ids:
            existing_rows = list(
                (
                    await session.execute(
                        select(MessageTranslation).where(
                            MessageTranslation.job_id == job.id,
                            MessageTranslation.message_id.in_(message_ids),
                            MessageTranslation.provider == PROVIDER,
                            MessageTranslation.target_language == target_language,
                        )
                    )
                )
                .scalars()
                .all()
            )
        else:
            existing_rows = []
        translations_by_message = {row.message_id: row for row in existing_rows}

        pending: list[tuple[TelegramMessage, str, str]] = []
        skipped_empty = 0
        skipped_existing = 0
        for message in messages:
            text = _source_text(message.text)
            if not text:
                skipped_empty += 1
                continue
            text_hash = source_text_hash(text)
            existing = translations_by_message.get(message.id)
            if existing and existing.source_text_hash == text_hash and existing.translated_text:
                skipped_existing += 1
                continue
            pending.append((message, text, text_hash))

        await self.emit_event(
            session,
            job=job,
            event_type="translation.started",
            message="LibreTranslate-Übersetzung gestartet",
            payload={
                "messages_total": len(messages),
                "texts_total": len(pending) + skipped_existing,
                "texts_pending": len(pending),
                "skipped_empty": skipped_empty,
                "skipped_existing": skipped_existing,
                "target_language": target_language,
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
                        "texts_total": len(pending) + skipped_existing,
                        "target_language": target_language,
                    },
                )
                await session.commit()
                return

            try:
                results = await client.translate_texts(
                    [item[1] for item in batch],
                    target_language=target_language,
                    source_language="auto",
                )
            except ValueError as exc:
                raise PermanentWorkerError(str(exc)) from exc

            now = datetime.now(timezone.utc)
            for (message, _text, text_hash), result in zip(batch, results, strict=True):
                row = translations_by_message.get(message.id)
                if row is None:
                    row = MessageTranslation(
                        job_id=job.id,
                        message_id=message.id,
                        provider=PROVIDER,
                        target_language=target_language,
                        source_text_hash=text_hash,
                        translated_text=result.translated_text,
                    )
                    session.add(row)
                    translations_by_message[message.id] = row
                row.source_text_hash = text_hash
                row.detected_source_language = result.detected_language
                row.detected_source_confidence = result.detected_confidence
                row.target_language = target_language
                row.translated_text = result.translated_text
                row.raw_response = result.raw_response
                row.updated_at = now

            translated += len(batch)
            done += len(batch)
            await self.emit_event(
                session,
                job=job,
                event_type="translation.progress",
                message=f"{done}/{len(pending) + skipped_existing} Nachrichten übersetzt",
                payload={
                    "texts_done": done,
                    "texts_total": len(pending) + skipped_existing,
                    "translated": translated,
                    "skipped_existing": skipped_existing,
                    "target_language": target_language,
                },
            )
            await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="translation.cancelled",
            message="Übersetzung wegen Job-Abbruch beendet",
            payload={"texts_done": done, "texts_total": len(pending) + skipped_existing},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="translation.completed",
            message="LibreTranslate-Übersetzung abgeschlossen",
            payload={
                "messages_total": len(messages),
                "texts_total": len(pending) + skipped_existing,
                "texts_done": done,
                "translated": translated,
                "skipped_empty": skipped_empty,
                "skipped_existing": skipped_existing,
                "target_language": target_language,
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

        for next_subject, next_key in next_tasks_after_translation(job):
            await self.enqueue(
                next_subject,
                {
                    "job_id": str(job.id),
                    "owner_user_id": str(job.owner_user_id),
                    "task_key": f"{next_key}:{job.id}",
                },
            )

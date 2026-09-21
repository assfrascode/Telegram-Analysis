import asyncio
import os
import tempfile
import uuid
from itertools import batched
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job, JobStatus, StepStatus, TelegramMedia, TelegramMessage
from app.services.minio_store import extracted_prefix, list_objects, minio_client
from app.services.telegram_export import (
    count_result_messages,
    iter_result_messages,
    parse_message,
    relative_path_from_root,
    telegram_export_root_from_result_key,
)
from app.workers import subjects
from app.workers.base import Worker
from app.workers.pipeline import next_tasks_after_messages

settings = get_settings()


def _locate_result_json(prefix: str) -> str | None:
    candidates = [obj.object_name for obj in list_objects(prefix) if PurePosixPath(obj.object_name).name == "result.json"]
    if not candidates:
        return None
    return sorted(candidates, key=lambda key: (key.count("/"), len(key)))[0]


def _download_to_temp(object_key: str) -> str:
    temp = tempfile.NamedTemporaryFile(prefix="chat-analyse-result-", suffix=".json", delete=False)
    temp.close()
    minio_client().fget_object(settings.minio_bucket, object_key, temp.name)
    return temp.name


def _list_available_media(root_prefix: str) -> dict[str, dict[str, Any]]:
    available: dict[str, dict[str, Any]] = {}
    for obj in list_objects(root_prefix):
        if obj.object_name.endswith("/"):
            continue
        try:
            relative = relative_path_from_root(root_prefix, obj.object_name)
        except Exception:
            continue
        available[relative] = {
            "object_key": obj.object_name,
            "size_bytes": getattr(obj, "size", None),
            "etag": getattr(obj, "etag", None),
        }
    return available


def _set_message_values(existing: TelegramMessage, parsed) -> None:
    existing.timestamp = parsed.timestamp
    existing.edited_timestamp = parsed.edited_timestamp
    existing.sender_id = parsed.sender_id
    existing.sender_name = parsed.sender_name
    existing.message_type = parsed.message_type
    existing.reply_to_message_id = parsed.reply_to_message_id
    existing.forwarded_from = parsed.forwarded_from
    existing.reactions = parsed.reactions
    existing.text = parsed.text
    existing.raw = parsed.raw


def _set_media_values(
    existing: TelegramMedia,
    media_ref,
    available_media: dict[str, dict[str, Any]],
) -> TelegramMedia:
    existing.media_type = media_ref.media_type
    existing.original_path = media_ref.original_path

    if media_ref.missing_reason:
        existing.status = StepStatus.failed_permanent
        existing.missing_reason = media_ref.missing_reason
        existing.minio_object_key = None
        existing.size_bytes = None
        return existing

    found = available_media.get(media_ref.original_path)
    if found:
        existing.status = (
            StepStatus.pending
            if media_ref.media_type in {"image", "video"}
            else StepStatus.completed
        )
        existing.missing_reason = None
        existing.minio_object_key = found["object_key"]
        existing.size_bytes = found["size_bytes"]
    else:
        existing.status = StepStatus.failed_permanent
        existing.missing_reason = "file_missing_after_extraction"
        existing.minio_object_key = None
        existing.size_bytes = None

    return existing


async def _persist_message_batch(
    session: AsyncSession,
    job: Job,
    parsed_messages: list,
    available_media: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    """Prefetch each table once; retain ORM change detection on parser retries."""
    rows = (
        await session.execute(
            select(TelegramMessage).where(
                TelegramMessage.job_id == job.id,
                TelegramMessage.telegram_message_id.in_(
                    [parsed.telegram_message_id for parsed in parsed_messages]
                ),
            )
        )
    ).scalars().all()
    messages = {row.telegram_message_id: row for row in rows}
    for parsed in parsed_messages:
        row = messages.get(parsed.telegram_message_id)
        if row is None:
            row = TelegramMessage(job_id=job.id, telegram_message_id=parsed.telegram_message_id)
            session.add(row)
            messages[parsed.telegram_message_id] = row
        _set_message_values(row, parsed)
    # Generate parent IDs and insert all parents before their media children.
    await session.flush()

    media_message_ids = list({
        messages[parsed.telegram_message_id].id for parsed in parsed_messages if parsed.media
    })
    media_rows = (
        (
            await session.execute(
                select(TelegramMedia).where(
                    TelegramMedia.job_id == job.id,
                    TelegramMedia.message_id.in_(media_message_ids),
                )
            )
        ).scalars().all()
        if media_message_ids else []
    )
    media_by_key = {(row.message_id, row.original_path): row for row in media_rows}
    total = available = missing = 0
    for parsed in parsed_messages:
        message = messages[parsed.telegram_message_id]
        for media_ref in parsed.media:
            key = (message.id, media_ref.original_path)
            media = media_by_key.get(key)
            if media is None:
                media = TelegramMedia(job_id=job.id, message_id=message.id)
                session.add(media)
                media_by_key[key] = media
            _set_media_values(media, media_ref, available_media)
            total += 1
            if media.status == StepStatus.pending:
                available += 1
            else:
                missing += 1
    await session.flush()
    return total, available, missing


class ParserWorker(Worker):
    subject = subjects.PARSE
    durable = "parser-worker"
    queue = "parser"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        source_format = str(payload.get("source_format") or "json")
        await self.emit_event(
            session,
            job=job,
            event_type="telegram.parse.started",
            message="Telegram-Export Parsing gestartet",
            payload={"source_format": source_format},
        )
        await session.commit()

        prefix = payload.get("extracted_prefix") or extracted_prefix(job.owner_user_id, job.id)
        result_object_key = payload.get("result_json_object_key")
        if not result_object_key:
            result_object_key = await asyncio.to_thread(_locate_result_json, prefix)

        if not result_object_key:
            job.status = JobStatus.failed
            job.error_message = "parseable Telegram export data not found"
            job.completed_at = datetime.now(timezone.utc)
            await self.emit_event(
                session,
                job=job,
                event_type="telegram.parse.failed",
                message="Keine parsebare Telegram-Exportdatei gefunden",
                level="error",
            )
            return

        export_root_prefix = telegram_export_root_from_result_key(prefix, result_object_key)
        result_temp_path = await asyncio.to_thread(_download_to_temp, result_object_key)
        try:
            available_media = await asyncio.to_thread(_list_available_media, export_root_prefix)

            messages_total = None
            with open(result_temp_path, "rb") as count_file:
                messages_total = count_result_messages(
                    count_file,
                    max_messages=settings.max_telegram_messages_per_export,
                    max_message_bytes=settings.max_telegram_message_chars,
                )

            await self.emit_event(
                session,
                job=job,
                event_type="telegram.parse.progress",
                message="Telegram-Export geladen; Message-Parsing läuft",
                payload={
                    "messages_total": messages_total,
                    "export_root_prefix": export_root_prefix,
                    "source_format": source_format,
                },
            )

            messages_done = 0
            media_total = 0
            media_available = 0
            media_missing = 0

            with open(result_temp_path, "rb") as result_file:
                parsed_messages = (
                    parsed
                    for raw in iter_result_messages(
                        result_file,
                        max_messages=settings.max_telegram_messages_per_export,
                        max_message_bytes=settings.max_telegram_message_chars,
                    )
                    if (parsed := parse_message(raw)) is not None
                )
                for batch in batched(parsed_messages, 500):
                    total, available, missing = await _persist_message_batch(
                        session, job, list(batch), available_media
                    )
                    media_total += total
                    media_available += available
                    media_missing += missing
                    messages_done += len(batch)
                    if messages_done % 1000 == 0:
                        await self.raise_if_cancelled(session, job.id)
                        await session.flush()
                        await self.emit_event(
                            session,
                            job=job,
                            event_type="telegram.parse.progress",
                            message=f"{messages_done} Telegram-Nachrichten geparst",
                            payload={
                                "messages_done": messages_done,
                                "messages_total": messages_total,
                                "media_total": media_total,
                                "media_available": media_available,
                                "media_missing": media_missing,
                                "source_format": source_format,
                            },
                        )

            await self.checkpoint_cancelled(
                session,
                job,
                event_type="telegram.parse.cancelled",
                message="Telegram-Parsing wegen Job-Abbruch beendet",
                payload={"messages_done": messages_done, "messages_total": messages_total},
            )

            await self.emit_event(
                session,
                job=job,
                event_type="telegram.parse.completed",
                message="Telegram-Export Parsing abgeschlossen",
                payload={
                    "messages_total": messages_done,
                    "media_total": media_total,
                    "media_available": media_available,
                    "media_missing": media_missing,
                    "result_json_object_key": result_object_key,
                    "export_root_prefix": export_root_prefix,
                    "source_format": source_format,
                },
            )
        finally:
            try:
                os.unlink(result_temp_path)
            except FileNotFoundError:
                pass

        await session.commit()
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="telegram.parse.cancelled",
            message="Telegram-Parsing nach Abschluss wegen Job-Abbruch nicht weitergeführt",
        )

        for next_subject, next_key in next_tasks_after_messages(job):
            await self.enqueue(
                next_subject,
                {
                    "job_id": str(job.id),
                    "owner_user_id": str(job.owner_user_id),
                    "task_key": f"{next_key}:{job.id}",
                },
            )

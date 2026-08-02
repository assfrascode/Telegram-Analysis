import asyncio
import mimetypes
import os
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    CollectedMediaTranscript,
    Job,
    MediaTranscript,
    StepStatus,
    TelegramMedia,
)
from app.services.minio_store import minio_client
from app.services.openai_transcription import OpenAITranscriptionClient
from app.workers import subjects
from app.workers.base import Worker
from app.workers.pipeline import MEDIA_TRANSCRIPTION_STEP, complete_media_branch

settings = get_settings()

PROVIDER = "openai"
RESPONSE_FORMAT = "text"
TRANSCRIBABLE_MEDIA_TYPES = ("audio", "voice", "video")
RETRYABLE_STATUSES = (StepStatus.pending, StepStatus.running, StepStatus.failed_retryable)
SUPPORTED_TRANSCRIPTION_EXTENSIONS = {
    ".flac",
    ".m4a",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".ogg",
    ".wav",
    ".webm",
}


@dataclass(slots=True)
class TranscriptionWorkResult:
    media_id: uuid.UUID
    status: StepStatus
    transcript_text: str | None = None
    raw_response: dict[str, Any] | None = None
    error: str | None = None
    permanent: bool = False


def _short_error(exc: BaseException, max_len: int = 1000) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _is_retryable_http_error(exc: httpx.HTTPStatusError) -> bool:
    status_code = exc.response.status_code
    return status_code == 429 or 500 <= status_code <= 599


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return _is_retryable_http_error(exc)
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return True
    return False


def _supported_transcription_path(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SUPPORTED_TRANSCRIPTION_EXTENSIONS


def _content_type_for_path(path: str) -> str:
    mime_type, _ = mimetypes.guess_type(path)
    return mime_type or "application/octet-stream"


def _download_object_to_tempfile(object_key: str, suffix: str) -> str:
    temp = tempfile.NamedTemporaryFile(
        prefix="chat-analyse-transcribe-",
        suffix=suffix,
        delete=False,
    )
    temp.close()
    minio_client().fget_object(settings.minio_bucket, object_key, temp.name)
    return temp.name


class TranscriptionWorker(Worker):
    subject = subjects.MEDIA_TRANSCRIBE
    durable = "transcription-worker"
    queue = "transcription"

    def __init__(self) -> None:
        super().__init__()
        self.client = OpenAITranscriptionClient()

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        await self.emit_event(
            session,
            job=job,
            event_type="media.transcription.started",
            message="Medientranskription gestartet",
            payload=await self._stats(session, job.id),
        )
        await session.commit()

        while True:
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.transcription.cancelled",
                    message="Medientranskription wegen Job-Abbruch beendet",
                    level="warning",
                    payload=await self._stats(session, job.id),
                )
                await session.commit()
                return

            rows = await self._next_batch(session, job.id)
            if not rows:
                break

            await self._mark_batch_running(session, rows)
            await self.emit_event(
                session,
                job=job,
                event_type="media.transcription.progress",
                message="Transkriptions-Batch gestartet",
                payload=await self._stats(session, job.id),
            )
            await session.commit()

            retryable_failures: list[str] = []
            for row in rows:
                result = await self._transcribe_one(row)
                retryable_error = await self._persist_result(session, job, row, result)
                if retryable_error:
                    retryable_failures.append(retryable_error)

                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.transcription.progress",
                    message=self._progress_message(row, result),
                    level="warning" if result.status != StepStatus.completed else "info",
                    payload={
                        **(await self._stats(session, job.id)),
                        "media_id": str(row.id),
                        "media_type": row.media_type,
                        "original_path": row.original_path,
                        "transcription_status": result.status.value,
                    },
                )
                await session.commit()

            if retryable_failures:
                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.transcription.retrying_rows",
                    message="Einige Transkriptionen werden intern erneut versucht",
                    level="warning",
                    payload={
                        **(await self._stats(session, job.id)),
                        "retryable_errors_sample": retryable_failures[:3],
                    },
                )
                await session.commit()
                await asyncio.sleep(min(2.0, settings.worker_retry_base_delay_seconds))
                continue

        final_stats = await self._stats(session, job.id)
        permanent_failures = final_stats.get("transcription_permanent_failed", 0)
        await self.emit_event(
            session,
            job=job,
            event_type="media.transcription.completed",
            message=(
                "Medientranskription abgeschlossen"
                if not permanent_failures
                else "Medientranskription abgeschlossen; einige Medien konnten nicht transkribiert werden"
            ),
            level="warning" if permanent_failures else "info",
            payload=final_stats,
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="media.transcription.cancelled",
            message="Medientranskription nach Abschluss wegen Job-Abbruch nicht weitergeführt",
            payload=final_stats,
        )

        next_task = await complete_media_branch(
            session,
            job_id=job.id,
            step_name=MEDIA_TRANSCRIPTION_STEP,
        )
        if next_task is not None:
            next_subject, next_key = next_task
            await self.enqueue(
                next_subject,
                {
                    "job_id": str(job.id),
                    "owner_user_id": str(job.owner_user_id),
                    "task_key": f"{next_key}:{job.id}",
                },
            )

    async def _next_batch(self, session: AsyncSession, job_id: uuid.UUID) -> list[TelegramMedia]:
        transcript_join = (
            (MediaTranscript.media_id == TelegramMedia.id)
            & (MediaTranscript.provider == PROVIDER)
            & (MediaTranscript.model_name == settings.openai_transcription_model)
            & (MediaTranscript.response_format == RESPONSE_FORMAT)
        )
        result = await session.execute(
            select(TelegramMedia, MediaTranscript)
            .outerjoin(MediaTranscript, transcript_join)
            .where(
                TelegramMedia.job_id == job_id,
                TelegramMedia.media_type.in_(TRANSCRIBABLE_MEDIA_TYPES),
                TelegramMedia.minio_object_key.is_not(None),
                or_(
                    MediaTranscript.id.is_(None),
                    MediaTranscript.status.in_(RETRYABLE_STATUSES),
                ),
            )
            .order_by(TelegramMedia.id)
            .limit(settings.openai_transcription_batch_size)
        )
        return [media for media, _transcript in result.all()]

    async def _mark_batch_running(self, session: AsyncSession, rows: list[TelegramMedia]) -> None:
        for row in rows:
            transcript = await self._existing_transcript(session, row.id)
            if transcript is None:
                transcript = MediaTranscript(
                    job_id=row.job_id,
                    media_id=row.id,
                    provider=PROVIDER,
                    model_name=settings.openai_transcription_model,
                    response_format=RESPONSE_FORMAT,
                    status=StepStatus.running,
                )
                session.add(transcript)
            else:
                transcript.status = StepStatus.running
                transcript.error_message = None
            transcript.updated_at = datetime.now(timezone.utc)
        await session.flush()

    async def _transcribe_one(self, row: TelegramMedia) -> TranscriptionWorkResult:
        if not row.minio_object_key:
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.failed_permanent,
                error="media_object_missing",
                permanent=True,
            )

        normalized_type = (row.media_type or "media").lower()
        if settings.llm_mock_enabled:
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.completed,
                transcript_text=(
                    f"[MOCK_{normalized_type.upper()}_TRANSCRIPT] "
                    f"Neutrales Platzhaltertranskript für {row.original_path}. "
                    "Es wurde kein OpenAI-Request ausgeführt."
                ),
                raw_response={
                    "mock": True,
                    "media_id": str(row.id),
                    "media_type": normalized_type,
                    "original_path": row.original_path,
                    "provider": PROVIDER,
                    "model": settings.openai_transcription_model,
                    "response_format": RESPONSE_FORMAT,
                },
            )

        if not settings.openai_api_key.strip():
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.failed_permanent,
                error="openai_api_key_missing",
                permanent=True,
            )

        if row.size_bytes is not None and row.size_bytes > settings.openai_transcription_max_bytes:
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.failed_permanent,
                error="media_too_large_for_openai_transcription",
                permanent=True,
            )

        if not _supported_transcription_path(row.original_path):
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.failed_permanent,
                error="unsupported_openai_transcription_format",
                permanent=True,
            )

        suffix = PurePosixPath(row.original_path).suffix.lower()
        temp_path = await asyncio.to_thread(_download_object_to_tempfile, row.minio_object_key, suffix)
        try:
            result = await self.client.transcribe_file(
                temp_path,
                filename=PurePosixPath(row.original_path).name,
                content_type=_content_type_for_path(row.original_path),
            )
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.completed,
                transcript_text=result.transcript_text,
                raw_response=result.raw_response,
            )
        except Exception as exc:
            return TranscriptionWorkResult(
                media_id=row.id,
                status=StepStatus.failed_retryable,
                error=_short_error(exc),
                permanent=not _is_retryable_exception(exc),
            )
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    async def _persist_result(
        self,
        session: AsyncSession,
        job: Job,
        row: TelegramMedia,
        result: TranscriptionWorkResult,
    ) -> str | None:
        transcript = await self._existing_transcript(session, row.id)
        if transcript is None:
            transcript = MediaTranscript(
                job_id=job.id,
                media_id=row.id,
                provider=PROVIDER,
                model_name=settings.openai_transcription_model,
                response_format=RESPONSE_FORMAT,
            )
            session.add(transcript)

        transcript.attempts = (transcript.attempts or 0) + 1
        transcript.updated_at = datetime.now(timezone.utc)

        if result.status == StepStatus.completed:
            transcript.status = StepStatus.completed
            transcript.transcript_text = result.transcript_text or ""
            transcript.error_message = None
            transcript.raw_response = result.raw_response or {}
            await self._upsert_source_transcript(session, row, transcript)
            await session.flush()
            return None

        error_text = result.error or "media_transcription_failed"
        if result.permanent or transcript.attempts >= settings.openai_transcription_max_attempts:
            transcript.status = StepStatus.failed_permanent
            transcript.error_message = error_text
            transcript.raw_response = result.raw_response or transcript.raw_response or {}
            await self._upsert_source_transcript(session, row, transcript)
            await session.flush()
            return None

        transcript.status = StepStatus.failed_retryable
        transcript.error_message = error_text
        transcript.raw_response = result.raw_response or transcript.raw_response or {}
        await session.flush()
        return error_text

    async def _upsert_source_transcript(
        self,
        session: AsyncSession,
        row: TelegramMedia,
        transcript: MediaTranscript,
    ) -> None:
        if row.source_media_id is None:
            return
        if transcript.status not in {StepStatus.completed, StepStatus.failed_permanent}:
            return

        source = (
            await session.execute(
                select(CollectedMediaTranscript).where(
                    CollectedMediaTranscript.media_id == row.source_media_id,
                    CollectedMediaTranscript.provider == transcript.provider,
                    CollectedMediaTranscript.model_name == transcript.model_name,
                    CollectedMediaTranscript.response_format == transcript.response_format,
                )
            )
        ).scalar_one_or_none()
        if source is None:
            source = CollectedMediaTranscript(
                media_id=row.source_media_id,
                provider=transcript.provider,
                model_name=transcript.model_name,
                response_format=transcript.response_format,
            )
            session.add(source)

        source.status = transcript.status
        source.attempts = transcript.attempts
        source.transcript_text = transcript.transcript_text
        source.error_message = transcript.error_message
        source.raw_response = transcript.raw_response
        source.updated_at = datetime.now(timezone.utc)

    async def _existing_transcript(
        self,
        session: AsyncSession,
        media_id: uuid.UUID,
    ) -> MediaTranscript | None:
        return (
            await session.execute(
                select(MediaTranscript).where(
                    MediaTranscript.media_id == media_id,
                    MediaTranscript.provider == PROVIDER,
                    MediaTranscript.model_name == settings.openai_transcription_model,
                    MediaTranscript.response_format == RESPONSE_FORMAT,
                )
            )
        ).scalar_one_or_none()

    async def _stats(self, session: AsyncSession, job_id: uuid.UUID) -> dict[str, int]:
        total = await self._count_media(session, job_id)
        completed = await self._count_transcripts(session, job_id, StepStatus.completed)
        running = await self._count_transcripts(session, job_id, StepStatus.running)
        retryable = await self._count_transcripts(session, job_id, StepStatus.failed_retryable)
        permanent = await self._count_transcripts(session, job_id, StepStatus.failed_permanent)
        pending = max(0, total - completed - running - retryable - permanent)
        return {
            "total": total,
            "done": completed,
            "transcription_total": total,
            "transcription_done": completed,
            "transcription_pending": pending,
            "transcription_running": running,
            "transcription_retryable_failed": retryable,
            "transcription_permanent_failed": permanent,
        }

    async def _count_media(self, session: AsyncSession, job_id: uuid.UUID) -> int:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(TelegramMedia)
                    .where(
                        TelegramMedia.job_id == job_id,
                        TelegramMedia.media_type.in_(TRANSCRIBABLE_MEDIA_TYPES),
                        TelegramMedia.minio_object_key.is_not(None),
                    )
                )
            ).scalar_one()
        )

    async def _count_transcripts(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        status: StepStatus,
    ) -> int:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MediaTranscript)
                    .join(TelegramMedia, TelegramMedia.id == MediaTranscript.media_id)
                    .where(
                        TelegramMedia.job_id == job_id,
                        TelegramMedia.media_type.in_(TRANSCRIBABLE_MEDIA_TYPES),
                        MediaTranscript.provider == PROVIDER,
                        MediaTranscript.model_name == settings.openai_transcription_model,
                        MediaTranscript.response_format == RESPONSE_FORMAT,
                        MediaTranscript.status == status,
                    )
                )
            ).scalar_one()
        )

    def _progress_message(self, row: TelegramMedia, result: TranscriptionWorkResult) -> str:
        if result.status == StepStatus.completed:
            return f"Medium transkribiert: {row.original_path}"
        return f"Medium konnte nicht transkribiert werden: {row.original_path}"

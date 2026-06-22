
import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.vllm_gateway import DEFAULT_MEDIA_DESCRIPTION_PROMPT, VLLMGateway
from app.models import (
    CollectedMediaAnalysis,
    Job,
    MediaAnalysis,
    StepStatus,
    TelegramMedia,
)
from app.services.media_sources import build_media_source_url
from app.workers import subjects
from app.services.worker_control import WorkerCancelled
from app.workers.base import Worker
from app.workers.pipeline import next_subject_after_media_analysis

settings = get_settings()

MEDIA_TYPES = ("image", "video")
RETRYABLE_STATUSES = (StepStatus.pending, StepStatus.running, StepStatus.failed_retryable)


@dataclass(slots=True)
class MediaWorkResult:
    media_id: uuid.UUID
    status: StepStatus
    description: str | None = None
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


class MediaWorker(Worker):
    subject = subjects.MEDIA_DESCRIBE
    durable = "media-worker"
    queue = "media"

    def fail_job_on_dead_letter(self, payload: dict[str, Any], exc: Exception, reason: str) -> bool:
        return settings.media_fail_job_on_error

    def __init__(self) -> None:
        super().__init__()
        self.gateway = VLLMGateway()
        self._semaphore = asyncio.Semaphore(settings.media_analysis_concurrency)

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        await self.emit_event(
            session,
            job=job,
            event_type="media.analysis.started",
            message="Medienanalyse gestartet",
            payload=await self._stats(session, job.id),
        )
        await session.commit()

        while True:
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.analysis.cancelled",
                    message="Medienanalyse wegen Job-Abbruch beendet",
                    level="warning",
                    payload=await self._stats(session, job.id),
                )
                await session.commit()
                return

            media_rows = await self._next_batch(session, job.id)
            if not media_rows:
                break

            await self._mark_batch_running(session, media_rows)
            await self.emit_event(
                session,
                job=job,
                event_type="media.analysis.progress",
                message="Medienanalyse-Batch an vLLM übergeben",
                payload=await self._stats(session, job.id),
            )
            await session.commit()

            results = await self._analyze_batch_with_cancellation_checks(
                session=session,
                job=job,
                rows=media_rows,
            )

            retryable_failures: list[str] = []
            for row, result in zip(media_rows, results, strict=True):
                if isinstance(result, BaseException):
                    result = MediaWorkResult(
                        media_id=row.id,
                        status=StepStatus.failed_retryable,
                        error=_short_error(result),
                        permanent=not _is_retryable_exception(result),
                    )

                retryable_error = await self._persist_result(session, job, row, result)
                if retryable_error:
                    retryable_failures.append(retryable_error)

                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.analysis.progress",
                    message=self._progress_message(row, result),
                    level="warning" if result.status != StepStatus.completed else "info",
                    payload={
                        **(await self._stats(session, job.id)),
                        "media_id": str(row.id),
                        "media_type": row.media_type,
                        "original_path": row.original_path,
                        "media_status": result.status.value,
                    },
                )
                await session.commit()

            if retryable_failures:
                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.analysis.retrying_rows",
                    message="Einige Medien werden intern erneut versucht",
                    level="warning",
                    payload={
                        **(await self._stats(session, job.id)),
                        "retryable_errors_sample": retryable_failures[:3],
                    },
                )
                await session.commit()
                # Media rows have their own attempt counter. They are retried inside
                # this worker until MAX_MEDIA_ANALYSIS_ATTEMPTS is reached and are
                # then marked failed_permanent. By default this does not fail the
                # whole job, because missing/failed media must remain visible in the
                # report while text analysis can continue.
                await asyncio.sleep(min(2.0, settings.worker_retry_base_delay_seconds))
                continue

        final_stats = await self._stats(session, job.id)
        permanent_failures = final_stats.get("media_permanent_failed", 0)
        if permanent_failures and settings.media_fail_job_on_error:
            await self.emit_event(
                session,
                job=job,
                event_type="media.analysis.failed",
                message="Medienanalyse enthält permanent fehlgeschlagene Medien; Job wird abgebrochen",
                level="error",
                payload=final_stats,
            )
            from app.services.worker_control import PermanentWorkerError
            raise PermanentWorkerError(f"{permanent_failures} media item(s) failed permanently")

        await self.emit_event(
            session,
            job=job,
            event_type="media.analysis.completed",
            message=(
                "Medienanalyse abgeschlossen"
                if not permanent_failures
                else "Medienanalyse abgeschlossen; einige Medien konnten nicht analysiert werden"
            ),
            level="warning" if permanent_failures else "info",
            payload=final_stats,
        )
        await session.commit()

        next_subject, next_key = next_subject_after_media_analysis(job)
        await self.enqueue(
            next_subject,
            {
                "job_id": str(job.id),
                "owner_user_id": str(job.owner_user_id),
                "task_key": f"{next_key}:{job.id}",
            },
        )

    async def _analyze_batch_with_cancellation_checks(
        self,
        *,
        session: AsyncSession,
        job: Job,
        rows: list[TelegramMedia],
    ) -> list[MediaWorkResult | BaseException]:
        tasks = [asyncio.create_task(self._analyze_one(row)) for row in rows]
        pending: set[asyncio.Task] = set(tasks)

        while pending:
            done, pending = await asyncio.wait(pending, timeout=2.0, return_when=asyncio.FIRST_COMPLETED)
            if not done:
                if await self.should_skip_cancelled(session, job.id):
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await self.emit_event(
                        session,
                        job=job,
                        event_type="media.analysis.cancelled",
                        message="Medienanalyse wegen Job-Abbruch beendet",
                        level="warning",
                        payload=await self._stats(session, job.id),
                    )
                    await session.commit()
                    raise WorkerCancelled("media batch cancelled")
                continue

            if await self.should_skip_cancelled(session, job.id):
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await self.emit_event(
                    session,
                    job=job,
                    event_type="media.analysis.cancelled",
                    message="Medienanalyse wegen Job-Abbruch beendet",
                    level="warning",
                    payload=await self._stats(session, job.id),
                )
                await session.commit()
                raise WorkerCancelled("media batch cancelled")

        return list(await asyncio.gather(*tasks, return_exceptions=True))

    async def _next_batch(self, session: AsyncSession, job_id: uuid.UUID) -> list[TelegramMedia]:
        result = await session.execute(
            select(TelegramMedia)
            .where(
                TelegramMedia.job_id == job_id,
                TelegramMedia.media_type.in_(MEDIA_TYPES),
                TelegramMedia.status.in_(RETRYABLE_STATUSES),
                TelegramMedia.minio_object_key.is_not(None),
            )
            .order_by(TelegramMedia.id)
            .limit(settings.media_analysis_batch_size)
        )
        return list(result.scalars().all())

    async def _mark_batch_running(self, session: AsyncSession, rows: list[TelegramMedia]) -> None:
        for row in rows:
            # Already completed rows may appear only if a previous run left status
            # stale; _persist_existing_analysis handles that. Keep attempts stable
            # until an actual vLLM request is made.
            row.status = StepStatus.running
            row.missing_reason = None
        await session.flush()

    async def _analyze_one(self, row: TelegramMedia) -> MediaWorkResult:
        async with self._semaphore:
            if not row.minio_object_key:
                return MediaWorkResult(
                    media_id=row.id,
                    status=StepStatus.failed_permanent,
                    error="media_object_missing",
                    permanent=True,
                )

            if settings.llm_mock_enabled:
                normalized = (row.media_type or "media").lower()
                return MediaWorkResult(
                    media_id=row.id,
                    status=StepStatus.completed,
                    description=(
                        f"[MOCK_{normalized.upper()}_DESCRIPTION] "
                        f"Neutrale Platzhalterbeschreibung für {row.original_path}. "
                        "Es wurde kein vLLM-Request ausgeführt."
                    ),
                    raw_response={
                        "mock": True,
                        "media_id": str(row.id),
                        "media_type": normalized,
                        "original_path": row.original_path,
                        "model": settings.vision_model,
                        "prompt_version": settings.media_analysis_prompt_version,
                    },
                )

            if (
                settings.media_analysis_transport.lower().strip() == "data_url"
                and row.size_bytes is not None
                and row.size_bytes > settings.max_inline_media_analysis_bytes
            ):
                return MediaWorkResult(
                    media_id=row.id,
                    status=StepStatus.failed_permanent,
                    error=(
                        "media_too_large_for_data_url_transport; set "
                        "MEDIA_ANALYSIS_TRANSPORT=internal_presigned_url if vLLM can access MinIO"
                    ),
                    permanent=True,
                )

            media_url = await asyncio.to_thread(
                build_media_source_url,
                object_key=row.minio_object_key,
                original_path=row.original_path,
                media_type=row.media_type,
            )
            result = await self.gateway.describe_media_with_raw(
                media_url=media_url,
                media_type=row.media_type,
                prompt=DEFAULT_MEDIA_DESCRIPTION_PROMPT,
                timeout=settings.vllm_media_request_timeout_seconds,
            )
            return MediaWorkResult(
                media_id=row.id,
                status=StepStatus.completed,
                description=result.description,
                raw_response=result.raw_response,
            )

    async def _persist_result(
        self,
        session: AsyncSession,
        job: Job,
        row: TelegramMedia,
        result: MediaWorkResult,
    ) -> str | None:
        # If a previous attempt produced an analysis but crashed before setting
        # TelegramMedia.status=completed, recover idempotently without another
        # vLLM call.
        existing = await self._existing_analysis(session, row.id)
        if existing is not None and result.status != StepStatus.completed:
            row.status = StepStatus.completed
            row.missing_reason = None
            row.analyzed_at = existing.created_at
            await session.flush()
            return None

        if result.status == StepStatus.completed:
            if existing is None:
                existing = MediaAnalysis(
                    media_id=row.id,
                    model_name=settings.vision_model,
                    prompt_version=settings.media_analysis_prompt_version,
                    description=result.description or "",
                    raw_response=result.raw_response or {},
                )
                session.add(existing)
            else:
                existing.description = result.description or existing.description
                existing.raw_response = result.raw_response or existing.raw_response

            row.status = StepStatus.completed
            row.missing_reason = None
            row.analyzed_at = datetime.now(timezone.utc)
            if row.source_media_id is not None:
                source_analysis = (
                    await session.execute(
                        select(CollectedMediaAnalysis).where(
                            CollectedMediaAnalysis.media_id == row.source_media_id,
                            CollectedMediaAnalysis.model_name == settings.vision_model,
                            CollectedMediaAnalysis.prompt_version
                            == settings.media_analysis_prompt_version,
                        )
                    )
                ).scalar_one_or_none()
                if source_analysis is None:
                    session.add(
                        CollectedMediaAnalysis(
                            media_id=row.source_media_id,
                            model_name=settings.vision_model,
                            prompt_version=settings.media_analysis_prompt_version,
                            description=existing.description,
                            raw_response=existing.raw_response,
                        )
                    )
                else:
                    source_analysis.description = existing.description
                    source_analysis.raw_response = existing.raw_response
            await session.flush()
            return None

        row.analysis_attempts = (row.analysis_attempts or 0) + 1
        error_text = result.error or "media_analysis_failed"

        if result.permanent or row.analysis_attempts >= settings.max_media_analysis_attempts:
            row.status = StepStatus.failed_permanent
            row.missing_reason = error_text
            row.analyzed_at = datetime.now(timezone.utc)
            await session.flush()
            return None

        row.status = StepStatus.failed_retryable
        row.missing_reason = error_text
        await session.flush()
        return error_text

    async def _existing_analysis(self, session: AsyncSession, media_id: uuid.UUID) -> MediaAnalysis | None:
        return (
            await session.execute(
                select(MediaAnalysis).where(
                    MediaAnalysis.media_id == media_id,
                    MediaAnalysis.model_name == settings.vision_model,
                    MediaAnalysis.prompt_version == settings.media_analysis_prompt_version,
                )
            )
        ).scalar_one_or_none()

    async def _stats(self, session: AsyncSession, job_id: uuid.UUID) -> dict[str, int]:
        total = await self._count(session, job_id)
        completed = await self._count(session, job_id, StepStatus.completed)
        pending = await self._count(session, job_id, StepStatus.pending)
        running = await self._count(session, job_id, StepStatus.running)
        retryable = await self._count(session, job_id, StepStatus.failed_retryable)
        permanent = await self._count(session, job_id, StepStatus.failed_permanent)
        return {
            "media_total": total,
            "media_done": completed,
            "media_pending": pending,
            "media_running": running,
            "media_retryable_failed": retryable,
            "media_permanent_failed": permanent,
        }

    async def _count(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        status: StepStatus | None = None,
    ) -> int:
        query = select(func.count()).select_from(TelegramMedia).where(
            TelegramMedia.job_id == job_id,
            TelegramMedia.media_type.in_(MEDIA_TYPES),
        )
        if status is not None:
            query = query.where(TelegramMedia.status == status)
        return int((await session.execute(query)).scalar_one())

    def _progress_message(self, row: TelegramMedia, result: MediaWorkResult) -> str:
        if result.status == StepStatus.completed:
            return f"Medium analysiert: {row.original_path}"
        return f"Medium konnte nicht analysiert werden: {row.original_path}"

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover - httpx is present in the app container
    httpx = None  # type: ignore[assignment]

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job, JobEvent, JobStatus, StepStatus, WorkerDeadLetter, WorkerTask
from app.services.events import record_event

settings = get_settings()


class RetryableWorkerError(Exception):
    """Raise for transient worker failures that should be retried."""


class PermanentWorkerError(Exception):
    """Raise for deterministic failures that should go to dead-letter immediately."""


class WorkerCancelled(Exception):
    """Raised internally when a job has been cancelled or reached a terminal state."""


@dataclass(slots=True)
class FailureDecision:
    retry: bool
    permanent: bool
    reason: str
    delay_seconds: int
    max_attempts: int


ACTIVE_JOB_STATUSES = {JobStatus.queued, JobStatus.running}
CANCELLING_JOB_STATUSES = {JobStatus.cancelling, JobStatus.cancelled}
TERMINAL_JOB_STATUSES = {JobStatus.cancelled, JobStatus.failed, JobStatus.completed}
NON_RUNNABLE_JOB_STATUSES = CANCELLING_JOB_STATUSES | {JobStatus.failed, JobStatus.completed}


DEFAULT_ATTEMPTS_BY_SUBJECT = {
    "jobs.ingest.validate": 1,
    "jobs.ingest.extract": 1,
    "jobs.telegram.parse": 1,
    "jobs.media.describe": 1,
    "jobs.chunk.create": 2,
    "jobs.embedding.create": 3,
    "jobs.question.retrieve": 3,
    "jobs.question.rerank": 3,
    "jobs.question.answer": 3,
    "jobs.report.render": 2,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _configured_attempt_overrides() -> dict[str, int]:
    raw = settings.worker_task_max_attempts_by_subject
    if isinstance(raw, dict):
        return {str(key): int(value) for key, value in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(key): int(value) for key, value in parsed.items()}
        except Exception:
            return {}
    return {}


def max_attempts_for_subject(subject: str) -> int:
    overrides = _configured_attempt_overrides()
    if subject in overrides:
        return max(1, int(overrides[subject]))
    if subject in DEFAULT_ATTEMPTS_BY_SUBJECT:
        return max(1, int(DEFAULT_ATTEMPTS_BY_SUBJECT[subject]))
    return max(1, int(settings.max_worker_task_attempts))


def retry_delay_seconds(attempts: int, subject: str) -> int:
    base = max(1, int(settings.worker_retry_base_delay_seconds))
    max_delay = max(base, int(settings.worker_retry_max_delay_seconds))
    # Linear backoff is sufficient for the MVP and keeps retries predictable in tests.
    return min(max_delay, base * max(1, attempts))


def is_retryable_exception(exc: Exception) -> bool:
    if isinstance(exc, RetryableWorkerError):
        return True
    if isinstance(exc, PermanentWorkerError):
        return False
    if isinstance(exc, WorkerCancelled):
        return False
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, ConnectionError)):
        return True
    if httpx is not None:
        if isinstance(exc, httpx.TimeoutException | httpx.ConnectError | httpx.NetworkError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            status_code = exc.response.status_code
            return status_code == 429 or 500 <= status_code <= 599
        if isinstance(exc, httpx.RequestError):
            return True
    return True


def classify_worker_exception(exc: Exception, attempts: int, subject: str) -> FailureDecision:
    """Return a consistent retry/dead-letter decision for all workers.

    Workers use domain exceptions to indicate deterministic permanent failures.
    Everything else is treated as transient until the subject-specific max-attempt
    limit is reached.
    """
    max_attempts = max_attempts_for_subject(subject)

    if isinstance(exc, WorkerCancelled):
        return FailureDecision(
            retry=False,
            permanent=False,
            reason="cancelled",
            delay_seconds=0,
            max_attempts=max_attempts,
        )

    if isinstance(exc, PermanentWorkerError):
        return FailureDecision(
            retry=False,
            permanent=True,
            reason="permanent_error",
            delay_seconds=0,
            max_attempts=max_attempts,
        )

    if not is_retryable_exception(exc):
        return FailureDecision(
            retry=False,
            permanent=True,
            reason="permanent_error",
            delay_seconds=0,
            max_attempts=max_attempts,
        )

    retry = attempts < max_attempts
    return FailureDecision(
        retry=retry,
        permanent=not retry,
        reason="retryable_error" if retry else "max_attempts_exceeded",
        delay_seconds=retry_delay_seconds(attempts, subject),
        max_attempts=max_attempts,
    )


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job | None:
    """Load a job with fresh status from PostgreSQL.

    Workers keep AsyncSession objects open for long loops. Without
    ``populate_existing`` SQLAlchemy can return the already-loaded Job instance
    from the identity map and hide a cancellation committed by the API process.
    Every cancellation check therefore uses this helper.
    """
    return (
        await session.execute(
            select(Job)
            .where(Job.id == job_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def has_job_event(session: AsyncSession, job_id: uuid.UUID, event_type: str) -> bool:
    result = await session.execute(
        select(func.count(JobEvent.id)).where(JobEvent.job_id == job_id, JobEvent.event_type == event_type)
    )
    return int(result.scalar() or 0) > 0


async def is_terminal_or_cancelling(session: AsyncSession, job_id: uuid.UUID) -> bool:
    job = await get_job(session, job_id)
    if job is None:
        return True
    return job.status in NON_RUNNABLE_JOB_STATUSES


async def raise_if_cancelled(session: AsyncSession, job_id: uuid.UUID) -> None:
    job = await get_job(session, job_id)
    if job is None:
        raise PermanentWorkerError(f"Job does not exist: {job_id}")
    if job.status in CANCELLING_JOB_STATUSES:
        raise WorkerCancelled(f"Job was cancelled: {job_id}")
    if job.status in {JobStatus.failed, JobStatus.completed}:
        raise WorkerCancelled(f"Job is terminal: {job_id} status={job.status.value}")


async def mark_job_cancelled(session: AsyncSession, job: Job, *, js: Any | None = None) -> None:
    """Move a non-terminal job to cancelled and emit exactly one job.cancelled event.

    This function is intentionally idempotent. It emits ``job.cancelled`` the
    first time it observes that no such event exists yet, even if the status was
    already set to ``cancelled`` by another process.
    """
    if job.status in {JobStatus.completed, JobStatus.failed}:
        return

    already_cancelled_event = await has_job_event(session, job.id, "job.cancelled")
    job.status = JobStatus.cancelled
    job.completed_at = job.completed_at or utc_now()

    if already_cancelled_event:
        return

    await record_event(
        session,
        js=js,
        job_id=job.id,
        owner_user_id=job.owner_user_id,
        event_type="job.cancelled",
        level="warning",
        message="Job wurde abgebrochen",
        payload={"job_id": str(job.id)},
    )


async def mark_job_failed(
    session: AsyncSession,
    job: Job,
    *,
    error_message: str,
    js: Any | None = None,
    event_type: str = "job.failed",
) -> None:
    if job.status in {JobStatus.completed, JobStatus.cancelled}:
        return
    job.status = JobStatus.failed
    job.error_message = error_message[:8000]
    job.completed_at = job.completed_at or utc_now()
    await record_event(
        session,
        js=js,
        job_id=job.id,
        owner_user_id=job.owner_user_id,
        event_type=event_type,
        level="error",
        message="Job ist fehlgeschlagen",
        payload={"error": error_message[:8000]},
    )


async def create_dead_letter(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    task: WorkerTask | None,
    subject: str,
    payload: dict[str, Any],
    error: Exception | str,
    reason: str,
) -> WorkerDeadLetter:
    error_text = str(error)
    dead = WorkerDeadLetter(
        job_id=job_id,
        worker_task_id=task.id if task else None,
        task_key=task.task_key if task else payload.get("task_key", f"{subject}:{job_id}"),
        subject=subject,
        attempts=task.attempts if task else 0,
        reason=reason,
        error_message=error_text[:16000],
        payload=payload,
    )
    session.add(dead)
    await session.flush()
    return dead


async def count_worker_backlog(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(WorkerTask.id)).where(
            WorkerTask.status.in_([StepStatus.pending, StepStatus.running, StepStatus.failed_retryable])
        )
    )
    return int(result.scalar() or 0)

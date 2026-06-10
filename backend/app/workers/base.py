import abc
import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from nats.errors import TimeoutError as NATSTimeoutError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import Job, JobStatus, StepStatus, WorkerTask
from app.nats_client import connect_nats, ensure_streams, publish_json
from app.services.events import record_event
from app.services.worker_control import (
    NON_RUNNABLE_JOB_STATUSES,
    PermanentWorkerError,
    RetryableWorkerError,
    WorkerCancelled,
    classify_worker_exception,
    create_dead_letter,
    get_job,
    mark_job_cancelled,
    mark_job_failed,
    raise_if_cancelled as raise_if_cancelled_fresh,
)

settings = get_settings()
AckAction = Literal["ack", "nak"]


class Worker(abc.ABC):
    subject: str
    durable: str
    queue: str
    fetch_batch_size = 8
    fetch_timeout_seconds = 1
    idle_sleep_seconds = 0.25
    ack_heartbeat_seconds = 10

    def __init__(self) -> None:
        self.nc = None
        self.js = None

    @abc.abstractmethod
    async def handle(self, session: AsyncSession, payload: dict[str, Any]) -> None:
        ...

    async def get_job_fresh(self, session: AsyncSession, job_id: uuid.UUID) -> Job | None:
        return await get_job(session, job_id)

    async def should_skip_cancelled(self, session: AsyncSession, job_id: uuid.UUID) -> bool:
        """Return True when a worker must not continue work for this job.

        This performs a fresh DB read, so API-side cancellation is noticed even
        inside long worker loops that already loaded the Job object earlier.
        """
        job = await self.get_job_fresh(session, job_id)
        if not job:
            return True
        if job.status in {JobStatus.cancelling, JobStatus.cancelled}:
            await mark_job_cancelled(session, job, js=self.js)
            return True
        return job.status in {JobStatus.failed, JobStatus.completed}

    async def raise_if_cancelled(self, session: AsyncSession, job_id: uuid.UUID) -> None:
        await raise_if_cancelled_fresh(session, job_id)

    async def checkpoint_cancelled(
        self,
        session: AsyncSession,
        job: Job,
        *,
        event_type: str | None = None,
        message: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Raise WorkerCancelled if cancellation/terminal state was requested.

        Long-running workers call this between batches and after slow external
        operations. If the job was cancelled, a stage-specific cancellation event
        can be emitted before the common worker base marks the task skipped.
        """
        fresh_job = await self.get_job_fresh(session, job.id)
        if fresh_job is None:
            raise PermanentWorkerError(f"Job not found: {job.id}")
        if fresh_job.status in {JobStatus.cancelling, JobStatus.cancelled}:
            if event_type and message:
                await self.emit_event(
                    session,
                    job=fresh_job,
                    event_type=event_type,
                    message=message,
                    payload=payload or {},
                    level="warning",
                )
            raise WorkerCancelled(f"Job was cancelled: {job.id}")
        if fresh_job.status in {JobStatus.failed, JobStatus.completed}:
            raise WorkerCancelled(f"Job is terminal: {job.id} status={fresh_job.status.value}")

    async def _fetch_messages(self, sub: Any) -> list[Any]:
        """Fetch a small pull-consumer batch.

        nats-py raises ``nats.errors.TimeoutError`` when a pull subscription has no
        available messages within the requested timeout. That is the normal idle
        condition for a worker and must not terminate the process.
        """
        try:
            return await sub.fetch(
                batch=self.fetch_batch_size,
                timeout=self.fetch_timeout_seconds,
            )
        except (NATSTimeoutError, asyncio.TimeoutError):
            return []

    async def run_forever(self) -> None:
        self.nc = await connect_nats()
        self.js = self.nc.jetstream()
        await ensure_streams(self.js)

        sub = await self.js.pull_subscribe(
            self.subject,
            durable=self.durable,
            stream="CHAT_ANALYSE_TASKS",
        )
        print(f"Worker subscribed: {self.subject} durable={self.durable}", flush=True)

        while True:
            messages = await self._fetch_messages(sub)
            if not messages:
                await asyncio.sleep(self.idle_sleep_seconds)
                continue

            # Each fetched message is checked independently before work begins.
            # A cancelled job's pending tasks are acked and skipped instead of
            # being executed or redelivered indefinitely.
            for msg in messages:
                heartbeat_task = asyncio.create_task(self._keep_message_alive(msg))
                try:
                    payload = json.loads(msg.data.decode("utf-8"))
                    action = await self._handle_message(payload)
                    if action == "nak":
                        delay = int(payload.get("retry_delay_seconds") or settings.worker_retry_base_delay_seconds)
                        await msg.nak(delay=delay)
                    else:
                        await msg.ack()
                except Exception as exc:
                    # Last-resort guard: the worker process must not die because a
                    # single task failed unexpectedly before it could be recorded.
                    print(f"Worker fatal task error subject={self.subject}: {exc}", flush=True)
                    try:
                        await msg.nak(delay=settings.worker_retry_base_delay_seconds)
                    except Exception as nak_exc:
                        print(f"Could not nak message subject={self.subject}: {nak_exc}", flush=True)
                finally:
                    heartbeat_task.cancel()
                    try:
                        await heartbeat_task
                    except asyncio.CancelledError:
                        pass
            await asyncio.sleep(0)

    async def _keep_message_alive(self, msg: Any) -> None:
        """Extend JetStream ack wait while long-running handlers work."""
        while True:
            await asyncio.sleep(self.ack_heartbeat_seconds)
            try:
                await msg.in_progress()
            except Exception as exc:
                print(f"Could not extend ack deadline subject={self.subject}: {exc}", flush=True)

    async def _handle_message(self, payload: dict[str, Any]) -> AckAction:
        job_id = uuid.UUID(payload["job_id"])
        task_key = payload.get("task_key") or f"{self.subject}:{job_id}"

        async with SessionLocal() as session:
            job = await get_job(session, job_id)
            if job is None:
                exc = PermanentWorkerError(f"Job not found: {job_id}")
                await publish_json(self.js, f"dlq.{self.subject}", {**payload, "error": str(exc), "reason": "job_not_found"})
                return "ack"

            result = await session.execute(select(WorkerTask).where(WorkerTask.task_key == task_key))
            task = result.scalar_one_or_none()
            if task and task.status in {StepStatus.completed, StepStatus.skipped, StepStatus.failed_permanent}:
                return "ack"

            if not task:
                task = WorkerTask(
                    task_key=task_key,
                    job_id=job_id,
                    subject=self.subject,
                    status=StepStatus.running,
                    attempts=1,
                )
                session.add(task)
            else:
                task.status = StepStatus.running
                task.attempts += 1
            task.updated_at = datetime.now(timezone.utc)
            await session.flush()

            # Cancellation/terminal check before the worker starts this task.
            job = await get_job(session, job_id)
            if job is None:
                raise PermanentWorkerError(f"Job not found after task creation: {job_id}")

            if job.status in {JobStatus.cancelling, JobStatus.cancelled}:
                task.status = StepStatus.skipped
                task.last_error = "job_cancelled_before_start"
                task.updated_at = datetime.now(timezone.utc)
                await mark_job_cancelled(session, job, js=self.js)
                await session.commit()
                return "ack"

            if job.status in {JobStatus.failed, JobStatus.completed}:
                task.status = StepStatus.skipped
                task.last_error = f"job_terminal_before_start:{job.status.value}"
                task.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return "ack"

            # Persist the attempt before entering the handler. Handlers may fail
            # before their first commit; without this commit the rollback below
            # removes the task row/increment and every redelivery becomes
            # "attempt 1" forever.
            await session.commit()

            try:
                await self.handle(session, payload)
                await session.flush()
                job = await get_job(session, job_id)
                if job is None:
                    raise PermanentWorkerError(f"Job not found after handler: {job_id}")

                if job.status == JobStatus.cancelled:
                    task.status = StepStatus.skipped
                    task.last_error = "job_cancelled"
                elif job.status == JobStatus.failed:
                    # The handler intentionally failed the job, for example
                    # because the ZIP was invalid. The worker task itself ran
                    # to a deterministic conclusion and must not be retried, but
                    # the failure is still persisted as a dead-letter so the UI
                    # and operators see it in one consistent place.
                    task.status = StepStatus.failed_permanent
                    task.last_error = job.error_message
                    dead = await create_dead_letter(
                        session,
                        job_id=job_id,
                        task=task,
                        subject=self.subject,
                        payload=payload,
                        error=job.error_message or "job_failed_by_handler",
                        reason="handler_marked_job_failed",
                    )
                    await self.emit_event(
                        session,
                        job=job,
                        event_type="worker.task.dead_letter",
                        level="error",
                        message=f"Task endgültig fehlgeschlagen: {self.subject}",
                        payload={
                            "subject": self.subject,
                            "task_key": task_key,
                            "attempts": task.attempts,
                            "reason": "handler_marked_job_failed",
                            "dead_letter_id": str(dead.id),
                            "error": (job.error_message or "job_failed_by_handler")[:4000],
                        },
                    )
                else:
                    task.status = StepStatus.completed
                    task.last_error = None
                task.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return "ack"
            except Exception as exc:
                await session.rollback()
                return await self._record_failure(job_id, task_key, payload, exc)

    def fail_job_on_dead_letter(self, payload: dict[str, Any], exc: Exception, reason: str) -> bool:
        """Whether a permanent task failure should fail the whole job.

        Most pipeline stages are job-critical. Specialized workers can override
        this when a task failure should be visible but non-fatal.
        """
        return True

    async def _record_failure(
        self,
        job_id: uuid.UUID,
        task_key: str,
        payload: dict[str, Any],
        exc: Exception,
    ) -> AckAction:
        async with SessionLocal() as session:
            task = (await session.execute(select(WorkerTask).where(WorkerTask.task_key == task_key))).scalar_one_or_none()
            job = await get_job(session, job_id)
            attempts = task.attempts if task else 1
            decision = classify_worker_exception(exc, attempts, self.subject)

            if isinstance(exc, WorkerCancelled) or decision.reason == "cancelled":
                if task is not None:
                    task.status = StepStatus.skipped
                    task.last_error = str(exc)
                    task.updated_at = datetime.now(timezone.utc)
                if job is not None:
                    await mark_job_cancelled(session, job, js=self.js)
                await session.commit()
                return "ack"

            if task is not None:
                task.status = StepStatus.failed_retryable if decision.retry else StepStatus.failed_permanent
                task.last_error = str(exc)[:16000]
                task.updated_at = datetime.now(timezone.utc)

            if decision.retry:
                print(
                    f"Worker task retrying subject={self.subject} "
                    f"task_key={task_key} attempts={attempts}/{decision.max_attempts} "
                    f"delay={decision.delay_seconds}s error={exc}",
                    flush=True,
                )
                if job is not None:
                    await self.emit_event(
                        session,
                        job=job,
                        event_type="worker.task.retrying",
                        level="warning",
                        message=f"Task wird erneut versucht: {self.subject}: {str(exc)[:500]}",
                        payload={
                            "subject": self.subject,
                            "task_key": task_key,
                            "attempts": attempts,
                            "max_attempts": decision.max_attempts,
                            "retry_delay_seconds": decision.delay_seconds,
                            "error": str(exc)[:4000],
                        },
                    )
                await session.commit()
                payload["retry_delay_seconds"] = decision.delay_seconds
                return "nak"

            dead = await create_dead_letter(
                session,
                job_id=job_id,
                task=task,
                subject=self.subject,
                payload=payload,
                error=exc,
                reason=decision.reason,
            )
            print(
                f"Worker task failed permanently subject={self.subject} "
                f"task_key={task_key} attempts={attempts}/{decision.max_attempts} "
                f"reason={decision.reason} error={exc}",
                flush=True,
            )
            if job is not None:
                await self.emit_event(
                    session,
                    job=job,
                    event_type="worker.task.dead_letter",
                    level="error",
                    message=f"Task endgültig fehlgeschlagen: {self.subject}",
                    payload={
                        "subject": self.subject,
                        "task_key": task_key,
                        "attempts": attempts,
                        "max_attempts": decision.max_attempts,
                        "reason": decision.reason,
                        "dead_letter_id": str(dead.id),
                        "error": str(exc)[:4000],
                    },
                )
                if self.fail_job_on_dead_letter(payload, exc, decision.reason):
                    await mark_job_failed(
                        session,
                        job,
                        js=self.js,
                        error_message=f"Worker task failed permanently: {self.subject}: {exc}",
                    )
            await session.commit()
            await publish_json(
                self.js,
                f"dlq.{self.subject}",
                {
                    **payload,
                    "subject": self.subject,
                    "task_key": task_key,
                    "attempts": attempts,
                    "reason": decision.reason,
                    "error": str(exc),
                },
            )
            return "ack"

    async def emit_event(
        self,
        session: AsyncSession,
        *,
        job: Job,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        level: str = "info",
    ) -> None:
        await record_event(
            session,
            js=self.js,
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            event_type=event_type,
            message=message,
            payload=payload,
            level=level,
        )

    async def enqueue(self, subject: str, payload: dict[str, Any]) -> bool:
        """Publish the next worker task unless the job was cancelled/terminal.

        This guards every pipeline transition. Even if an individual worker
        forgets to check immediately before calling ``enqueue``, a cancellation
        committed by the API is respected here and the next subject is not
        published.
        """
        raw_job_id = payload.get("job_id")
        if raw_job_id:
            job_id = uuid.UUID(str(raw_job_id))
            async with SessionLocal() as session:
                job = await get_job(session, job_id)
                if job is None:
                    return False
                if job.status in NON_RUNNABLE_JOB_STATUSES:
                    if job.status in {JobStatus.cancelling, JobStatus.cancelled}:
                        await mark_job_cancelled(session, job, js=self.js)
                        await session.commit()
                    return False

        await publish_json(self.js, subject, payload)
        return True

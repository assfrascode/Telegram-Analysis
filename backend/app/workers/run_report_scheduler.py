import asyncio
import os
import socket
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    Job,
    JobStatus,
    QuestionSet,
    TelegramChat,
    TelegramChatStatus,
    TelegramReportSchedule,
)
from app.nats_client import nats_context
from app.schemas import JobOptions, TelegramReportCreateRequest
from app.services.capacity import capacity_snapshot
from app.services.jobs import (
    create_telegram_job_record,
    mark_job_start_failed_db_only,
    publish_initial_job_task,
)
from app.services.report_schedules import calculate_next_run_at

settings = get_settings()
SCHEDULER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"
ACTIVE_JOB_STATUSES = {JobStatus.queued, JobStatus.running, JobStatus.cancelling}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def claim_due_schedule() -> uuid.UUID | None:
    now = utc_now()
    async with SessionLocal() as session:
        schedule = (
            await session.execute(
                select(TelegramReportSchedule)
                .where(
                    TelegramReportSchedule.enabled.is_(True),
                    TelegramReportSchedule.next_run_at.is_not(None),
                    TelegramReportSchedule.next_run_at <= now,
                    or_(
                        TelegramReportSchedule.lease_expires_at.is_(None),
                        TelegramReportSchedule.lease_expires_at < now,
                    ),
                )
                .order_by(TelegramReportSchedule.next_run_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalar_one_or_none()
        if schedule is None:
            return None

        schedule.lease_owner = SCHEDULER_ID
        schedule.lease_expires_at = now + timedelta(minutes=settings.report_scheduler_lease_minutes)
        schedule.updated_at = now
        await session.commit()
        return schedule.id


async def previous_job_is_active(session, schedule: TelegramReportSchedule) -> bool:
    if schedule.last_job_id is None:
        return False
    job = await session.get(Job, schedule.last_job_id)
    return bool(job and job.status in ACTIVE_JOB_STATUSES)


def question_set_options(question_set: QuestionSet) -> JobOptions:
    return JobOptions(
        translate=bool(question_set.default_translate),
        analyze_media=bool(question_set.default_analyze_media),
        retrieval_k=question_set.default_retrieval_k or settings.default_retrieval_k,
        rerank_k=question_set.default_rerank_k or settings.default_rerank_k,
    )


def scheduled_report_metadata(
    schedule: TelegramReportSchedule,
    *,
    scheduled_for: datetime,
) -> dict[str, str | int]:
    return {
        "schedule_id": str(schedule.id),
        "scheduled_for": scheduled_for.isoformat(),
        "rolling_window_days": schedule.rolling_window_days,
        "timezone": schedule.timezone,
        "run_time_local": schedule.run_time_local,
        "question_set_id": str(schedule.question_set_id),
    }


def release_schedule(schedule: TelegramReportSchedule) -> None:
    schedule.lease_owner = None
    schedule.lease_expires_at = None
    schedule.updated_at = utc_now()


def defer_schedule(schedule: TelegramReportSchedule, reason: str) -> None:
    now = utc_now()
    schedule.last_error = reason[:4000]
    schedule.lease_owner = f"defer:{SCHEDULER_ID}"
    schedule.lease_expires_at = now + timedelta(seconds=max(10, settings.report_scheduler_poll_seconds))
    schedule.updated_at = now


def disable_schedule(schedule: TelegramReportSchedule, reason: str) -> None:
    schedule.enabled = False
    schedule.next_run_at = None
    schedule.last_error = reason[:4000]
    release_schedule(schedule)


async def process_schedule(schedule_id: uuid.UUID) -> uuid.UUID | None:
    async with SessionLocal() as session:
        schedule = await session.get(TelegramReportSchedule, schedule_id)
        if schedule is None or schedule.lease_owner != SCHEDULER_ID:
            return None
        if not schedule.enabled or schedule.next_run_at is None:
            release_schedule(schedule)
            await session.commit()
            return None

        scheduled_for = schedule.next_run_at
        if await previous_job_is_active(session, schedule):
            defer_schedule(schedule, "Previous scheduled report is still queued or running")
            await session.commit()
            return None

        try:
            capacity = await capacity_snapshot(session)
        except Exception as exc:
            defer_schedule(schedule, f"Capacity check failed: {exc}")
            await session.commit()
            return None
        if not capacity.get("accepting_jobs", False):
            blockers = ", ".join(capacity.get("blockers") or ["capacity_unavailable"])
            defer_schedule(schedule, f"System is not accepting new analyses: {blockers}")
            await session.commit()
            return None

        chat = await session.get(TelegramChat, schedule.telegram_chat_id)
        if chat is None or chat.owner_user_id != schedule.owner_user_id:
            disable_schedule(schedule, "Scheduled Telegram chat is no longer available")
            await session.commit()
            return None
        if chat.status == TelegramChatStatus.archived:
            disable_schedule(schedule, "Scheduled Telegram chat is archived")
            await session.commit()
            return None

        question_set = await session.get(QuestionSet, schedule.question_set_id)
        if (
            question_set is None
            or question_set.owner_user_id != schedule.owner_user_id
            or question_set.archived_at is not None
        ):
            disable_schedule(schedule, "Scheduled question set is no longer available")
            await session.commit()
            return None

        payload = TelegramReportCreateRequest(
            telegram_chat_id=schedule.telegram_chat_id,
            start_at=scheduled_for - timedelta(days=schedule.rolling_window_days),
            end_at=scheduled_for,
            question_set_id=schedule.question_set_id,
            options=question_set_options(question_set),
        )
        try:
            job = await create_telegram_job_record(session, schedule.owner_user_id, payload)
        except HTTPException as exc:
            disable_schedule(schedule, str(exc.detail))
            await session.commit()
            return None

        job.options = {
            **(job.options or {}),
            "scheduled_report": scheduled_report_metadata(schedule, scheduled_for=scheduled_for),
        }
        schedule.last_job_id = job.id
        schedule.last_run_at = utc_now()
        schedule.last_error = None
        schedule.next_run_at = calculate_next_run_at(
            schedule.run_time_local,
            schedule.timezone,
            now=utc_now(),
        )
        release_schedule(schedule)
        await session.commit()

        try:
            async with nats_context() as (_, js):
                await publish_initial_job_task(session, js, job)
                await session.commit()
        except Exception as exc:
            await session.rollback()
            await mark_job_start_failed_db_only(session, job.id, exc)
            schedule = await session.get(TelegramReportSchedule, schedule_id)
            if schedule is not None:
                schedule.last_error = f"Analysis job could not be enqueued: {exc}"[:4000]
                release_schedule(schedule)
            await session.commit()
            return None

        print(
            f"Scheduled Telegram report created schedule_id={schedule_id} "
            f"job_id={job.id} scheduled_for={scheduled_for.isoformat()}",
            flush=True,
        )
        return job.id


async def record_schedule_failure(schedule_id: uuid.UUID, exc: Exception) -> None:
    async with SessionLocal() as session:
        schedule = await session.get(TelegramReportSchedule, schedule_id)
        if schedule is None or schedule.lease_owner != SCHEDULER_ID:
            return
        defer_schedule(schedule, str(exc) or exc.__class__.__name__)
        await session.commit()


async def process_safely(schedule_id: uuid.UUID) -> None:
    try:
        await process_schedule(schedule_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(
            f"Scheduled Telegram report failed schedule_id={schedule_id}: "
            f"{exc.__class__.__name__}: {exc}",
            flush=True,
        )
        traceback.print_exc()
        await record_schedule_failure(schedule_id, exc)


async def main() -> None:
    await init_db()
    print(
        f"Report scheduler started id={SCHEDULER_ID} "
        f"poll_interval={settings.report_scheduler_poll_seconds}s",
        flush=True,
    )
    while True:
        schedule_id = await claim_due_schedule()
        if schedule_id is None:
            await asyncio.sleep(settings.report_scheduler_poll_seconds)
            continue
        await process_safely(schedule_id)


if __name__ == "__main__":
    asyncio.run(main())

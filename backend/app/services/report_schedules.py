import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, status
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    QuestionSet,
    TelegramChat,
    TelegramChatStatus,
    TelegramReportSchedule,
)
from app.schemas import (
    TelegramReportScheduleCreateRequest,
    TelegramReportScheduleResponse,
    TelegramReportScheduleUpdateRequest,
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def parse_run_time(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":", 1))
    return time(hour=hour, minute=minute)


def calculate_next_run_at(
    run_time_local: str,
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> datetime:
    reference = ensure_utc(now or utc_now())
    zone = ZoneInfo(timezone_name)
    local_now = reference.astimezone(zone)
    run_time = parse_run_time(run_time_local)
    candidate = local_now.replace(
        hour=run_time.hour,
        minute=run_time.minute,
        second=0,
        microsecond=0,
    )
    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def response(schedule: TelegramReportSchedule) -> TelegramReportScheduleResponse:
    return TelegramReportScheduleResponse(
        id=schedule.id,
        telegram_chat_id=schedule.telegram_chat_id,
        question_set_id=schedule.question_set_id,
        enabled=schedule.enabled,
        allow_partial_telegram_sync=bool(getattr(schedule, "allow_partial_telegram_sync", False)),
        run_time_local=schedule.run_time_local,
        timezone=schedule.timezone,
        rolling_window_days=schedule.rolling_window_days,
        next_run_at=schedule.next_run_at,
        last_run_at=schedule.last_run_at,
        last_job_id=schedule.last_job_id,
        last_error=schedule.last_error,
        created_at=schedule.created_at,
        updated_at=schedule.updated_at,
    )


async def _load_owned_active_chat(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    chat_id: uuid.UUID,
) -> TelegramChat:
    chat = (
        await session.execute(
            select(TelegramChat).where(
                TelegramChat.id == chat_id,
                TelegramChat.owner_user_id == owner_user_id,
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if chat.status == TelegramChatStatus.archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Telegram chat is archived")
    return chat


async def _load_owned_active_question_set(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    question_set_id: uuid.UUID,
) -> QuestionSet:
    question_set = (
        await session.execute(
            select(QuestionSet).where(
                QuestionSet.id == question_set_id,
                QuestionSet.owner_user_id == owner_user_id,
                QuestionSet.archived_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if question_set is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return question_set


async def get_owned_report_schedule(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    schedule_id: uuid.UUID,
) -> TelegramReportSchedule:
    schedule = (
        await session.execute(
            select(TelegramReportSchedule).where(
                TelegramReportSchedule.id == schedule_id,
                TelegramReportSchedule.owner_user_id == owner_user_id,
            )
        )
    ).scalar_one_or_none()
    if schedule is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return schedule


async def list_report_schedules(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
) -> list[TelegramReportScheduleResponse]:
    schedules = (
        await session.execute(
            select(TelegramReportSchedule)
            .where(TelegramReportSchedule.owner_user_id == owner_user_id)
            .order_by(desc(TelegramReportSchedule.created_at))
        )
    ).scalars().all()
    return [response(schedule) for schedule in schedules]


async def create_report_schedule(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    payload: TelegramReportScheduleCreateRequest,
) -> TelegramReportSchedule:
    await _load_owned_active_chat(session, owner_user_id=owner_user_id, chat_id=payload.telegram_chat_id)
    await _load_owned_active_question_set(
        session,
        owner_user_id=owner_user_id,
        question_set_id=payload.question_set_id,
    )
    schedule = TelegramReportSchedule(
        owner_user_id=owner_user_id,
        telegram_chat_id=payload.telegram_chat_id,
        question_set_id=payload.question_set_id,
        run_time_local=payload.run_time_local,
        timezone=payload.timezone,
        rolling_window_days=payload.rolling_window_days,
        enabled=payload.enabled,
        allow_partial_telegram_sync=payload.allow_partial_telegram_sync,
        next_run_at=(
            calculate_next_run_at(payload.run_time_local, payload.timezone)
            if payload.enabled
            else None
        ),
    )
    session.add(schedule)
    await session.flush()
    return schedule


async def update_report_schedule(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    schedule: TelegramReportSchedule,
    payload: TelegramReportScheduleUpdateRequest,
) -> TelegramReportSchedule:
    should_recalculate = False

    if payload.telegram_chat_id is not None:
        await _load_owned_active_chat(session, owner_user_id=owner_user_id, chat_id=payload.telegram_chat_id)
        schedule.telegram_chat_id = payload.telegram_chat_id

    if payload.question_set_id is not None:
        await _load_owned_active_question_set(
            session,
            owner_user_id=owner_user_id,
            question_set_id=payload.question_set_id,
        )
        schedule.question_set_id = payload.question_set_id

    if payload.run_time_local is not None:
        schedule.run_time_local = payload.run_time_local
        should_recalculate = True
    if payload.timezone is not None:
        schedule.timezone = payload.timezone
        should_recalculate = True
    if payload.rolling_window_days is not None:
        schedule.rolling_window_days = payload.rolling_window_days
    if payload.enabled is not None:
        schedule.enabled = payload.enabled
        should_recalculate = True
    if payload.allow_partial_telegram_sync is not None:
        schedule.allow_partial_telegram_sync = payload.allow_partial_telegram_sync

    if schedule.enabled and (should_recalculate or schedule.next_run_at is None):
        schedule.next_run_at = calculate_next_run_at(schedule.run_time_local, schedule.timezone)
    elif not schedule.enabled:
        schedule.next_run_at = None

    schedule.last_error = None
    schedule.lease_owner = None
    schedule.lease_expires_at = None
    schedule.updated_at = utc_now()
    await session.flush()
    return schedule


async def delete_report_schedule(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    schedule_id: uuid.UUID,
) -> None:
    schedule = await get_owned_report_schedule(
        session,
        owner_user_id=owner_user_id,
        schedule_id=schedule_id,
    )
    await session.execute(delete(TelegramReportSchedule).where(TelegramReportSchedule.id == schedule.id))
    await session.flush()

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models import Job, JobStatus, QuestionSet, TelegramChat, TelegramChatStatus, TelegramReportSchedule
from app.schemas import (
    TelegramReportScheduleCreateRequest,
    TelegramReportScheduleResponse,
    TelegramReportScheduleUpdateRequest,
)
from app.services.report_schedules import calculate_next_run_at, response as schedule_response
from app.workers import run_report_scheduler


def test_report_schedule_schema_validates_time_timezone_and_window() -> None:
    default_request = TelegramReportScheduleCreateRequest(
        telegram_chat_id=uuid.uuid4(),
        question_set_id=uuid.uuid4(),
        run_time_local="05:00",
        timezone="Europe/Berlin",
        rolling_window_days=1,
    )
    assert default_request.allow_partial_telegram_sync is False

    update_request = TelegramReportScheduleUpdateRequest(allow_partial_telegram_sync=True)
    assert update_request.allow_partial_telegram_sync is True

    with pytest.raises(ValidationError):
        TelegramReportScheduleCreateRequest(
            telegram_chat_id=uuid.uuid4(),
            question_set_id=uuid.uuid4(),
            run_time_local="25:00",
            timezone="Europe/Berlin",
            rolling_window_days=1,
        )

    with pytest.raises(ValidationError):
        TelegramReportScheduleCreateRequest(
            telegram_chat_id=uuid.uuid4(),
            question_set_id=uuid.uuid4(),
            run_time_local="05:00",
            timezone="Not/AZone",
            rolling_window_days=1,
        )

    with pytest.raises(ValidationError):
        TelegramReportScheduleCreateRequest(
            telegram_chat_id=uuid.uuid4(),
            question_set_id=uuid.uuid4(),
            run_time_local="05:00",
            timezone="Europe/Berlin",
            rolling_window_days=2,
        )


def test_next_run_uses_local_wall_clock_timezone() -> None:
    same_day = calculate_next_run_at(
        "05:00",
        "Europe/Berlin",
        now=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc),
    )
    assert same_day == datetime(2026, 1, 1, 4, 0, tzinfo=timezone.utc)

    next_day = calculate_next_run_at(
        "05:00",
        "Europe/Berlin",
        now=datetime(2026, 1, 1, 5, 30, tzinfo=timezone.utc),
    )
    assert next_day == datetime(2026, 1, 2, 4, 0, tzinfo=timezone.utc)


def test_recurring_run_uses_window_days_and_preserves_wall_clock_across_dst() -> None:
    previous = datetime(2026, 3, 21, 4, 0, tzinfo=timezone.utc)

    next_run = calculate_next_run_at(
        "05:00",
        "Europe/Berlin",
        interval_days=14,
        now=datetime(2026, 3, 22, 12, 0, tzinfo=timezone.utc),
        previous_scheduled_for=previous,
    )

    assert next_run == datetime(2026, 4, 4, 3, 0, tzinfo=timezone.utc)


def test_recurring_run_skips_elapsed_intervals_without_drifting() -> None:
    next_run = calculate_next_run_at(
        "05:00",
        "Europe/Berlin",
        interval_days=14,
        now=datetime(2026, 4, 20, 12, 0, tzinfo=timezone.utc),
        previous_scheduled_for=datetime(2026, 3, 21, 4, 0, tzinfo=timezone.utc),
    )

    assert next_run == datetime(2026, 5, 2, 3, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("interval_days", "expected_day"),
    [(1, 2), (7, 8), (14, 15), (30, 31)],
)
def test_each_report_window_preset_is_also_the_recurrence(
    interval_days: int,
    expected_day: int,
) -> None:
    next_run = calculate_next_run_at(
        "05:00",
        "UTC",
        interval_days=interval_days,
        now=datetime(2026, 1, 1, 6, 0, tzinfo=timezone.utc),
        previous_scheduled_for=datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc),
    )

    assert next_run == datetime(2026, 1, expected_day, 5, 0, tzinfo=timezone.utc)


class FakeSession:
    def __init__(self, *, schedule, chat=None, question_set=None, previous_job=None):
        self.schedule = schedule
        self.chat = chat
        self.question_set = question_set
        self.previous_job = previous_job
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, model, value):
        if model is TelegramReportSchedule:
            return self.schedule
        if model is Job:
            return self.previous_job
        if model is TelegramChat:
            return self.chat
        if model is QuestionSet:
            return self.question_set
        return None

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


def patch_session(monkeypatch, session: FakeSession) -> None:
    monkeypatch.setattr(run_report_scheduler, "SessionLocal", lambda: session)


def make_schedule(**overrides):
    values = {
        "id": uuid.uuid4(),
        "owner_user_id": uuid.uuid4(),
        "telegram_chat_id": uuid.uuid4(),
        "question_set_id": uuid.uuid4(),
        "run_time_local": "05:00",
        "timezone": "UTC",
        "rolling_window_days": 7,
        "allow_partial_telegram_sync": False,
        "enabled": True,
        "next_run_at": datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc),
        "last_job_id": None,
        "last_run_at": None,
        "last_error": None,
        "lease_owner": run_report_scheduler.SCHEDULER_ID,
        "lease_expires_at": datetime(2026, 1, 1, 5, 5, tzinfo=timezone.utc),
        "created_at": datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_scheduler_defers_when_previous_job_is_active(monkeypatch) -> None:
    previous_job_id = uuid.uuid4()
    schedule = make_schedule(last_job_id=previous_job_id)
    previous_job = SimpleNamespace(id=previous_job_id, status=JobStatus.running)
    session = FakeSession(schedule=schedule, previous_job=previous_job)
    patch_session(monkeypatch, session)

    async def fail_capacity(session):
        raise AssertionError("capacity should not be checked while previous job is active")

    monkeypatch.setattr(run_report_scheduler, "capacity_snapshot", fail_capacity)

    result = asyncio.run(run_report_scheduler.process_schedule(schedule.id))

    assert result is None
    assert "Previous scheduled report" in schedule.last_error
    assert schedule.lease_owner.startswith("defer:")
    assert schedule.next_run_at == datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)


def test_scheduler_defers_when_capacity_is_full(monkeypatch) -> None:
    schedule = make_schedule()
    session = FakeSession(schedule=schedule)
    patch_session(monkeypatch, session)

    async def full_capacity(session):
        return {"accepting_jobs": False, "blockers": ["max_active_jobs_reached"]}

    monkeypatch.setattr(run_report_scheduler, "capacity_snapshot", full_capacity)

    result = asyncio.run(run_report_scheduler.process_schedule(schedule.id))

    assert result is None
    assert "max_active_jobs_reached" in schedule.last_error
    assert schedule.lease_owner.startswith("defer:")
    assert schedule.last_job_id is None


def test_scheduler_creates_job_with_rolling_window_and_live_question_set(monkeypatch) -> None:
    owner_id = uuid.uuid4()
    chat_id = uuid.uuid4()
    question_set_id = uuid.uuid4()
    schedule = make_schedule(
        owner_user_id=owner_id,
        telegram_chat_id=chat_id,
        question_set_id=question_set_id,
        rolling_window_days=14,
        allow_partial_telegram_sync=True,
    )
    chat = SimpleNamespace(id=chat_id, owner_user_id=owner_id, status=TelegramChatStatus.active)
    question_set = SimpleNamespace(
        id=question_set_id,
        owner_user_id=owner_id,
        archived_at=None,
        default_translate=True,
        default_analyze_media=False,
        default_retrieval_k=40,
        default_rerank_k=10,
    )
    session = FakeSession(schedule=schedule, chat=chat, question_set=question_set)
    patch_session(monkeypatch, session)
    created_jobs = []
    published_jobs = []

    async def accepting_capacity(session):
        return {"accepting_jobs": True, "blockers": []}

    async def create_job(session, owner_user_id, payload):
        assert owner_user_id == owner_id
        assert payload.telegram_chat_id == chat_id
        assert payload.question_set_id == question_set_id
        assert payload.start_at == datetime(2025, 12, 18, 5, 0, tzinfo=timezone.utc)
        assert payload.end_at == datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc)
        assert payload.options.translate is True
        assert payload.options.analyze_media is False
        assert payload.options.allow_partial_telegram_sync is True
        assert payload.options.retrieval_k == 40
        assert payload.options.rerank_k == 10
        job = SimpleNamespace(id=uuid.uuid4(), options={})
        created_jobs.append(job)
        return job

    async def publish_task(session, js, job):
        published_jobs.append(job.id)

    @asynccontextmanager
    async def fake_nats_context():
        yield None, SimpleNamespace()

    monkeypatch.setattr(run_report_scheduler, "capacity_snapshot", accepting_capacity)
    monkeypatch.setattr(run_report_scheduler, "create_telegram_job_record", create_job)
    monkeypatch.setattr(run_report_scheduler, "publish_initial_job_task", publish_task)
    monkeypatch.setattr(run_report_scheduler, "nats_context", fake_nats_context)
    monkeypatch.setattr(
        run_report_scheduler,
        "utc_now",
        lambda: datetime(2026, 1, 1, 5, 1, tzinfo=timezone.utc),
    )

    result = asyncio.run(run_report_scheduler.process_schedule(schedule.id))

    assert result == created_jobs[0].id
    assert published_jobs == [created_jobs[0].id]
    assert schedule.last_job_id == created_jobs[0].id
    assert schedule.last_error is None
    assert schedule.next_run_at == datetime(2026, 1, 15, 5, 0, tzinfo=timezone.utc)
    metadata = created_jobs[0].options["scheduled_report"]
    assert metadata["schedule_id"] == str(schedule.id)
    assert metadata["scheduled_for"] == "2026-01-01T05:00:00+00:00"
    assert metadata["rolling_window_days"] == 14
    assert metadata["allow_partial_telegram_sync"] is True


def test_report_schedule_response_exposes_partial_flag() -> None:
    schedule = make_schedule(allow_partial_telegram_sync=True)

    result = schedule_response(schedule)

    assert isinstance(result, TelegramReportScheduleResponse)
    assert result.allow_partial_telegram_sync is True

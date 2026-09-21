import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from app.api import routes_jobs
from app.models import Job, JobSourceType, JobStatus


def test_scheduled_job_cancellation_is_committed_before_event_publish(monkeypatch) -> None:
    job = Job(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        source_type=JobSourceType.telegram_chat,
        telegram_chat_id=uuid.uuid4(),
        report_start_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        report_end_at=datetime(2026, 9, 21, tzinfo=timezone.utc),
        source_name="Scheduled chat",
        status=JobStatus.running,
        options={
            "scheduled_report": {
                "schedule_id": str(uuid.uuid4()),
                "scheduled_for": "2026-09-21T05:00:00+00:00",
                "rolling_window_days": 1,
                "timezone": "Europe/Berlin",
                "run_time_local": "07:00",
            }
        },
        created_at=datetime.now(timezone.utc),
    )

    class CountResult:
        def scalar(self):
            return 0

    class Session:
        def __init__(self):
            self.commits = 0
            self.events = []

        async def execute(self, statement):
            return CountResult()

        def add(self, value):
            self.events.append(value)

        async def flush(self):
            for index, event in enumerate(self.events, start=1):
                event.id = event.id or index

        async def commit(self):
            self.commits += 1

    session = Session()

    async def get_owned_job(session, *, job_id, user):
        return job

    @asynccontextmanager
    async def unavailable_nats():
        assert session.commits == 1
        assert job.status == JobStatus.cancelled
        assert job.completed_at is not None
        raise ConnectionError("NATS unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(routes_jobs, "get_owned_job_or_404", get_owned_job)
    monkeypatch.setattr(routes_jobs, "nats_context", unavailable_nats)

    response = asyncio.run(
        routes_jobs.cancel_job(
            job.id,
            user=SimpleNamespace(id=job.owner_user_id),
            session=session,
        )
    )

    assert response == {"ok": True, "status": "cancelled"}
    assert session.commits == 1
    assert job.status == JobStatus.cancelled
    assert job.completed_at is not None
    assert [event.event_type for event in session.events] == [
        "job.cancel.requested",
        "job.cancelled",
    ]
    assert "scheduled_report" in job.options

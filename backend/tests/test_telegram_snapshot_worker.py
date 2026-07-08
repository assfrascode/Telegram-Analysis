import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Job, JobStatus, TelegramChat, TelegramIngestMode, TelegramSyncStatus
from app.workers import run_telegram_collector
from app.workers.telegram_snapshot_worker import TelegramSnapshotWorker


def test_snapshot_waits_until_collector_lease_is_released(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(id=uuid.uuid4(), status=JobStatus.queued)
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        lease_owner="collector:1",
        lease_expires_at=now + timedelta(minutes=5),
    )
    events = []

    class Session:
        refresh_count = 0

        async def refresh(self, value):
            self.refresh_count += 1
            if self.refresh_count >= 2:
                value.lease_owner = None
                value.lease_expires_at = None

        async def commit(self):
            return None

    async def no_sleep(seconds):
        return None

    worker = TelegramSnapshotWorker()

    async def emit_event(session, **kwargs):
        events.append(kwargs)

    async def checkpoint_cancelled(session, job, **kwargs):
        return None

    worker.emit_event = emit_event
    worker.checkpoint_cancelled = checkpoint_cancelled
    monkeypatch.setattr(asyncio, "sleep", no_sleep)

    asyncio.run(worker._wait_for_chat_lease(Session(), job, chat))

    assert len(events) == 1
    assert events[0]["event_type"] == "telegram.sync.waiting"


def test_partial_snapshot_skips_external_wait_and_fails_without_messages(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    job_id = uuid.uuid4()
    chat_id = uuid.uuid4()
    job = SimpleNamespace(
        id=job_id,
        owner_user_id=uuid.uuid4(),
        telegram_chat_id=chat_id,
        report_start_at=now - timedelta(hours=2),
        report_end_at=now,
        status=JobStatus.queued,
        started_at=None,
        completed_at=None,
        error_message=None,
        options={"allow_partial_telegram_sync": True},
    )
    chat = SimpleNamespace(
        id=chat_id,
        ingest_mode=TelegramIngestMode.external_push,
        coverage_start=None,
        coverage_end=None,
        next_sync_at=now + timedelta(hours=1),
        updated_at=now,
    )
    events = []

    class Scalars:
        def all(self):
            return []

    class Result:
        def scalars(self):
            return Scalars()

    class Session:
        async def get(self, model, value):
            if model is Job:
                return job
            if model is TelegramChat:
                return chat
            return None

        async def execute(self, query):
            return Result()

        async def flush(self):
            return None

        async def commit(self):
            return None

    worker = TelegramSnapshotWorker()

    async def emit_event(session, **kwargs):
        events.append(kwargs)

    async def fail_wait(session, job, chat):
        raise AssertionError("partial report should not wait for external coverage")

    worker.emit_event = emit_event
    worker._wait_for_external_coverage = fail_wait

    asyncio.run(worker.handle(Session(), {"job_id": str(job_id)}))

    assert chat.next_sync_at <= datetime.now(timezone.utc)
    assert job.status == JobStatus.failed
    assert job.error_message == "No collected Telegram messages exist in the requested interval"
    assert any(event["event_type"] == "telegram.sync.partial" for event in events)
    assert any(event["event_type"] == "telegram.snapshot.failed" for event in events)


def test_backend_collector_prefers_completed_partial_report_interval() -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    report_start = now - timedelta(days=2)
    report_job = SimpleNamespace(
        id=uuid.uuid4(),
        status=JobStatus.completed,
        report_start_at=report_start,
        report_end_at=now,
        options={"allow_partial_telegram_sync": True},
    )
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        initial_sync_from=now - timedelta(days=30),
        coverage_start=now - timedelta(days=3),
        coverage_end=now - timedelta(days=1),
    )

    class Scalars:
        def all(self):
            return [report_job]

    class Result:
        def scalars(self):
            return Scalars()

    class Session:
        async def execute(self, query):
            return Result()

    requested_start, requested_end, job_id = asyncio.run(
        run_telegram_collector.sync_request_for_chat(Session(), chat, now)
    )

    assert requested_start == report_start
    assert requested_end == now
    assert job_id == report_job.id


def test_collector_startup_releases_only_non_report_leases(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    collector_chat = SimpleNamespace(
        id=uuid.uuid4(),
        lease_owner="old-collector:1",
        lease_expires_at=now + timedelta(minutes=20),
        status=run_telegram_collector.TelegramChatStatus.syncing,
        next_sync_at=now - timedelta(minutes=1),
    )
    report_chat = SimpleNamespace(
        lease_owner=f"report:{uuid.uuid4()}",
        lease_expires_at=now + timedelta(minutes=20),
        status=run_telegram_collector.TelegramChatStatus.syncing,
        next_sync_at=now - timedelta(minutes=1),
    )
    external_chat = SimpleNamespace(
        lease_owner=f"external:{uuid.uuid4()}",
        lease_expires_at=now + timedelta(minutes=20),
        status=run_telegram_collector.TelegramChatStatus.syncing,
        next_sync_at=now - timedelta(minutes=1),
    )

    class Scalars:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Result:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return Scalars(self.values)

    class Session:
        committed = False
        execute_count = 0

        async def execute(self, query):
            self.execute_count += 1
            return Result([collector_chat] if self.execute_count == 1 else [])

        async def commit(self):
            self.committed = True

    session = Session()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(run_telegram_collector, "SessionLocal", SessionContext)
    released = asyncio.run(run_telegram_collector.release_orphaned_collector_leases())

    assert released == 1
    assert collector_chat.lease_owner is None
    assert collector_chat.status == run_telegram_collector.TelegramChatStatus.error
    assert "restarted" in collector_chat.last_error
    assert collector_chat.next_sync_at > now
    assert report_chat.lease_owner is not None
    assert external_chat.lease_owner is not None


def test_collector_failure_is_persisted_only_for_owned_lease(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        lease_owner=run_telegram_collector.COLLECTOR_ID,
        status=run_telegram_collector.TelegramChatStatus.syncing,
        last_error=None,
        next_sync_at=now,
        lease_expires_at=now + timedelta(minutes=20),
        updated_at=now,
    )
    sync_run = SimpleNamespace(
        status=TelegramSyncStatus.running,
        error_message=None,
        completed_at=None,
    )

    class Scalars:
        def all(self):
            return [sync_run]

    class Result:
        def scalars(self):
            return Scalars()

    class Session:
        committed = False

        async def get(self, model, value):
            return chat

        async def execute(self, query):
            return Result()

        async def commit(self):
            self.committed = True

    session = Session()

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(run_telegram_collector, "SessionLocal", SessionContext)
    asyncio.run(
        run_telegram_collector.record_collection_failure(
            chat.id, RuntimeError("network stopped responding")
        )
    )

    assert session.committed
    assert chat.status == run_telegram_collector.TelegramChatStatus.error
    assert chat.last_error == "network stopped responding"
    assert chat.lease_owner is None
    assert sync_run.status == TelegramSyncStatus.failed

    chat.lease_owner = f"report:{uuid.uuid4()}"
    chat.last_error = None
    session.committed = False
    asyncio.run(
        run_telegram_collector.record_collection_failure(
            chat.id, RuntimeError("old collector failure")
        )
    )

    assert not session.committed
    assert chat.last_error is None

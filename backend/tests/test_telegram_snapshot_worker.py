import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import JobStatus, TelegramSyncStatus
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

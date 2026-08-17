import asyncio
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes_telegram_ingest import chat_response
from app.models import (
    JobStatus,
    TelegramChat,
    TelegramChatStatus,
    TelegramIngestMode,
    TelegramSyncRun,
    TelegramSyncStatus,
)
from app.schemas import (
    TelegramIngestChatUpsertRequest,
    TelegramIngestRunCompleteRequest,
)
from app.services import telegram_ingest
from app.services.telegram_ingest import (
    IngestPrincipal,
    claim_next_external_chat,
    complete_external_run,
    hash_ingest_token,
    new_ingest_token,
    upsert_external_chat,
)
from app.workers.telegram_snapshot_worker import TelegramSnapshotWorker

collector_spec = importlib.util.spec_from_file_location(
    "external_telegram_collector",
    Path(__file__).resolve().parents[2] / "external_telegram_collector" / "collector.py",
)
assert collector_spec and collector_spec.loader
external_collector = importlib.util.module_from_spec(collector_spec)
collector_spec.loader.exec_module(external_collector)


def test_ingest_tokens_are_prefixed_and_hashed() -> None:
    token = new_ingest_token()

    assert token.startswith("tg_ingest_")
    assert hash_ingest_token(token) != token
    assert len(hash_ingest_token(token)) == 64
    assert hash_ingest_token(token) == hash_ingest_token(token)


def test_external_chat_schema_uses_existing_interval_presets() -> None:
    with pytest.raises(ValidationError):
        TelegramIngestChatUpsertRequest(
            telegram_chat_id=42,
            title="External channel",
            chat_type="channel",
            initial_sync_from=datetime.now(timezone.utc),
            sync_interval_minutes=17,
        )


def test_ingest_run_completion_requires_known_status() -> None:
    with pytest.raises(ValidationError):
        TelegramIngestRunCompleteRequest(status="cancelled")


def test_external_collector_uses_claim_cursor_as_telegram_min_id() -> None:
    requested_end = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

    kwargs = external_collector.message_scan_kwargs(requested_end, 987)

    assert kwargs["offset_date"] == requested_end
    assert kwargs["min_id"] == 987
    assert kwargs["reverse"] is False


def test_completed_external_run_advances_durable_cursor() -> None:
    now = datetime.now(timezone.utc)
    owner_id = uuid.uuid4()
    token_id = uuid.uuid4()
    run_id = uuid.uuid4()
    chat_id = uuid.uuid4()
    run = SimpleNamespace(
        id=run_id,
        owner_user_id=owner_id,
        ingest_token_id=token_id,
        chat_id=chat_id,
        job_id=None,
        status=TelegramSyncStatus.running,
        requested_start=now - timedelta(hours=1),
        requested_end=now,
        messages_seen=0,
        attachments_seen=0,
        attachments_failed=0,
        error_message=None,
        completed_at=None,
    )
    chat = SimpleNamespace(
        id=chat_id,
        owner_user_id=owner_id,
        ingest_token_id=token_id,
        ingest_mode=TelegramIngestMode.external_push,
        lease_owner=f"external:{run_id}",
        lease_expires_at=now + timedelta(minutes=5),
        sync_interval_minutes=60,
        last_collected_message_id=100,
        coverage_start=now - timedelta(days=1),
        coverage_end=now - timedelta(hours=1),
        status=TelegramChatStatus.syncing,
        updated_at=now,
    )

    class Scalars:
        def all(self):
            return []

    class Result:
        def scalar_one_or_none(self):
            return 105

        def scalars(self):
            return Scalars()

    class Session:
        async def get(self, model, value):
            if model is TelegramSyncRun:
                return run
            if model is TelegramChat:
                return chat
            return None

        async def execute(self, query):
            return Result()

        async def flush(self):
            return None

    asyncio.run(
        complete_external_run(
            Session(),
            principal=IngestPrincipal(token_id=token_id, owner_user_id=owner_id),
            run_id=run_id,
            payload=TelegramIngestRunCompleteRequest(
                status="completed",
                messages_seen=5,
            ),
        )
    )

    assert chat.last_collected_message_id == 105
    assert run.status == TelegramSyncStatus.completed


def test_completed_external_run_immediately_requeues_chat_for_another_waiting_report(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    owner_id = uuid.uuid4()
    token_id = uuid.uuid4()
    run_id = uuid.uuid4()
    chat_id = uuid.uuid4()
    completed_interval_end = now - timedelta(days=2)
    run = SimpleNamespace(
        id=run_id,
        owner_user_id=owner_id,
        ingest_token_id=token_id,
        chat_id=chat_id,
        job_id=uuid.uuid4(),
        status=TelegramSyncStatus.running,
        requested_start=now - timedelta(days=9),
        requested_end=completed_interval_end,
        messages_seen=0,
        attachments_seen=0,
        attachments_failed=0,
        error_message=None,
        completed_at=None,
    )
    chat = SimpleNamespace(
        id=chat_id,
        owner_user_id=owner_id,
        ingest_token_id=token_id,
        ingest_mode=TelegramIngestMode.external_push,
        lease_owner=f"external:{run_id}",
        lease_expires_at=now + timedelta(minutes=5),
        sync_interval_minutes=60,
        last_collected_message_id=None,
        coverage_start=now - timedelta(days=9),
        coverage_end=now - timedelta(days=3),
        status=TelegramChatStatus.syncing,
        updated_at=now,
    )
    waiting_job = SimpleNamespace(
        id=uuid.uuid4(),
        status=JobStatus.running,
        report_start_at=now - timedelta(days=7),
        report_end_at=now - timedelta(days=1),
        options={},
    )

    class Scalars:
        def all(self):
            return [waiting_job]

    class Result:
        def __init__(self, scalar=None):
            self.scalar = scalar

        def scalar_one_or_none(self):
            return self.scalar

        def scalars(self):
            return Scalars()

    class Session:
        async def get(self, model, value):
            if model is TelegramSyncRun:
                return run
            if model is TelegramChat:
                return chat
            return None

        async def execute(self, query):
            return Result()

        async def flush(self):
            return None

    monkeypatch.setattr(telegram_ingest, "utc_now", lambda: now)

    asyncio.run(
        complete_external_run(
            Session(),
            principal=IngestPrincipal(token_id=token_id, owner_user_id=owner_id),
            run_id=run_id,
            payload=TelegramIngestRunCompleteRequest(status="completed"),
        )
    )

    assert chat.coverage_end == completed_interval_end
    assert chat.next_sync_at == now


def test_external_chat_response_exposes_ingest_mode_without_connection() -> None:
    now = datetime.now(timezone.utc)
    chat = TelegramChat(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        connection_id=None,
        telegram_chat_id=42,
        ingest_mode=TelegramIngestMode.external_push,
        title="External channel",
        chat_type="channel",
        initial_sync_from=now - timedelta(days=1),
        sync_interval_minutes=60,
        next_sync_at=now,
        status=TelegramChatStatus.active,
    )

    response = chat_response(chat)

    assert response.ingest_mode == "external_push"
    assert response.telegram_chat_id == 42


def test_external_chat_upsert_refuses_backend_pull_conversion() -> None:
    now = datetime.now(timezone.utc)
    owner_id = uuid.uuid4()
    chat = SimpleNamespace(
        owner_user_id=owner_id,
        connection_id=uuid.uuid4(),
        telegram_chat_id=-10042,
        ingest_mode=TelegramIngestMode.backend_pull,
        access_hash=None,
        title="Backend row",
        username=None,
        chat_type="channel",
        initial_sync_from=now - timedelta(days=30),
        sync_interval_minutes=60,
        status=TelegramChatStatus.error,
        last_error="Telegram connection is not available",
        next_sync_at=now + timedelta(days=1),
        lease_owner="collector:old",
        lease_expires_at=now + timedelta(minutes=5),
        updated_at=now - timedelta(days=1),
    )
    payload = TelegramIngestChatUpsertRequest(
        telegram_chat_id=-10042,
        title="External channel",
        chat_type="channel",
        initial_sync_from=now - timedelta(days=1),
        sync_interval_minutes=15,
    )

    class Result:
        def scalar_one_or_none(self):
            return chat

    class Session:
        async def execute(self, query):
            return Result()

        async def flush(self):
            return None

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            upsert_external_chat(
                Session(),
                principal=IngestPrincipal(token_id=uuid.uuid4(), owner_user_id=owner_id),
                payload=payload,
            )
        )

    assert exc_info.value.status_code == 409
    assert chat.connection_id is not None
    assert chat.ingest_mode == TelegramIngestMode.backend_pull


def test_snapshot_external_coverage_helper_accepts_existing_coverage() -> None:
    now = datetime.now(timezone.utc)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        report_start_at=now - timedelta(hours=2),
        report_end_at=now - timedelta(hours=1),
    )
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        coverage_start=now - timedelta(hours=3),
        coverage_end=now,
        next_sync_at=now + timedelta(hours=1),
        updated_at=now,
        last_error=None,
        ingest_mode=TelegramIngestMode.external_push,
    )

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def commit(self):
            return None

        async def refresh(self, value):
            return None

        async def execute(self, query):
            return Result()

    worker = TelegramSnapshotWorker()
    result = asyncio.run(worker._wait_for_external_coverage(Session(), job, chat))

    assert result is None
    assert chat.next_sync_at == now + timedelta(hours=1)


def test_external_claim_prefers_waiting_report_interval(monkeypatch) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    owner_id = uuid.uuid4()
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        initial_sync_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sync_interval_minutes=60,
        status=TelegramChatStatus.active,
        last_error="previous error",
        next_sync_at=now,
        coverage_start=None,
        coverage_end=None,
        lease_owner=None,
        lease_expires_at=None,
        updated_at=None,
    )
    report_start = now - timedelta(days=30)
    report_job = SimpleNamespace(
        id=uuid.uuid4(),
        report_start_at=report_start,
        report_end_at=now,
    )

    class Scalars:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Result:
        def __init__(self, *, scalar=None, values=None):
            self.scalar = scalar
            self.values = values or []

        def scalar_one_or_none(self):
            return self.scalar

        def scalars(self):
            return Scalars(self.values)

    class Session:
        def __init__(self):
            self.execute_count = 0
            self.added = None
            self.flushes = 0

        async def execute(self, query):
            self.execute_count += 1
            if self.execute_count == 1:
                return Result(scalar=chat)
            return Result(values=[report_job])

        def add(self, value):
            self.added = value

        async def flush(self):
            self.flushes += 1

    monkeypatch.setattr(telegram_ingest, "utc_now", lambda: now)
    session = Session()

    run, claimed_chat, after_message_id = asyncio.run(
        claim_next_external_chat(
            session,
            principal=IngestPrincipal(token_id=uuid.uuid4(), owner_user_id=owner_id),
        )
    )

    assert claimed_chat is chat
    assert run is session.added
    assert run.job_id == report_job.id
    assert run.requested_start == report_start
    assert run.requested_end == now
    assert run.requested_start != chat.initial_sync_from
    assert after_message_id is None
    assert chat.status == TelegramChatStatus.syncing
    assert chat.last_error is None
    assert chat.lease_owner == f"external:{run.id}"


def test_external_claim_picks_completed_partial_report_until_covered(monkeypatch) -> None:
    now = datetime(2026, 7, 8, 12, 0, tzinfo=timezone.utc)
    owner_id = uuid.uuid4()
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        initial_sync_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sync_interval_minutes=60,
        status=TelegramChatStatus.active,
        last_error=None,
        next_sync_at=now,
        coverage_start=now - timedelta(days=3),
        coverage_end=now - timedelta(days=1),
        last_collected_message_id=500,
        lease_owner=None,
        lease_expires_at=None,
        updated_at=None,
    )
    report_start = now - timedelta(days=2)
    report_job = SimpleNamespace(
        id=uuid.uuid4(),
        status=JobStatus.completed,
        report_start_at=report_start,
        report_end_at=now,
        options={"allow_partial_telegram_sync": True},
    )

    class Scalars:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Result:
        def __init__(self, *, scalar=None, values=None):
            self.scalar = scalar
            self.values = values or []

        def scalar_one_or_none(self):
            return self.scalar

        def scalars(self):
            return Scalars(self.values)

    class Session:
        def __init__(self):
            self.execute_count = 0
            self.added = None

        async def execute(self, query):
            self.execute_count += 1
            if self.execute_count == 1:
                return Result(scalar=chat)
            return Result(values=[report_job])

        def add(self, value):
            self.added = value

        async def flush(self):
            return None

    monkeypatch.setattr(telegram_ingest, "utc_now", lambda: now)

    run, claimed_chat, after_message_id = asyncio.run(
        claim_next_external_chat(
            Session(),
            principal=IngestPrincipal(token_id=uuid.uuid4(), owner_user_id=owner_id),
        )
    )

    assert claimed_chat is chat
    assert run.job_id == report_job.id
    assert run.requested_start == chat.coverage_end
    assert run.requested_end == now
    assert after_message_id == 500


def test_external_claim_defers_periodic_sync_when_coverage_is_not_before_now(
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    owner_id = uuid.uuid4()
    coverage_end = now + timedelta(hours=1)
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=owner_id,
        initial_sync_from=now - timedelta(days=30),
        sync_interval_minutes=60,
        status=TelegramChatStatus.syncing,
        last_error="Backend claim end must be after its start",
        next_sync_at=now,
        coverage_start=now - timedelta(days=30),
        coverage_end=coverage_end,
        last_collected_message_id=500,
        lease_owner=None,
        lease_expires_at=None,
        updated_at=None,
    )

    class Scalars:
        def all(self):
            return []

    class Result:
        def __init__(self, chat_result=None):
            self.chat_result = chat_result

        def scalar_one_or_none(self):
            return self.chat_result

        def scalars(self):
            return Scalars()

    class Session:
        def __init__(self):
            self.execute_count = 0
            self.added = None
            self.flushes = 0

        async def execute(self, query):
            self.execute_count += 1
            return Result(chat if self.execute_count == 1 else None)

        def add(self, value):
            self.added = value

        async def flush(self):
            self.flushes += 1

    monkeypatch.setattr(telegram_ingest, "utc_now", lambda: now)
    session = Session()

    claimed = asyncio.run(
        claim_next_external_chat(
            session,
            principal=IngestPrincipal(token_id=uuid.uuid4(), owner_user_id=owner_id),
        )
    )

    assert claimed is None
    assert session.added is None
    assert session.flushes == 1
    assert chat.status == TelegramChatStatus.active
    assert chat.last_error is None
    assert chat.lease_owner is None
    assert chat.lease_expires_at is None
    assert chat.next_sync_at == coverage_end + timedelta(minutes=60)
    assert chat.updated_at == now


def test_external_claim_prioritizes_active_report_over_older_partial_backfill() -> None:
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    chat = SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        coverage_start=now - timedelta(days=30),
        coverage_end=now - timedelta(days=6),
    )
    completed_partial_job = SimpleNamespace(
        id=uuid.uuid4(),
        status=JobStatus.completed,
        report_start_at=now - timedelta(days=14),
        report_end_at=now - timedelta(days=5),
        options={"allow_partial_telegram_sync": True},
    )
    active_job = SimpleNamespace(
        id=uuid.uuid4(),
        status=JobStatus.running,
        report_start_at=now - timedelta(days=7),
        report_end_at=now - timedelta(days=2),
        options={},
    )

    class Scalars:
        def all(self):
            # This is the creation-time order returned by the database.
            return [completed_partial_job, active_job]

    class Result:
        def scalars(self):
            return Scalars()

    class Session:
        async def execute(self, query):
            return Result()

    selected = asyncio.run(telegram_ingest.report_job_needing_coverage(Session(), chat))

    assert selected is active_job

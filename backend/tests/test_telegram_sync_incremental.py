import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import (
    TelegramChatStatus,
    TelegramConnectionStatus,
    TelegramIngestMode,
)
from app.services import telegram_sync
from app.services.telegram_sync import TelegramSyncError, synchronize_chat


class FakeSession:
    def __init__(self, connection):
        self.connection = connection
        self.added = []
        self.commits = 0

    async def get(self, model, value):
        return self.connection

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


class FakeClient:
    def __init__(self, messages, *, delay_seconds=0):
        self.messages = messages
        self.delay_seconds = delay_seconds
        self.iter_kwargs = None
        self.disconnected = False

    def iter_messages(self, entity, **kwargs):
        self.iter_kwargs = kwargs

        async def generate():
            for message in self.messages:
                if self.delay_seconds:
                    await asyncio.sleep(self.delay_seconds)
                yield message

        return generate()

    async def disconnect(self):
        self.disconnected = True


def make_chat(now: datetime):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        telegram_chat_id=42,
        ingest_mode=TelegramIngestMode.backend_pull,
        sync_interval_minutes=60,
        status=TelegramChatStatus.active,
        last_error=None,
        last_sync_at=now - timedelta(hours=1),
        last_collected_message_id=100,
        next_sync_at=now,
        coverage_start=now - timedelta(days=1),
        coverage_end=now - timedelta(hours=1),
        lease_owner="collector:test",
        lease_expires_at=now + timedelta(minutes=30),
        updated_at=now,
    )


def patch_sync_dependencies(monkeypatch, client):
    async def connected(connection):
        return client

    async def resolve(client, chat):
        return object()

    async def upsert(session, *, chat, message):
        return SimpleNamespace(id=uuid.uuid4(), telegram_message_id=message.id)

    async def download(session, *, chat, message_row, message):
        return False, False

    monkeypatch.setattr(telegram_sync, "connected_client", connected)
    monkeypatch.setattr(telegram_sync, "_resolve_entity", resolve)
    monkeypatch.setattr(telegram_sync, "_upsert_message", upsert)
    monkeypatch.setattr(telegram_sync, "_download_media", download)


def test_forward_sync_passes_cursor_and_advances_it_only_after_completion(monkeypatch) -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    messages = [
        SimpleNamespace(id=102, date=now - timedelta(minutes=1)),
        SimpleNamespace(id=101, date=now - timedelta(minutes=2)),
    ]
    client = FakeClient(messages)
    patch_sync_dependencies(monkeypatch, client)
    chat = make_chat(now)
    session = FakeSession(
        SimpleNamespace(status=TelegramConnectionStatus.connected)
    )

    run = asyncio.run(
        synchronize_chat(
            session,
            chat=chat,
            requested_start=chat.coverage_end,
            requested_end=now,
        )
    )

    assert client.iter_kwargs["min_id"] == 100
    assert run.messages_seen == 2
    assert chat.last_collected_message_id == 102
    assert client.disconnected is True


def test_total_sync_can_exceed_inactivity_window_while_each_message_progresses(
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    messages = [
        SimpleNamespace(id=103, date=now - timedelta(seconds=1)),
        SimpleNamespace(id=102, date=now - timedelta(seconds=2)),
        SimpleNamespace(id=101, date=now - timedelta(seconds=3)),
    ]
    client = FakeClient(messages, delay_seconds=0.03)
    patch_sync_dependencies(monkeypatch, client)
    monkeypatch.setattr(
        telegram_sync.settings,
        "telegram_sync_inactivity_timeout_seconds",
        0.05,
    )
    chat = make_chat(now)
    session = FakeSession(
        SimpleNamespace(status=TelegramConnectionStatus.connected)
    )

    run = asyncio.run(
        synchronize_chat(
            session,
            chat=chat,
            requested_start=chat.coverage_end,
            requested_end=now,
        )
    )

    assert run.messages_seen == 3
    assert chat.last_collected_message_id == 103


def test_stalled_or_failed_sync_does_not_advance_cursor(monkeypatch) -> None:
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    client = FakeClient(
        [SimpleNamespace(id=101, date=now - timedelta(seconds=1))],
        delay_seconds=0.05,
    )
    patch_sync_dependencies(monkeypatch, client)
    monkeypatch.setattr(
        telegram_sync.settings,
        "telegram_sync_inactivity_timeout_seconds",
        0.01,
    )
    chat = make_chat(now)
    session = FakeSession(
        SimpleNamespace(status=TelegramConnectionStatus.connected)
    )

    with pytest.raises(TelegramSyncError, match="made no progress"):
        asyncio.run(
            synchronize_chat(
                session,
                chat=chat,
                requested_start=chat.coverage_end,
                requested_end=now,
            )
        )

    assert chat.last_collected_message_id == 100
    assert chat.status == TelegramChatStatus.error

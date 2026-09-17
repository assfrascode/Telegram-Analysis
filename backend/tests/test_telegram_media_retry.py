import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api import routes_telegram_ingest
from app.models import (
    CollectedTelegramMedia, CollectedTelegramMessage, StepStatus, TelegramChat,
    TelegramConnection, TelegramConnectionStatus, TelegramIngestMode, TelegramSyncRun,
    User,
)
from app.services import telegram_sync
from app.services.telegram_ingest import IngestPrincipal, upsert_external_media


class AsyncSessionAdapter:
    """Exercise real ORM queries on the collection tables without external services."""
    def __init__(self, session):
        self.session = session

    def add(self, value):
        self.session.add(value)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def execute(self, query):
        return self.session.execute(query)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()


@pytest.fixture
def collection(monkeypatch):
    engine = create_engine("sqlite://")
    tables = [User, TelegramConnection, TelegramChat, CollectedTelegramMessage,
              CollectedTelegramMedia, TelegramSyncRun]
    for model in tables:
        model.__table__.create(engine)
    now = datetime.now(timezone.utc)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="retry@example.test", password_hash="unused")
        db.add(user)
        db.flush()
        connection = TelegramConnection(
            owner_user_id=user.id, api_id=1, api_hash_encrypted="unused",
            session_encrypted="unused", telegram_user_id=1,
            status=TelegramConnectionStatus.connected,
        )
        db.add(connection)
        db.flush()
        chat = TelegramChat(
            owner_user_id=user.id, connection_id=connection.id, telegram_chat_id=42,
            title="Test", chat_type="channel", initial_sync_from=now - timedelta(days=30),
            coverage_start=now - timedelta(days=30), coverage_end=now - timedelta(hours=1),
            last_collected_message_id=100, sync_interval_minutes=60,
        )
        db.add(chat)
        db.commit()

        async def direct(function, *args, **kwargs):
            return function(*args, **kwargs)

        monkeypatch.setattr(telegram_sync.asyncio, "to_thread", direct)
        monkeypatch.setattr(telegram_sync, "utc_now", lambda: now)
        yield SimpleNamespace(db=db, session=AsyncSessionAdapter(db), chat=chat, now=now)
    engine.dispose()


def failed_media(collection, message_id=90, **overrides):
    message = CollectedTelegramMessage(
        chat_id=collection.chat.id, owner_user_id=collection.chat.owner_user_id,
        telegram_message_id=message_id, timestamp=collection.now - timedelta(days=1),
    )
    collection.db.add(message)
    collection.db.flush()
    media = CollectedTelegramMedia(
        chat_id=collection.chat.id, owner_user_id=collection.chat.owner_user_id,
        message_id=message.id, telegram_media_key=f"photo:{message_id}",
        media_type="image", filename=f"photo-{message_id}.jpg", mime_type="image/jpeg",
        status=StepStatus.failed_retryable, updated_at=collection.now - timedelta(hours=1),
    )
    for key, value in overrides.items():
        setattr(media, key, value)
    collection.db.add(media)
    collection.db.commit()
    return media, message


class Message(SimpleNamespace):
    def __init__(self, message_id, date, *, failure=None):
        super().__init__(
            id=message_id, date=date, edit_date=None, sender_id=None, action=None,
            photo=SimpleNamespace(id=message_id, sizes=[]), document=None,
            reply_to_msg_id=None, forward=None, reactions=None, message="Photo",
            failure=failure,
        )

    async def get_sender(self):
        return None

    def to_dict(self):
        return {}

    async def download_media(self, *, file, progress_callback):
        if self.failure:
            raise self.failure
        Path(file).write_bytes(b"test-image")
        progress_callback(10, 10)
        return file


class Client:
    def __init__(self, messages=(), retries=None):
        self.messages = messages
        self.retries = retries or {}
        self.fetched = []
        self.scans = []

    async def get_messages(self, entity, *, ids):
        self.fetched.append(ids)
        result = self.retries.get(ids)
        if isinstance(result, Exception):
            raise result
        return result

    async def iter_messages(self, entity, **kwargs):
        self.scans.append(kwargs)
        for message in self.messages:
            yield message

    async def disconnect(self):
        pass


def sync(collection, client, monkeypatch):
    async def connect(_connection):
        return client

    async def resolve(_client, _chat):
        return object()

    monkeypatch.setattr(telegram_sync, "connected_client", connect)
    monkeypatch.setattr(telegram_sync, "_resolve_entity", resolve)
    return asyncio.run(telegram_sync.synchronize_chat(
        collection.session, chat=collection.chat,
        requested_start=collection.chat.coverage_end, requested_end=collection.now,
    ))


def test_failed_attachment_recovers_after_cursor_advanced_with_no_new_messages(collection, monkeypatch):
    message = Message(101, collection.now - timedelta(minutes=1), failure=OSError("temporary failure"))
    first = sync(collection, Client([message]), monkeypatch)
    assert first.attachments_failed == 1
    assert collection.chat.last_collected_message_id == 101
    media = collection.db.execute(select(CollectedTelegramMedia)).scalar_one()
    assert media.status == StepStatus.failed_retryable
    media.updated_at = collection.now - timedelta(minutes=10)
    collection.now += timedelta(hours=1)
    collection.db.commit()
    message.failure = None
    stored = []
    monkeypatch.setattr(telegram_sync, "put_stream", lambda key, data, *args: stored.append((key, data.read())))
    client = Client(retries={101: message})

    second = sync(collection, client, monkeypatch)

    assert client.fetched == [101]
    assert client.scans[0]["min_id"] == 101
    assert second.messages_seen == 0
    assert second.attachments_seen == 1 and second.attachments_failed == 0
    assert collection.chat.last_collected_message_id == 101
    assert media.status == StepStatus.completed and media.error_message is None
    assert stored == [(media.minio_object_key, b"test-image")]
    assert asyncio.run(telegram_sync.pending_media_retries(collection.session, chat=collection.chat)) == []


def test_retry_queue_filters_owner_status_cooldown_and_local_boundary(collection):
    wanted, _ = failed_media(collection)
    failed_media(collection, 91, status=StepStatus.completed)
    failed_media(collection, 92, status=StepStatus.failed_permanent)
    failed_media(collection, 93, updated_at=collection.now)
    failed_media(collection, 94, owner_user_id=uuid.uuid4())
    failed_media(collection, 95, chat_id=uuid.uuid4())
    _, older = failed_media(collection, 96)
    older.timestamp = collection.chat.initial_sync_from - timedelta(days=1)
    collection.db.commit()
    rows = asyncio.run(telegram_sync.pending_media_retries(collection.session, chat=collection.chat))
    assert [media.id for media, _ in rows] == [wanted.id]


def test_retry_queue_is_bounded_and_oldest_failures_are_first(collection):
    for index in range(102):
        failed_media(collection, index + 1, updated_at=collection.now - timedelta(minutes=200 - index))
    rows = asyncio.run(telegram_sync.pending_media_retries(collection.session, chat=collection.chat))
    assert len(rows) == 100
    assert [message.telegram_message_id for _, message in rows] == list(range(1, 101))


@pytest.mark.parametrize("failure", [None, TimeoutError("try later"), "oversized", "changed"])
def test_retry_classifies_failures_without_resetting_cursor(collection, monkeypatch, failure):
    media, _ = failed_media(collection)
    value = failure
    if failure in ("oversized", "changed"):
        value = Message(90 if failure == "oversized" else 999, collection.now - timedelta(days=1))
        if failure == "oversized":
            value.photo.sizes = [SimpleNamespace(size=telegram_sync.settings.max_ingest_media_bytes + 1)]
    client = Client(retries={90: value})
    run = sync(collection, client, monkeypatch)
    assert run.attachments_failed == 1
    assert collection.chat.last_collected_message_id == 100
    assert media.error_message
    assert media.status == (StepStatus.failed_retryable if isinstance(failure, TimeoutError) else StepStatus.failed_permanent)


def test_external_claim_includes_old_media_independently_of_cursor(collection, monkeypatch):
    media, message = failed_media(collection)
    chat = collection.chat
    chat.ingest_mode = TelegramIngestMode.external_push
    run = SimpleNamespace(id=uuid.uuid4(), requested_start=chat.coverage_end, requested_end=collection.now)

    async def claim(*args, **kwargs):
        return run, chat, 100

    monkeypatch.setattr(routes_telegram_ingest, "claim_next_external_chat", claim)
    payload = asyncio.run(routes_telegram_ingest.claim_next(principal=None, session=collection.session))
    assert payload.after_message_id == 100
    assert payload.media_retries[0].telegram_message_id == message.telegram_message_id
    assert payload.media_retries[0].telegram_media_key == media.telegram_media_key


@pytest.mark.parametrize("retryable", [True, False])
def test_external_error_persists_retry_classification(collection, retryable):
    media, message = failed_media(collection)
    chat = collection.chat
    chat.ingest_mode = TelegramIngestMode.external_push
    chat.ingest_token_id = uuid.uuid4()
    run = TelegramSyncRun(
        chat_id=chat.id, owner_user_id=chat.owner_user_id, ingest_token_id=chat.ingest_token_id,
        requested_start=chat.coverage_end, requested_end=collection.now,
    )
    collection.db.add(run)
    collection.db.flush()
    chat.lease_owner = f"external:{run.id}"
    chat.lease_expires_at = collection.now + timedelta(hours=1)
    collection.db.commit()
    result = asyncio.run(upsert_external_media(
        collection.session, principal=IngestPrincipal(owner_user_id=chat.owner_user_id, token_id=chat.ingest_token_id),
        run_id=run.id, telegram_message_id=message.telegram_message_id,
        telegram_media_key=media.telegram_media_key, media_type=media.media_type,
        filename=media.filename, mime_type=media.mime_type,
        declared_size_bytes=None, declared_sha256=None, file=None,
        error_message="download failed", retryable=retryable,
    ))
    assert result.media.status == (StepStatus.failed_retryable if retryable else StepStatus.failed_permanent)

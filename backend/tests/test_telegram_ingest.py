import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.routes_telegram_ingest import chat_response
from app.models import TelegramChat, TelegramChatStatus, TelegramIngestMode
from app.schemas import (
    TelegramIngestChatUpsertRequest,
    TelegramIngestRunCompleteRequest,
)
from app.services.telegram_ingest import hash_ingest_token, new_ingest_token
from app.workers.telegram_snapshot_worker import TelegramSnapshotWorker


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
    assert chat.next_sync_at <= datetime.now(timezone.utc)

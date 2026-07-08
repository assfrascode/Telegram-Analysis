import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from telethon.tl.types import Channel

from app.models import (
    Job,
    JobSourceType,
    StepStatus,
    TelegramChat,
    TelegramConnectionStatus,
    TelegramIngestMode,
    TelegramMedia,
)
from app.schemas import (
    TelegramChatCreateRequest,
    TelegramDialogResponse,
    TelegramReportCreateRequest,
)
from app.services.jobs import initial_task_payload
from app.services.report_builder import build_report_media
from app.services.telegram_crypto import decrypt_telegram_secret, encrypt_telegram_secret
from app.services.telegram_chat_access import ensure_chat_sync_source_available
from app.services.telegram_sync import _resolve_entity, periodic_sync_start


def test_telegram_secrets_are_encrypted_and_round_trip() -> None:
    encrypted = encrypt_telegram_secret("api-hash-value")
    assert encrypted != "api-hash-value"
    assert decrypt_telegram_secret(encrypted) == "api-hash-value"


def test_telegram_report_requires_timezone_and_ordered_interval() -> None:
    default_request = TelegramReportCreateRequest(
        telegram_chat_id=uuid.uuid4(),
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        questions=[{"text": "What happened?"}],
    )
    assert default_request.options.allow_partial_telegram_sync is False

    partial_request = TelegramReportCreateRequest(
        telegram_chat_id=uuid.uuid4(),
        start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        questions=[{"text": "What happened?"}],
        options={"allow_partial_telegram_sync": True},
    )
    assert partial_request.options.allow_partial_telegram_sync is True

    with pytest.raises(ValidationError):
        TelegramReportCreateRequest(
            telegram_chat_id=uuid.uuid4(),
            start_at=datetime(2026, 1, 1),
            end_at=datetime(2026, 1, 2),
            questions=[{"text": "What happened?"}],
        )

    with pytest.raises(ValidationError):
        TelegramReportCreateRequest(
            telegram_chat_id=uuid.uuid4(),
            start_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            end_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            questions=[{"text": "What happened?"}],
        )

    with pytest.raises(ValidationError):
        TelegramReportCreateRequest(
            telegram_chat_id=uuid.uuid4(),
            start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            questions=[{"text": "What happened?"}],
        )


def test_chat_sync_interval_is_limited_to_supported_presets() -> None:
    with pytest.raises(ValidationError):
        TelegramChatCreateRequest(
            telegram_chat_id=42,
            title="Channel",
            chat_type="channel",
            initial_sync_from=datetime.now(timezone.utc),
            sync_interval_minutes=17,
        )


def test_dialog_access_hash_stays_exact_over_json() -> None:
    access_hash = 9_007_199_254_740_993
    dialog = TelegramDialogResponse(
        telegram_chat_id=42,
        access_hash=str(access_hash),
        title="Channel",
        chat_type="channel",
    )
    assert f'"access_hash":"{access_hash}"' in dialog.model_dump_json()


def test_chat_create_accepts_access_hash_as_decimal_string() -> None:
    payload = TelegramChatCreateRequest(
        telegram_chat_id=42,
        access_hash="9007199254740993",
        title="Channel",
        chat_type="channel",
        initial_sync_from=datetime.now(timezone.utc),
    )
    assert payload.access_hash == "9007199254740993"


def test_live_dialog_resolution_repairs_rounded_channel_access_hash() -> None:
    live_hash = 9_007_199_254_740_993
    entity = Channel(
        id=42,
        title="Canonical channel",
        photo=None,
        date=datetime.now(timezone.utc),
        broadcast=True,
        access_hash=live_hash,
        username="canonical",
    )

    class FakeClient:
        async def iter_dialogs(self):
            yield SimpleNamespace(entity=entity, name="Canonical channel")

    chat = TelegramChat(
        owner_user_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        telegram_chat_id=42,
        access_hash=9_007_199_254_740_992,
        title="Rounded channel",
        chat_type="channel",
        initial_sync_from=datetime.now(timezone.utc),
        next_sync_at=datetime.now(timezone.utc),
    )
    resolved = asyncio.run(_resolve_entity(FakeClient(), chat))
    assert resolved is entity
    assert chat.access_hash == live_hash
    assert chat.title == "Canonical channel"


def test_initial_task_uses_telegram_snapshot_subject_payload() -> None:
    job = Job(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        source_type=JobSourceType.telegram_chat,
        telegram_chat_id=uuid.uuid4(),
        options={},
    )
    payload = initial_task_payload(job)
    assert payload["telegram_chat_id"] == str(job.telegram_chat_id)
    assert payload["task_key"] == f"telegram-snapshot:{job.id}"
    assert "upload_id" not in payload


def test_external_chat_does_not_require_backend_connection_for_sync_source() -> None:
    chat = SimpleNamespace(ingest_mode=TelegramIngestMode.external_push, connection_id=None)

    class Session:
        async def get(self, *args):
            raise AssertionError("external chats must not load backend connection")

    asyncio.run(ensure_chat_sync_source_available(Session(), chat))


def test_backend_pull_chat_requires_connected_backend_account_for_sync_source() -> None:
    chat = SimpleNamespace(ingest_mode=TelegramIngestMode.backend_pull, connection_id=None)

    class Session:
        async def get(self, *args):
            return None

    with pytest.raises(HTTPException) as exc:
        asyncio.run(ensure_chat_sync_source_available(Session(), chat))

    assert "external collector chat" in exc.value.detail


def test_backend_pull_chat_accepts_connected_backend_account_for_sync_source() -> None:
    chat = SimpleNamespace(ingest_mode=TelegramIngestMode.backend_pull, connection_id=uuid.uuid4())
    connection = SimpleNamespace(status=TelegramConnectionStatus.connected)

    class Session:
        async def get(self, *args):
            return connection

    asyncio.run(ensure_chat_sync_source_available(Session(), chat))


def test_periodic_sync_rewinds_overlap_but_not_before_initial_start() -> None:
    initial = datetime(2026, 1, 1, tzinfo=timezone.utc)
    chat = TelegramChat(
        owner_user_id=uuid.uuid4(),
        connection_id=uuid.uuid4(),
        telegram_chat_id=42,
        title="Channel",
        chat_type="channel",
        initial_sync_from=initial,
        next_sync_at=initial,
        coverage_end=initial + timedelta(hours=12),
    )
    assert periodic_sync_start(chat) == initial


def test_collected_media_does_not_emit_broken_report_link() -> None:
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        source_media_id=uuid.uuid4(),
        media_type="document",
        original_path="telegram/source/file.pdf",
        status=StepStatus.completed,
    )
    rendered = build_report_media(media, None)
    assert rendered.relative_href is None

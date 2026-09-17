import asyncio
import importlib.util
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

spec = importlib.util.spec_from_file_location("collector_media_retry", Path(__file__).resolve().parents[1] / "collector.py")
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


class Message:
    id = 90
    photo = SimpleNamespace(id=90, sizes=[])
    document = None

    def __init__(self, now, failure=None):
        self.date = now - timedelta(days=1)
        self.failure = failure

    async def download_media(self, *, file, progress_callback):
        if self.failure:
            raise self.failure
        Path(file).write_bytes(b"retry-image")
        progress_callback(11, 11)
        return file


class Backend:
    def __init__(self):
        self.files = []
        self.errors = []
        self.completed = None

    async def post_media_file(self, run, message_id, metadata, path):
        self.files.append((message_id, metadata.media_key, Path(path).read_bytes()))

    async def post_media_error(self, run, message_id, metadata, error, *, retryable=True):
        self.errors.append((message_id, error, retryable))

    async def complete(self, run, **kwargs):
        self.completed = kwargs


def claim(now):
    return {
        "run_id": str(uuid.uuid4()),
        "chat": {"id": str(uuid.uuid4()), "telegram_chat_id": 42, "title": "Test", "chat_type": "channel"},
        "requested_start": now - timedelta(hours=1), "requested_end": now,
        "after_message_id": 100,
        "media_retries": [{
            "telegram_message_id": 90, "telegram_media_key": "photo:90",
            "media_type": "image", "filename": "photo-90.jpg", "mime_type": "image/jpeg",
        }],
    }


def run_retry(monkeypatch, outcome, *, max_bytes=None):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(collector, "INITIAL_SYNC_FROM", (now - timedelta(days=30)).isoformat())
    monkeypatch.setattr(collector, "USE_TAKEOUT", False)
    monkeypatch.setattr(collector, "approved_entity_for_claim", lambda *args: SimpleNamespace(id=42, title="Test"))
    monkeypatch.setattr(collector, "log", lambda *args, **kwargs: None)
    if max_bytes is not None:
        monkeypatch.setattr(collector, "MAX_MEDIA_FILE_BYTES", max_bytes)

    class Client:
        def __init__(self):
            self.fetched = []
            self.scan = None

        async def get_messages(self, entity, *, ids):
            self.fetched.append(ids)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def iter_messages(self, entity, **kwargs):
            self.scan = kwargs
            for message in []:
                yield message

    backend = Backend()
    client = Client()
    asyncio.run(collector.process_claim(backend, client, claim(now)))
    assert client.fetched == [90]
    assert client.scan["min_id"] == 100
    assert backend.completed["status"] == "completed"
    assert backend.completed["messages_seen"] == 0
    assert backend.completed["attachments_seen"] == 1
    return backend


def test_external_retry_recovers_old_attachment_without_new_messages(monkeypatch):
    backend = run_retry(monkeypatch, Message(datetime.now(timezone.utc)))
    assert backend.files == [(90, "photo:90", b"retry-image")]
    assert backend.errors == []
    assert backend.completed["attachments_failed"] == 0


@pytest.mark.parametrize("phase", ["lookup", "download"])
def test_external_transient_failures_remain_retryable(monkeypatch, phase):
    error = TimeoutError("temporary timeout")
    outcome = error if phase == "lookup" else Message(datetime.now(timezone.utc), error)
    backend = run_retry(monkeypatch, outcome)
    assert backend.errors == [(90, "temporary timeout", True)]
    assert backend.files == []
    assert backend.completed["attachments_failed"] == 1


@pytest.mark.parametrize("outcome", [None, SimpleNamespace(id=90)])
def test_external_missing_message_is_permanent(monkeypatch, outcome):
    backend = run_retry(monkeypatch, outcome)
    assert backend.errors == [(90, "Original Telegram attachment is no longer available", False)]


def test_external_retry_keeps_file_size_limit(monkeypatch):
    backend = run_retry(monkeypatch, Message(datetime.now(timezone.utc)), max_bytes=5)
    assert backend.errors[0][2] is False
    assert "TELEGRAM_MAX_MEDIA_FILE_BYTES" in backend.errors[0][1]
    assert backend.files == []


def test_external_retry_respects_local_collection_boundary(monkeypatch):
    message = Message(datetime.now(timezone.utc) - timedelta(days=60))
    backend = run_retry(monkeypatch, message)
    assert backend.errors[0][2] is False
    assert "local sync boundary" in backend.errors[0][1]
    assert backend.files == []


def test_external_retry_run_quota_is_retryable(monkeypatch):
    monkeypatch.setattr(collector, "MAX_MEDIA_FILES_PER_RUN", 0)
    backend = run_retry(monkeypatch, Message(datetime.now(timezone.utc)))
    assert backend.errors[0][2] is True
    assert backend.files == []


def test_claim_retry_list_is_bounded_and_optional():
    payload = claim(datetime.now(timezone.utc))
    payload["media_retries"] *= 101
    with pytest.raises(ValidationError):
        collector.ClaimInput.model_validate(payload)
    del payload["media_retries"]
    assert collector.ClaimInput.model_validate(payload).media_retries == []

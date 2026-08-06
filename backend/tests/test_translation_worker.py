import asyncio
import os
import uuid

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import (
    Job,
    MediaTranscript,
    MediaTranscriptTranslation,
    MessageTranslation,
    StepStatus,
    TelegramMessage,
)
from app.services.libretranslate import LibreTranslationResult
from app.services.worker_control import PermanentWorkerError
from app.workers import subjects, translation_worker
from app.workers.translation_worker import TranslateWorker, source_text_hash


class _Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.commits = 0

    async def execute(self, _query):
        return _Result(self.results.pop(0))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def _worker_with_spies():
    worker = TranslateWorker()
    events = []
    enqueued = []

    async def emit_event(session, **kwargs):
        events.append(kwargs)

    async def should_skip_cancelled(session, job_id):
        return False

    async def checkpoint_cancelled(session, job, **kwargs):
        return None

    async def enqueue(subject, payload):
        enqueued.append((subject, payload))

    worker.emit_event = emit_event
    worker.should_skip_cancelled = should_skip_cancelled
    worker.checkpoint_cancelled = checkpoint_cancelled
    worker.enqueue = enqueue
    return worker, events, enqueued


def _job_sources():
    job = Job(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), options={"translate": True})
    message = TelegramMessage(
        id=uuid.uuid4(),
        job_id=job.id,
        telegram_message_id=1,
        text="Guten Morgen",
        raw={},
    )
    transcript = MediaTranscript(
        id=uuid.uuid4(),
        job_id=job.id,
        media_id=uuid.uuid4(),
        provider="openai",
        model_name="whisper-1",
        response_format="text",
        status=StepStatus.completed,
        transcript_text="Gesprochener Inhalt.",
        raw_response={},
    )
    return job, message, transcript


def test_translation_worker_translates_messages_and_whisper_results(monkeypatch) -> None:
    job, message, transcript = _job_sources()
    session = _Session([job, [message], [transcript], [], []])
    calls = []

    class Client:
        async def translate_texts(self, texts, *, target_language, source_language):
            calls.append((texts, target_language, source_language))
            return [
                LibreTranslationResult("Good morning", "de", 99.0, {"index": 0}),
                LibreTranslationResult("Spoken content.", "de", 98.0, {"index": 1}),
            ]

    monkeypatch.setattr(translation_worker.settings, "libretranslate_base_url", "http://translate")
    monkeypatch.setattr(translation_worker.settings, "openai_transcription_model", "whisper-1")
    monkeypatch.setattr(translation_worker.settings, "libretranslate_batch_size", 20)
    monkeypatch.setattr(translation_worker, "LibreTranslateClient", Client)
    worker, events, enqueued = _worker_with_spies()

    asyncio.run(worker.handle(session, {"job_id": str(job.id)}))

    assert calls == [
        (["Guten Morgen", "Gesprochener Inhalt."], "en", "auto")
    ]
    message_translation = next(
        row for row in session.added if isinstance(row, MessageTranslation)
    )
    transcript_translation = next(
        row for row in session.added if isinstance(row, MediaTranscriptTranslation)
    )
    assert message_translation.translated_text == "Good morning"
    assert transcript_translation.transcript_id == transcript.id
    assert transcript_translation.translated_text == "Spoken content."
    completed = next(event for event in events if event["event_type"] == "translation.completed")
    assert completed["payload"]["message_texts_total"] == 1
    assert completed["payload"]["transcript_texts_total"] == 1
    assert enqueued[0][0] == subjects.CHUNK_CREATE


def test_translation_worker_refreshes_changed_transcript_translation(monkeypatch) -> None:
    job, _message, transcript = _job_sources()
    existing = MediaTranscriptTranslation(
        job_id=job.id,
        transcript_id=transcript.id,
        provider="libretranslate",
        source_text_hash="old-hash",
        detected_source_language="de",
        target_language="en",
        translated_text="Old text",
        raw_response={},
    )
    session = _Session([job, [], [transcript], [existing]])

    class Client:
        async def translate_texts(self, texts, *, target_language, source_language):
            return [LibreTranslationResult("Updated spoken content.", "de", 97.0, {})]

    monkeypatch.setattr(translation_worker.settings, "libretranslate_base_url", "http://translate")
    monkeypatch.setattr(translation_worker.settings, "openai_transcription_model", "whisper-1")
    monkeypatch.setattr(translation_worker, "LibreTranslateClient", Client)
    worker, _events, _enqueued = _worker_with_spies()

    asyncio.run(worker.handle(session, {"job_id": str(job.id)}))

    assert session.added == []
    assert existing.source_text_hash == source_text_hash(transcript.transcript_text)
    assert existing.translated_text == "Updated spoken content."


def test_translation_worker_rejects_blank_result(monkeypatch) -> None:
    job, message, _transcript = _job_sources()
    session = _Session([job, [message], [], [], []])

    class Client:
        async def translate_texts(self, texts, *, target_language, source_language):
            return [LibreTranslationResult("   ", "de", 99.0, {})]

    monkeypatch.setattr(translation_worker.settings, "libretranslate_base_url", "http://translate")
    monkeypatch.setattr(translation_worker, "LibreTranslateClient", Client)
    worker, _events, enqueued = _worker_with_spies()

    with pytest.raises(PermanentWorkerError, match="blank translation"):
        asyncio.run(worker.handle(session, {"job_id": str(job.id)}))

    assert enqueued == []

import asyncio
import os
import uuid

import httpx

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import StepStatus, TelegramMedia
from app.workers import transcription_worker
from app.workers.transcription_worker import TranscriptionWorker


def _media(path: str, *, size_bytes: int = 1024) -> TelegramMedia:
    return TelegramMedia(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        media_type="audio",
        original_path=path,
        minio_object_key="objects/audio",
        size_bytes=size_bytes,
    )


def _http_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.test/v1/audio/transcriptions")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("openai error", request=request, response=response)


def test_supported_transcription_path_matches_openai_direct_formats() -> None:
    assert transcription_worker._supported_transcription_path("audio.mp3") is True
    assert transcription_worker._supported_transcription_path("video.mp4") is True
    assert transcription_worker._supported_transcription_path("video.mkv") is False
    assert transcription_worker._supported_transcription_path("video.mov") is False


def test_retryable_transcription_exception_classification() -> None:
    assert transcription_worker._is_retryable_exception(_http_error(429)) is True
    assert transcription_worker._is_retryable_exception(_http_error(500)) is True
    assert transcription_worker._is_retryable_exception(_http_error(400)) is False
    assert transcription_worker._is_retryable_exception(
        httpx.ConnectError("boom", request=httpx.Request("POST", "https://example.test"))
    ) is True


def test_transcribe_one_uses_mock_mode_without_api_key(monkeypatch) -> None:
    monkeypatch.setattr(transcription_worker.settings, "llm_mock_enabled", True)
    monkeypatch.setattr(transcription_worker.settings, "openai_api_key", "")

    result = asyncio.run(TranscriptionWorker()._transcribe_one(_media("audio.mp3")))

    assert result.status == StepStatus.completed
    assert "[MOCK_AUDIO_TRANSCRIPT]" in result.transcript_text


def test_transcribe_one_marks_missing_api_key_permanent(monkeypatch) -> None:
    monkeypatch.setattr(transcription_worker.settings, "llm_mock_enabled", False)
    monkeypatch.setattr(transcription_worker.settings, "openai_api_key", "")

    result = asyncio.run(TranscriptionWorker()._transcribe_one(_media("audio.mp3")))

    assert result.status == StepStatus.failed_permanent
    assert result.permanent is True
    assert result.error == "openai_api_key_missing"


def test_transcribe_one_marks_too_large_file_permanent(monkeypatch) -> None:
    monkeypatch.setattr(transcription_worker.settings, "llm_mock_enabled", False)
    monkeypatch.setattr(transcription_worker.settings, "openai_api_key", "secret-key")
    monkeypatch.setattr(transcription_worker.settings, "openai_transcription_max_bytes", 100)

    result = asyncio.run(TranscriptionWorker()._transcribe_one(_media("audio.mp3", size_bytes=101)))

    assert result.status == StepStatus.failed_permanent
    assert result.permanent is True
    assert result.error == "media_too_large_for_openai_transcription"


def test_transcribe_one_marks_unsupported_format_permanent(monkeypatch) -> None:
    monkeypatch.setattr(transcription_worker.settings, "llm_mock_enabled", False)
    monkeypatch.setattr(transcription_worker.settings, "openai_api_key", "secret-key")

    result = asyncio.run(TranscriptionWorker()._transcribe_one(_media("video.mkv")))

    assert result.status == StepStatus.failed_permanent
    assert result.permanent is True
    assert result.error == "unsupported_openai_transcription_format"


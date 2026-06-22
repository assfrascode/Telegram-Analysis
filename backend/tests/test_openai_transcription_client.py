import asyncio
import os

import httpx
import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.services.openai_transcription import OpenAITranscriptionClient


def test_transcribe_file_sends_openai_multipart_payload(tmp_path) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake audio")
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["content_type"] = request.headers.get("content-type")
        captured["body"] = body
        return httpx.Response(200, text="Plain transcript")

    client = OpenAITranscriptionClient(
        base_url="https://api.openai.test/v1",
        api_key="secret-key",
        model="whisper-1",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        client.transcribe_file(
            str(audio_path),
            filename="audio.mp3",
            content_type="audio/mpeg",
        )
    )

    assert captured["url"] == "https://api.openai.test/v1/audio/transcriptions"
    assert captured["authorization"] == "Bearer secret-key"
    assert "multipart/form-data" in str(captured["content_type"])
    assert b'name="model"' in captured["body"]
    assert b"whisper-1" in captured["body"]
    assert b'name="response_format"' in captured["body"]
    assert b"text" in captured["body"]
    assert b'filename="audio.mp3"' in captured["body"]
    assert result.transcript_text == "Plain transcript"
    assert result.raw_response["response_format"] == "text"
    assert result.raw_response["text_chars"] == len("Plain transcript")


def test_transcribe_file_requires_api_key(tmp_path) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake audio")
    client = OpenAITranscriptionClient(
        base_url="https://api.openai.test/v1",
        api_key="",
    )

    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        asyncio.run(client.transcribe_file(str(audio_path)))


def test_transcribe_file_raises_for_openai_error(tmp_path) -> None:
    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake audio")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request", request=request)

    client = OpenAITranscriptionClient(
        base_url="https://api.openai.test/v1",
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(client.transcribe_file(str(audio_path)))

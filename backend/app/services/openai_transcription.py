from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.observability.metrics import observe_model_call

settings = get_settings()


@dataclass(slots=True)
class TranscriptionResult:
    transcript_text: str
    raw_response: dict[str, Any]


class OpenAITranscriptionClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        configured_base_url = base_url if base_url is not None else settings.openai_transcription_base_url
        configured_api_key = api_key if api_key is not None else settings.openai_api_key
        configured_model = model if model is not None else settings.openai_transcription_model
        self.base_url = configured_base_url.strip().rstrip("/")
        self.api_key = configured_api_key.strip()
        self.model = configured_model.strip() or "whisper-1"
        self.timeout = timeout if timeout is not None else settings.openai_transcription_timeout_seconds
        self.transport = transport

    async def transcribe_file(
        self,
        file_path: str,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> TranscriptionResult:
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        if not self.base_url:
            raise ValueError("OPENAI_TRANSCRIPTION_BASE_URL is not configured")

        upload_name = filename or Path(file_path).name or "audio"
        media_type = content_type or "application/octet-stream"
        async with observe_model_call("openai", "transcription", self.model):
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                with open(file_path, "rb") as source:
                    response = await client.post(
                        f"{self.base_url}/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data={
                            "model": self.model,
                            "response_format": "text",
                        },
                        files={"file": (upload_name, source, media_type)},
                    )
                response.raise_for_status()

        transcript = response.text.strip()
        return TranscriptionResult(
            transcript_text=transcript,
            raw_response={
                "response_format": "text",
                "text_chars": len(transcript),
            },
        )

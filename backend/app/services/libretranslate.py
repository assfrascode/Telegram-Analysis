from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.observability.metrics import observe_model_call

settings = get_settings()


@dataclass(slots=True)
class LibreTranslationResult:
    translated_text: str
    detected_language: str | None
    detected_confidence: float | None
    raw_response: dict[str, Any]


def _detected_item(value: Any, index: int) -> dict[str, Any] | None:
    if isinstance(value, list):
        if index >= len(value):
            return None
        item = value[index]
        return item if isinstance(item, dict) else None
    if isinstance(value, dict):
        return value
    return None


def _confidence(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_translate_response(
    data: dict[str, Any],
    texts_count: int,
) -> list[LibreTranslationResult]:
    raw_translated = data.get("translatedText")
    if texts_count == 1 and isinstance(raw_translated, str):
        translated_texts = [raw_translated]
    elif isinstance(raw_translated, list) and all(isinstance(item, str) for item in raw_translated):
        translated_texts = list(raw_translated)
    else:
        raise ValueError("LibreTranslate response did not contain translatedText")

    if len(translated_texts) != texts_count:
        count = len(translated_texts)
        raise ValueError(
            f"LibreTranslate returned {count} translation(s) for {texts_count} text(s)"
        )

    results: list[LibreTranslationResult] = []
    detected = data.get("detectedLanguage")
    for index, translated_text in enumerate(translated_texts):
        detected_language = _detected_item(detected, index)
        language = detected_language.get("language") if detected_language else None
        confidence = _confidence(detected_language.get("confidence") if detected_language else None)
        results.append(
            LibreTranslationResult(
                translated_text=translated_text,
                detected_language=str(language) if language else None,
                detected_confidence=confidence,
                raw_response={
                    "translatedText": translated_text,
                    "detectedLanguage": detected_language,
                },
            )
        )
    return results


class LibreTranslateClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        configured_base_url = base_url if base_url is not None else settings.libretranslate_base_url
        self.base_url = configured_base_url.strip()
        self.api_key = (api_key if api_key is not None else settings.libretranslate_api_key).strip()
        self.timeout = timeout if timeout is not None else settings.libretranslate_timeout_seconds
        self.transport = transport

    async def translate_texts(
        self,
        texts: list[str],
        *,
        target_language: str,
        source_language: str = "auto",
    ) -> list[LibreTranslationResult]:
        if not texts:
            return []
        if not self.base_url:
            raise ValueError("LIBRETRANSLATE_BASE_URL is not configured")

        form_data: list[tuple[str, str]] = [("q", text) for text in texts]
        form_data.extend(
            [
                ("source", source_language),
                ("target", target_language),
                ("format", "text"),
            ]
        )
        if self.api_key:
            form_data.append(("api_key", self.api_key))

        async with observe_model_call("libretranslate", "translate", "default"):
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url.rstrip('/')}/translate",
                    content=urlencode(form_data).encode("utf-8"),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                response.raise_for_status()
                data = response.json()

        if not isinstance(data, dict):
            raise ValueError("LibreTranslate response must be a JSON object")
        return normalize_translate_response(data, len(texts))

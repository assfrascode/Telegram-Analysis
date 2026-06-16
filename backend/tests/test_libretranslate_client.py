import asyncio
import os
from urllib.parse import parse_qsl

import httpx

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.services.libretranslate import LibreTranslateClient, normalize_translate_response


def test_translate_texts_sends_libretranslate_form_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["pairs"] = parse_qsl(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "translatedText": ["Hello", "World"],
                "detectedLanguage": [
                    {"language": "de", "confidence": 98.5},
                    {"language": "fr", "confidence": 87},
                ],
            },
        )

    client = LibreTranslateClient(
        base_url="http://libretranslate.local",
        api_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    results = asyncio.run(client.translate_texts(["Hallo", "Monde"], target_language="en"))

    assert captured["url"] == "http://libretranslate.local/translate"
    assert captured["pairs"] == [
        ("q", "Hallo"),
        ("q", "Monde"),
        ("source", "auto"),
        ("target", "en"),
        ("format", "text"),
        ("api_key", "secret-key"),
    ]
    assert [result.translated_text for result in results] == ["Hello", "World"]
    assert results[0].detected_language == "de"
    assert results[0].detected_confidence == 98.5


def test_normalize_translate_response_accepts_single_string_result() -> None:
    results = normalize_translate_response(
        {
            "translatedText": "Hello",
            "detectedLanguage": {"language": "de", "confidence": 100},
        },
        texts_count=1,
    )

    assert len(results) == 1
    assert results[0].translated_text == "Hello"
    assert results[0].detected_language == "de"
    assert results[0].detected_confidence == 100.0
    assert results[0].raw_response["translatedText"] == "Hello"


def test_normalize_translate_response_rejects_count_mismatch() -> None:
    try:
        normalize_translate_response({"translatedText": ["Hello"]}, texts_count=2)
    except ValueError as exc:
        assert "translation(s) for 2 text(s)" in str(exc)
    else:
        raise AssertionError("Expected ValueError")

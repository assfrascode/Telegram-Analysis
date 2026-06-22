import asyncio
import os

os.environ.setdefault("SECRET_KEY", "test-secret")

import pytest

from app.llm.vllm_gateway import (
    VLLMGateway,
    build_multimodal_content,
    extract_chat_completion_text,
    multimodal_content_type,
    settings,
)


def test_multimodal_content_type_maps_images_and_videos() -> None:
    assert multimodal_content_type("image") == "image_url"
    assert multimodal_content_type("video") == "video_url"
    assert multimodal_content_type("unknown") == "image_url"


def test_build_multimodal_content_uses_video_url_shape() -> None:
    content = build_multimodal_content(
        media_url="http://minio:9000/bucket/video.mp4",
        media_type="video",
        prompt="Describe neutrally",
    )

    assert content[0] == {"type": "text", "text": "Describe neutrally"}
    assert content[1] == {
        "type": "video_url",
        "video_url": {"url": "http://minio:9000/bucket/video.mp4"},
    }


def test_extract_chat_completion_text_from_string_content() -> None:
    response = {"choices": [{"message": {"content": "  Ein Bild.  "}}]}
    assert extract_chat_completion_text(response) == "Ein Bild."


def test_extract_chat_completion_text_from_segmented_content() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "content": [
                        {"type": "text", "text": "Teil 1"},
                        {"type": "text", "text": "Teil 2"},
                    ]
                }
            }
        ]
    }
    assert extract_chat_completion_text(response) == "Teil 1\nTeil 2"


def test_synthesize_bluf_returns_mock_response(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_mock_enabled", True)

    bluf = asyncio.run(VLLMGateway().synthesize_bluf("Frage 1: Kurzantwort"))

    assert bluf.startswith("[MOCK_BLUF]")
    assert "Frage 1" in bluf


def test_synthesize_bluf_rejects_empty_model_response(monkeypatch) -> None:
    async def empty_chat_completion(self, **kwargs):
        return {"choices": [{"message": {"content": "   "}}]}

    monkeypatch.setattr(settings, "llm_mock_enabled", False)
    monkeypatch.setattr(VLLMGateway, "chat_completion", empty_chat_completion)

    with pytest.raises(ValueError, match="empty text"):
        asyncio.run(VLLMGateway().synthesize_bluf("Frage 1: Kurzantwort"))

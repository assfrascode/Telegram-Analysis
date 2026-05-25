import os

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.llm.vllm_gateway import (
    build_multimodal_content,
    extract_chat_completion_text,
    multimodal_content_type,
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

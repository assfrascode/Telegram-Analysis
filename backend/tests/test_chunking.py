import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import MediaAnalysis, MessageTranslation, StepStatus, TelegramMedia, TelegramMessage
from app.services.chunking import MediaAttachment, build_chunks, render_message_block


def _message(message_id: int, text: str) -> TelegramMessage:
    return TelegramMessage(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        telegram_message_id=message_id,
        timestamp=datetime(2025, 1, 1, 12, message_id % 60, tzinfo=timezone.utc),
        sender_id="user123",
        sender_name="Alice",
        message_type="message",
        text=text,
        raw={},
    )


def test_render_message_block_appends_image_description() -> None:
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        media_type="image",
        original_path="photos/photo_1.jpg",
        status=StepStatus.completed,
    )
    analysis = MediaAnalysis(
        media_id=media.id,
        model_name="mock-vision",
        prompt_version="neutral-v1",
        description="Ein neutrales Mock-Bild.",
        raw_response={},
    )

    block = render_message_block(_message(42, "Siehe Bild"), attachments=[MediaAttachment(media=media, analysis=analysis)])

    assert "[msg_id=42]" in block.text
    assert "Siehe Bild" in block.text
    assert "IMAGE_DESCRIPTION:" in block.text
    assert "Ein neutrales Mock-Bild." in block.text
    assert "MEDIA_PATH: photos/photo_1.jpg" in block.text
    assert block.has_media is True


def test_render_message_block_marks_missing_video() -> None:
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        media_type="video",
        original_path="videos/video_1.mp4",
        status=StepStatus.failed_permanent,
        missing_reason="not_included_in_export",
    )

    block = render_message_block(_message(7, ""), attachments=[MediaAttachment(media=media, analysis=None)])

    assert "[NO_TEXT]" in block.text
    assert "[VIDEO_DESCRIPTION_MISSING]" in block.text
    assert "not_included_in_export" in block.text


def test_render_message_block_appends_saved_translation() -> None:
    message = _message(11, "Guten Morgen")
    translation = MessageTranslation(
        job_id=message.job_id,
        message_id=message.id,
        provider="libretranslate",
        source_text_hash="hash",
        detected_source_language="de",
        target_language="en",
        translated_text="Good morning",
        raw_response={},
    )

    block = render_message_block(message, translation=translation)

    assert "Guten Morgen" in block.text
    assert "ENGLISH_TRANSLATION:" in block.text
    assert "Good morning" in block.text


def test_build_chunks_preserves_order_and_overlap() -> None:
    blocks = [render_message_block(_message(i, "x" * 600)) for i in range(1, 8)]
    chunks = build_chunks(blocks, target_chars=1500, overlap_messages=1)

    assert len(chunks) > 1
    assert chunks[0].telegram_message_ids[0] == 1
    assert chunks[1].telegram_message_ids[0] == chunks[0].telegram_message_ids[-1]
    assert all(chunk.chunk_hash for chunk in chunks)

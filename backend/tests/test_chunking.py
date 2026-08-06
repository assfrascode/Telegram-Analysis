import os
import uuid
from datetime import datetime, timezone

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import (
    MediaAnalysis,
    MediaTranscript,
    MediaTranscriptTranslation,
    MessageTranslation,
    StepStatus,
    TelegramMedia,
    TelegramMessage,
)
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


def test_render_message_block_appends_audio_transcript() -> None:
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        media_type="audio",
        original_path="files/audio_1.mp3",
        status=StepStatus.completed,
    )
    transcript = MediaTranscript(
        job_id=media.job_id,
        media_id=media.id,
        provider="openai",
        model_name="whisper-1",
        response_format="text",
        status=StepStatus.completed,
        transcript_text="Das ist ein transkribierter Audiobeitrag.",
        raw_response={},
    )

    block = render_message_block(
        _message(43, "Audio anbei"),
        attachments=[MediaAttachment(media=media, transcript=transcript)],
    )

    assert "AUDIO_TRANSCRIPT:" in block.text
    assert "Das ist ein transkribierter Audiobeitrag." in block.text
    assert "MEDIA_DESCRIPTION" not in block.text


def test_render_message_block_keeps_video_description_and_transcript() -> None:
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        message_id=uuid.uuid4(),
        media_type="video",
        original_path="videos/video_1.mp4",
        status=StepStatus.completed,
    )
    analysis = MediaAnalysis(
        media_id=media.id,
        model_name="mock-vision",
        prompt_version="neutral-v1",
        description="Ein neutrales Mock-Video.",
        raw_response={},
    )
    transcript = MediaTranscript(
        job_id=media.job_id,
        media_id=media.id,
        provider="openai",
        model_name="whisper-1",
        response_format="text",
        status=StepStatus.completed,
        transcript_text="Gesprochener Inhalt des Videos.",
        raw_response={},
    )

    block = render_message_block(
        _message(44, "Video anbei"),
        attachments=[MediaAttachment(media=media, analysis=analysis, transcript=transcript)],
    )

    assert "VIDEO_DESCRIPTION:" in block.text
    assert "Ein neutrales Mock-Video." in block.text
    assert "VIDEO_TRANSCRIPT:" in block.text
    assert "Gesprochener Inhalt des Videos." in block.text


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


def test_render_message_block_uses_only_english_message_and_transcript() -> None:
    message = _message(12, "Guten Morgen")
    message_translation = MessageTranslation(
        job_id=message.job_id,
        message_id=message.id,
        provider="libretranslate",
        source_text_hash="message-hash",
        detected_source_language="de",
        target_language="en",
        translated_text="Good morning",
        raw_response={},
    )
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=message.job_id,
        message_id=message.id,
        media_type="audio",
        original_path="files/audio_english.mp3",
        status=StepStatus.completed,
    )
    transcript = MediaTranscript(
        id=uuid.uuid4(),
        job_id=message.job_id,
        media_id=media.id,
        provider="openai",
        model_name="whisper-1",
        response_format="text",
        status=StepStatus.completed,
        transcript_text="Gesprochener Inhalt.",
        raw_response={},
    )
    transcript_translation = MediaTranscriptTranslation(
        job_id=message.job_id,
        transcript_id=transcript.id,
        provider="libretranslate",
        source_text_hash="transcript-hash",
        detected_source_language="de",
        target_language="en",
        translated_text="Spoken content.",
        raw_response={},
    )

    block = render_message_block(
        message,
        attachments=[
            MediaAttachment(
                media=media,
                transcript=transcript,
                transcript_translation=transcript_translation,
            )
        ],
        translation=message_translation,
        english_only=True,
    )

    assert "Good morning" in block.text
    assert "Spoken content." in block.text
    assert "Guten Morgen" not in block.text
    assert "Gesprochener Inhalt." not in block.text
    assert "ENGLISH_TRANSLATION:" not in block.text


def test_render_message_block_never_falls_back_to_source_in_english_mode() -> None:
    block = render_message_block(_message(13, "Nicht übersetzt"), english_only=True)

    assert "[ENGLISH_TRANSLATION_UNAVAILABLE]" in block.text
    assert "Nicht übersetzt" not in block.text


def test_build_chunks_preserves_order_and_overlap() -> None:
    blocks = [render_message_block(_message(i, "x" * 600)) for i in range(1, 8)]
    chunks = build_chunks(blocks, target_chars=1500, overlap_messages=1)

    assert len(chunks) > 1
    assert chunks[0].telegram_message_ids[0] == 1
    assert chunks[1].telegram_message_ids[0] == chunks[0].telegram_message_ids[-1]
    assert all(chunk.chunk_hash for chunk in chunks)

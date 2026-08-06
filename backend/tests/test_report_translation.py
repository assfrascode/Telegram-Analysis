import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import (
    MediaTranscript,
    MediaTranscriptTranslation,
    MessageTranslation,
    StepStatus,
    TelegramMedia,
    TelegramMessage,
)
from app.services.report_builder import (
    ReportEvidenceChunk,
    ReportQuestion,
    build_report_message,
)


def test_report_message_and_subreport_render_saved_translation() -> None:
    message = TelegramMessage(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        telegram_message_id=42,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sender_id="user-1",
        sender_name="Alice",
        message_type="message",
        text="Guten Morgen",
        raw={},
    )
    translation = MessageTranslation(
        job_id=message.job_id,
        message_id=message.id,
        provider="libretranslate",
        source_text_hash="hash",
        detected_source_language="de",
        detected_source_confidence=99.0,
        target_language="en",
        translated_text="Good morning",
        raw_response={},
    )

    report_message = build_report_message(message, translation=translation)
    assert report_message.translation_text == "Good morning"
    assert report_message.translation_source_language == "de"

    question = ReportQuestion(
        index=1,
        filename="questions/q_001.html",
        question="What happened?",
        answer="Answer",
        short_answer="Answer",
        status="completed",
        retrieval_k=50,
        rerank_k=15,
        evidence=[
            ReportEvidenceChunk(
                id=str(uuid.uuid4()),
                chunk_index=0,
                chunk_hash="hash",
                retrieval_rank=1,
                retrieval_score=None,
                rerank_rank=1,
                rerank_score=None,
                start_timestamp="2026-01-01 00:00:00 UTC",
                end_timestamp="2026-01-01 00:00:00 UTC",
                text="chunk text",
                messages=[report_message],
            )
        ],
    )
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml", "html.j2"]),
    )

    html = env.get_template("subreport.html.j2").render(
        job=object(),
        question=question,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stats={},
    )

    assert "translation-panel" in html
    assert "EN translation" in html
    assert "Good morning" in html
    assert "detected de" in html


def test_report_message_and_subreport_render_media_transcript() -> None:
    message = TelegramMessage(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        telegram_message_id=43,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sender_id="user-1",
        sender_name="Alice",
        message_type="message",
        text="Audio",
        raw={},
    )
    media = TelegramMedia(
        id=uuid.uuid4(),
        job_id=message.job_id,
        message_id=message.id,
        media_type="audio",
        original_path="files/audio.mp3",
        status=StepStatus.completed,
    )
    transcript = MediaTranscript(
        job_id=message.job_id,
        media_id=media.id,
        provider="openai",
        model_name="whisper-1",
        response_format="text",
        status=StepStatus.completed,
        transcript_text="Transkribierter Inhalt.",
        raw_response={},
    )

    report_message = build_report_message(message, media_items=[(media, None, transcript, None)])
    assert report_message.media[0].transcript_text == "Transkribierter Inhalt."
    assert report_message.media[0].transcript_model == "whisper-1"

    question = ReportQuestion(
        index=1,
        filename="questions/q_001.html",
        question="What happened?",
        answer="Answer",
        short_answer="Answer",
        status="completed",
        retrieval_k=50,
        rerank_k=15,
        evidence=[
            ReportEvidenceChunk(
                id=str(uuid.uuid4()),
                chunk_index=0,
                chunk_hash="hash",
                retrieval_rank=1,
                retrieval_score=None,
                rerank_rank=1,
                rerank_score=None,
                start_timestamp="2026-01-01 00:00:00 UTC",
                end_timestamp="2026-01-01 00:00:00 UTC",
                text="chunk text",
                messages=[report_message],
            )
        ],
    )
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml", "html.j2"]),
    )

    html = env.get_template("subreport.html.j2").render(
        job=object(),
        question=question,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        stats={},
    )

    assert "AUDIO_TRANSCRIPT" in html
    assert "Transkribierter Inhalt." in html
    assert "whisper-1" in html


def test_report_uses_only_english_evidence_when_requested() -> None:
    message = TelegramMessage(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        telegram_message_id=44,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        sender_name="Alice",
        text="Guten Morgen",
        raw={},
    )
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
        original_path="files/audio.mp3",
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

    report_message = build_report_message(
        message,
        media_items=[(media, None, transcript, transcript_translation)],
        translation=message_translation,
        english_only=True,
    )

    assert report_message.text == "Good morning"
    assert report_message.translation_text is None
    assert report_message.translation_applied is True
    assert report_message.media[0].transcript_text == "Spoken content."
    assert report_message.media[0].transcript_translation_source_language == "de"
    assert "Guten Morgen" not in report_message.text
    assert "Gesprochener Inhalt." not in report_message.media[0].transcript_text

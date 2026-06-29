
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models import (
    MediaAnalysis,
    MediaTranscript,
    MessageChunk,
    MessageTranslation,
    RetrievalHit,
    StepStatus,
    TelegramMedia,
    TelegramMessage,
)


NO_ANSWER_BLUF = "Es wurden keine beantworteten Fragen gefunden."
MISSING_SHORT_ANSWER = "Noch keine Kurzantwort gespeichert."


def isoformat_or_empty(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def display_timestamp(value: datetime | None) -> str:
    if value is None:
        return "Unbekannter Zeitpunkt"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def normalize_message_text(value: str | None) -> str:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text if text else "[NO_TEXT]"


def format_reaction_chip(value: dict[str, Any]) -> str | None:
    """Return a compact Telegram-like reaction label, e.g. "👍 3".

    Telegram exports differ between versions. Common keys include "emoji",
    "emoticon", "reaction", "type" and "count". Keep the output compact and
    avoid exposing raw JSON in the report UI.
    """
    if not isinstance(value, dict):
        return None

    emoji = (
        value.get("emoji")
        or value.get("emoticon")
        or value.get("reaction")
        or value.get("symbol")
    )
    if isinstance(emoji, dict):
        emoji = emoji.get("emoji") or emoji.get("emoticon") or emoji.get("type")
    if not emoji:
        reaction_type = str(value.get("type") or "").strip()
        if reaction_type and reaction_type not in {"emoji", "custom_emoji"}:
            emoji = reaction_type
        elif reaction_type == "custom_emoji":
            emoji = "custom"
        else:
            emoji = "reaction"

    count = value.get("count")
    try:
        count_int = int(count)
    except (TypeError, ValueError):
        count_int = 0

    label = str(emoji).strip()
    if not label:
        return None
    if count_int > 1:
        return f"{label} {count_int}"
    return label


def format_reaction_chips(values: Iterable[dict[str, Any]] | None) -> list[str]:
    chips: list[str] = []
    seen: set[str] = set()
    for item in values or []:
        chip = format_reaction_chip(item)
        if not chip or chip in seen:
            continue
        seen.add(chip)
        chips.append(chip)
    return chips


def parse_uuid_list(values: Iterable[str] | None) -> list[uuid.UUID]:
    parsed: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for value in values or []:
        try:
            item = uuid.UUID(str(value))
        except (TypeError, ValueError):
            continue
        if item in seen:
            continue
        seen.add(item)
        parsed.append(item)
    return parsed


def relative_media_href_from_subreport(original_path: str | None) -> str | None:
    """Return a media href from ``report/questions/q_XXX.html`` to the Telegram root.

    Users explicitly want to download only the ``report/`` folder and then place it
    manually inside the original Telegram export directory. From a subreport at
    ``report/questions/q_001.html`` the export root is two levels up, hence
    ``../../photos/example.jpg``.
    """
    if not original_path:
        return None
    cleaned = str(original_path).replace("\\", "/").lstrip("/").strip()
    if not cleaned or cleaned.startswith("../") or "/../" in cleaned:
        return None
    return f"../../{cleaned}"


def status_label(status: StepStatus | str | None) -> str:
    value = status.value if isinstance(status, StepStatus) else str(status or "unknown")
    return value.replace("_", " ")


def _normalized_status(status: StepStatus | str | None) -> str:
    if isinstance(status, StepStatus):
        return status.value
    return str(status or "").strip()


@dataclass(slots=True)
class ReportMedia:
    id: str
    media_type: str
    original_path: str
    relative_href: str | None
    status: str
    missing_reason: str | None
    size_bytes: int | None
    sha256: str | None
    description: str | None
    analysis_model: str | None
    analysis_prompt_version: str | None
    analyzed_at: str
    transcript_text: str | None
    transcript_status: str | None
    transcript_error: str | None
    transcript_model: str | None
    transcript_provider: str | None


@dataclass(slots=True)
class ReportMessage:
    id: str
    telegram_message_id: int
    timestamp: str
    timestamp_iso: str
    edited_timestamp: str
    edited_timestamp_iso: str
    sender_id: str | None
    sender_name: str | None
    message_type: str | None
    reply_to_message_id: int | None
    forwarded_from: str | None
    reactions: list[dict[str, Any]]
    reaction_chips: list[str]
    text: str
    translation_text: str | None = None
    translation_target_language: str | None = None
    translation_source_language: str | None = None
    translation_source_confidence: float | None = None
    translation_provider: str | None = None
    media: list[ReportMedia] = field(default_factory=list)


@dataclass(slots=True)
class ReportEvidenceChunk:
    id: str
    chunk_index: int
    chunk_hash: str
    retrieval_rank: int
    retrieval_score: float | None
    rerank_rank: int | None
    rerank_score: float | None
    start_timestamp: str
    end_timestamp: str
    text: str
    messages: list[ReportMessage]


@dataclass(slots=True)
class ReportQuestion:
    index: int
    filename: str
    question: str
    answer: str
    short_answer: str
    status: str
    retrieval_k: int | None
    rerank_k: int | None
    evidence: list[ReportEvidenceChunk]


def bluf_source_questions(questions: list[ReportQuestion]) -> list[ReportQuestion]:
    """Return completed questions with real short summaries for BLUF synthesis."""
    source_questions: list[ReportQuestion] = []
    for question in questions:
        short_answer = (question.short_answer or "").strip()
        if _normalized_status(question.status) != StepStatus.completed.value:
            continue
        if not short_answer or short_answer == MISSING_SHORT_ANSWER:
            continue
        source_questions.append(question)
    return source_questions


def build_bluf_synthesis_prompt(questions: list[ReportQuestion]) -> str:
    source_questions = bluf_source_questions(questions)
    if not source_questions:
        return ""
    return build_bluf_synthesis_prompt_from_blocks(bluf_question_blocks(source_questions))


def bluf_question_blocks(questions: list[ReportQuestion]) -> list[str]:
    blocks: list[str] = []
    for question in questions:
        blocks.append(
            "\n".join(
                [
                    f"Frage {question.index}:",
                    f"Ausgangsfrage: {question.question.strip()}",
                    f"Kurzantwort: {question.short_answer.strip()}",
                ]
            )
        )
    return blocks


def build_bluf_synthesis_prompt_from_blocks(blocks: list[str]) -> str:
    if not blocks:
        return ""
    lines = [
        "Erstelle eine knappe deutsche BLUF für den Hauptreport.",
        "Nutze ausschließlich die folgenden Fragen und Kurzantworten.",
        "Fasse die wichtigsten übergreifenden Befunde zusammen, "
        "statt jede Frage einzeln aufzulisten.",
        "Nenne Unsicherheiten oder fehlende Evidenz, wenn sie in den Kurzantworten enthalten sind.",
        "Schreibe 3 bis 6 kurze Sätze oder kompakte Absätze.",
        "",
        "Fragen und Kurzantworten:",
    ]
    for block in blocks:
        lines.extend([block.strip(), ""])
    return "\n".join(lines).strip()


def build_bluf_reduce_prompt(summaries: list[str]) -> str:
    lines = [
        "Erstelle eine finale knappe deutsche BLUF aus den folgenden Teil-BLUFs.",
        "Nutze ausschließlich die Teil-BLUFs. Erfinde keine neuen Fakten.",
        "Fasse die wichtigsten übergreifenden Befunde zusammen und bewahre Unsicherheiten.",
        "Schreibe 3 bis 6 kurze Sätze oder kompakte Absätze.",
        "",
        "Teil-BLUFs:",
    ]
    for index, summary in enumerate(summaries, start=1):
        lines.extend(
            [
                f"[TEIL_BLUF index={index}]",
                summary.strip(),
                "[/TEIL_BLUF]",
                "",
            ]
        )
    return "\n".join(lines).strip()


def build_report_media(
    media: TelegramMedia,
    analysis: MediaAnalysis | None,
    transcript: MediaTranscript | None = None,
) -> ReportMedia:
    transcript_text = transcript.transcript_text.strip() if transcript else ""
    return ReportMedia(
        id=str(media.id),
        media_type=media.media_type,
        original_path=media.original_path,
        relative_href=(
            None
            if media.source_media_id is not None
            else relative_media_href_from_subreport(media.original_path)
        ),
        status=status_label(media.status),
        missing_reason=media.missing_reason,
        size_bytes=media.size_bytes,
        sha256=media.sha256,
        description=(analysis.description.strip() if analysis and analysis.description else None),
        analysis_model=(analysis.model_name if analysis else None),
        analysis_prompt_version=(analysis.prompt_version if analysis else None),
        analyzed_at=isoformat_or_empty(media.analyzed_at),
        transcript_text=transcript_text or None,
        transcript_status=(status_label(transcript.status) if transcript else None),
        transcript_error=(transcript.error_message if transcript else None),
        transcript_model=(transcript.model_name if transcript else None),
        transcript_provider=(transcript.provider if transcript else None),
    )


def build_report_message(
    message: TelegramMessage,
    media_items: list[tuple[TelegramMedia, MediaAnalysis | None, MediaTranscript | None]] | None = None,
    translation: MessageTranslation | None = None,
) -> ReportMessage:
    translation_text = translation.translated_text.strip() if translation else ""
    return ReportMessage(
        id=str(message.id),
        telegram_message_id=message.telegram_message_id,
        timestamp=display_timestamp(message.timestamp),
        timestamp_iso=isoformat_or_empty(message.timestamp),
        edited_timestamp=display_timestamp(message.edited_timestamp) if message.edited_timestamp else "",
        edited_timestamp_iso=isoformat_or_empty(message.edited_timestamp),
        sender_id=message.sender_id,
        sender_name=message.sender_name,
        message_type=message.message_type,
        reply_to_message_id=message.reply_to_message_id,
        forwarded_from=message.forwarded_from,
        reactions=list(message.reactions or []),
        reaction_chips=format_reaction_chips(message.reactions or []),
        text=normalize_message_text(message.text),
        translation_text=translation_text or None,
        translation_target_language=(translation.target_language if translation else None),
        translation_source_language=(translation.detected_source_language if translation else None),
        translation_source_confidence=(
            translation.detected_source_confidence if translation else None
        ),
        translation_provider=(translation.provider if translation else None),
        media=[
            build_report_media(media, analysis, transcript)
            for media, analysis, transcript in (media_items or [])
        ],
    )


def build_report_evidence_chunk(
    *,
    hit: RetrievalHit,
    chunk: MessageChunk,
    messages: list[ReportMessage],
) -> ReportEvidenceChunk:
    return ReportEvidenceChunk(
        id=str(chunk.id),
        chunk_index=chunk.chunk_index,
        chunk_hash=chunk.chunk_hash,
        retrieval_rank=hit.retrieval_rank,
        retrieval_score=hit.retrieval_score,
        rerank_rank=hit.rerank_rank,
        rerank_score=hit.rerank_score,
        start_timestamp=display_timestamp(chunk.start_timestamp),
        end_timestamp=display_timestamp(chunk.end_timestamp),
        text=chunk.text,
        messages=messages,
    )


def make_bluf(questions: list[ReportQuestion], *, max_items: int = 5) -> str:
    completed = bluf_source_questions(questions)
    if not completed:
        return NO_ANSWER_BLUF

    lines: list[str] = []
    for question in completed[:max_items]:
        lines.append(f"Frage {question.index}: {question.short_answer}")
    if len(completed) > max_items:
        lines.append(f"Weitere {len(completed) - max_items} Antworten sind in den Subreports enthalten.")
    return "\n".join(lines)

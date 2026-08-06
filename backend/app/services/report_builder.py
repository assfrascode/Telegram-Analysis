
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models import (
    MediaAnalysis,
    MediaTranscript,
    MediaTranscriptTranslation,
    MessageChunk,
    MessageTranslation,
    RetrievalHit,
    StepStatus,
    TelegramMedia,
    TelegramMessage,
)


NO_ANSWER_BLUF = "No answered questions were available for this report."
MISSING_SHORT_ANSWER = "No summary has been saved yet."


def isoformat_or_empty(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def display_timestamp(value: datetime | None) -> str:
    if value is None:
        return "Unknown time"
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

    The ``report/`` folder is placed inside the original Telegram export either
    automatically by Download All or manually after a report-only download. From a
    subreport at ``report/questions/q_001.html`` the export root is two levels up, hence
    ``../../photos/example.jpg``.
    """
    if not original_path:
        return None
    cleaned = str(original_path).replace("\\", "/").lstrip("/").strip()
    if not cleaned or cleaned.startswith("../") or "/../" in cleaned:
        return None
    return f"../../{cleaned}"


def relative_media_href_from_gallery(original_path: str | None) -> str | None:
    """Return a media href from ``report/media_gallery.html`` to the export root."""
    if not original_path:
        return None
    cleaned = str(original_path).replace("\\", "/").lstrip("/").strip()
    if not cleaned or cleaned.startswith("../") or "/../" in cleaned:
        return None
    return f"../{cleaned}"


def report_media_link(
    media: TelegramMedia,
    *,
    from_subreport: bool,
) -> tuple[str | None, str | None]:
    """Return an offline-safe media link and a concise user-facing fallback reason.

    Media processing state and file availability are deliberately kept separate:
    an attachment can still be opened when its AI analysis failed. Collector media,
    on the other hand, does not live beside an exported offline report.
    """
    if media.source_media_id is not None:
        return None, "Collector file not included"

    if not media.minio_object_key:
        if media.missing_reason == "unsafe_path":
            return None, "Invalid file reference"
        if media.missing_reason == "not_included_in_export":
            return None, "Not included in export"
        return None, "File missing"

    href_builder = (
        relative_media_href_from_subreport
        if from_subreport
        else relative_media_href_from_gallery
    )
    relative_href = href_builder(media.original_path)
    if relative_href is None:
        return None, "Invalid file reference"
    return relative_href, None


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
    unavailable_reason: str | None
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
    transcript_translation_source_language: str | None = None
    transcript_translation_source_confidence: float | None = None
    transcript_translation_provider: str | None = None


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
    translation_applied: bool = False
    media: list[ReportMedia] = field(default_factory=list)


@dataclass(slots=True)
class ReportGalleryItem:
    id: str
    media_type: str
    filename: str
    original_path: str
    relative_href: str | None
    link_unavailable_reason: str | None
    status: str
    missing_reason: str | None
    size_bytes: int | None
    telegram_message_id: int | None
    timestamp: str
    timestamp_iso: str
    sender_id: str | None
    sender_name: str | None


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
                    f"Question {question.index}:",
                    f"Original question: {question.question.strip()}",
                    f"Short answer: {question.short_answer.strip()}",
                ]
            )
        )
    return blocks


def build_bluf_synthesis_prompt_from_blocks(blocks: list[str]) -> str:
    if not blocks:
        return ""
    lines = [
        "Write a concise English bottom-line summary for the main report.",
        "Use only the questions and short answers provided below.",
        "Synthesize the most important findings instead of listing every question.",
        "Retain uncertainty or missing evidence when it appears in the short answers.",
        "Write 3 to 6 short sentences or compact paragraphs.",
        "Respond in English even when the source question is in another language.",
        "",
        "Questions and short answers:",
    ]
    for block in blocks:
        lines.extend([block.strip(), ""])
    return "\n".join(lines).strip()


def build_bluf_reduce_prompt(summaries: list[str]) -> str:
    lines = [
        "Write a final concise English bottom-line summary from the partial summaries below.",
        "Use only the partial summaries. Do not introduce new facts.",
        "Synthesize the most important findings and retain uncertainty.",
        "Write 3 to 6 short sentences or compact paragraphs.",
        "",
        "Partial summaries:",
    ]
    for index, summary in enumerate(summaries, start=1):
        lines.extend(
            [
                f"[PARTIAL_SUMMARY index={index}]",
                summary.strip(),
                "[/PARTIAL_SUMMARY]",
                "",
            ]
        )
    return "\n".join(lines).strip()


def build_report_media(
    media: TelegramMedia,
    analysis: MediaAnalysis | None,
    transcript: MediaTranscript | None = None,
    transcript_translation: MediaTranscriptTranslation | None = None,
    *,
    english_only: bool = False,
) -> ReportMedia:
    source_transcript_text = transcript.transcript_text.strip() if transcript else ""
    translated_transcript_text = (
        transcript_translation.translated_text.strip() if transcript_translation else ""
    )
    transcript_text = (
        translated_transcript_text if english_only and source_transcript_text else source_transcript_text
    )
    transcript_error = transcript.error_message if transcript else None
    if english_only and source_transcript_text and not translated_transcript_text:
        transcript_error = "English translation unavailable"
    relative_href, unavailable_reason = report_media_link(media, from_subreport=True)
    return ReportMedia(
        id=str(media.id),
        media_type=media.media_type,
        original_path=media.original_path,
        relative_href=relative_href,
        unavailable_reason=unavailable_reason,
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
        transcript_error=transcript_error,
        transcript_model=(transcript.model_name if transcript else None),
        transcript_provider=(transcript.provider if transcript else None),
        transcript_translation_source_language=(
            transcript_translation.detected_source_language if transcript_translation else None
        ),
        transcript_translation_source_confidence=(
            transcript_translation.detected_source_confidence if transcript_translation else None
        ),
        transcript_translation_provider=(
            transcript_translation.provider if transcript_translation else None
        ),
    )


def build_report_message(
    message: TelegramMessage,
    media_items: list[
        tuple[
            TelegramMedia,
            MediaAnalysis | None,
            MediaTranscript | None,
            MediaTranscriptTranslation | None,
        ]
    ] | None = None,
    translation: MessageTranslation | None = None,
    *,
    english_only: bool = False,
) -> ReportMessage:
    source_text = normalize_message_text(message.text)
    translation_text = translation.translated_text.strip() if translation else ""
    translation_applied = bool(
        english_only and source_text != "[NO_TEXT]" and translation_text
    )
    display_text = source_text
    if english_only and source_text != "[NO_TEXT]":
        display_text = translation_text or "[ENGLISH_TRANSLATION_UNAVAILABLE]"
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
        text=display_text,
        translation_text=(translation_text or None) if not english_only else None,
        translation_target_language=(translation.target_language if translation else None),
        translation_source_language=(translation.detected_source_language if translation else None),
        translation_source_confidence=(
            translation.detected_source_confidence if translation else None
        ),
        translation_provider=(translation.provider if translation else None),
        translation_applied=translation_applied,
        media=[
            build_report_media(
                media,
                analysis,
                transcript,
                transcript_translation,
                english_only=english_only,
            )
            for media, analysis, transcript, transcript_translation in (media_items or [])
        ],
    )


def build_report_gallery_item(
    media: TelegramMedia,
    message: TelegramMessage | None,
) -> ReportGalleryItem:
    relative_href, link_unavailable_reason = report_media_link(media, from_subreport=False)

    normalized_path = str(media.original_path or "").replace("\\", "/").rstrip("/")
    filename = normalized_path.rsplit("/", 1)[-1] or "Unnamed attachment"
    return ReportGalleryItem(
        id=str(media.id),
        media_type=media.media_type,
        filename=filename,
        original_path=media.original_path,
        relative_href=relative_href,
        link_unavailable_reason=link_unavailable_reason,
        status=status_label(media.status),
        missing_reason=media.missing_reason,
        size_bytes=media.size_bytes,
        telegram_message_id=(message.telegram_message_id if message else None),
        timestamp=(display_timestamp(message.timestamp) if message else "Unknown time"),
        timestamp_iso=(isoformat_or_empty(message.timestamp) if message else ""),
        sender_id=(message.sender_id if message else None),
        sender_name=(message.sender_name if message else None),
    )


def sort_report_gallery_items(items: list[ReportGalleryItem]) -> list[ReportGalleryItem]:
    return sorted(
        items,
        key=lambda item: (
            not bool(item.timestamp_iso),
            item.timestamp_iso,
            item.telegram_message_id is None,
            item.telegram_message_id or 0,
            item.original_path.casefold(),
            item.id,
        ),
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
        lines.append(f"Question {question.index}: {question.short_answer}")
    if len(completed) > max_items:
        lines.append(
            f"Another {len(completed) - max_items} answers are available in the question reports."
        )
    return "\n".join(lines)

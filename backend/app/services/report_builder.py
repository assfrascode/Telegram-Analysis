
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from app.models import MediaAnalysis, MessageChunk, RetrievalHit, StepStatus, TelegramMedia, TelegramMessage


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


def build_report_media(media: TelegramMedia, analysis: MediaAnalysis | None) -> ReportMedia:
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
    )


def build_report_message(
    message: TelegramMessage,
    media_items: list[tuple[TelegramMedia, MediaAnalysis | None]] | None = None,
) -> ReportMessage:
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
        media=[build_report_media(media, analysis) for media, analysis in (media_items or [])],
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
    completed = [question for question in questions if question.short_answer]
    if not completed:
        return "Es wurden keine beantworteten Fragen gefunden."

    lines: list[str] = []
    for question in completed[:max_items]:
        lines.append(f"Frage {question.index}: {question.short_answer}")
    if len(completed) > max_items:
        lines.append(f"Weitere {len(completed) - max_items} Antworten sind in den Subreports enthalten.")
    return "\n".join(lines)

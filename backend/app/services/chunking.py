
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models import MediaAnalysis, MessageTranslation, StepStatus, TelegramMedia, TelegramMessage


@dataclass(slots=True)
class MediaAttachment:
    media: TelegramMedia
    analysis: MediaAnalysis | None = None


@dataclass(slots=True)
class MessageBlock:
    message: TelegramMessage
    text: str
    media_ids: list[str] = field(default_factory=list)
    media_types: list[str] = field(default_factory=list)
    media_paths: list[str] = field(default_factory=list)
    has_media: bool = False


@dataclass(slots=True)
class BuiltChunk:
    chunk_index: int
    text: str
    chunk_hash: str
    message_db_ids: list[str]
    telegram_message_ids: list[int]
    sender_ids: list[str]
    sender_names: list[str]
    message_types: list[str]
    media_ids: list[str]
    media_types: list[str]
    media_paths: list[str]
    start_timestamp: datetime | None
    end_timestamp: datetime | None
    has_media: bool


def stable_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iso_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown-time"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _clean_text(text: str | None) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text if text else "[NO_TEXT]"


def _media_label(media_type: str | None) -> str:
    normalized = (media_type or "media").upper()
    if normalized == "IMAGE":
        return "IMAGE_DESCRIPTION"
    if normalized == "VIDEO":
        return "VIDEO_DESCRIPTION"
    return "MEDIA_DESCRIPTION"


def _translation_label(target_language: str | None) -> str:
    normalized = (target_language or "").strip().lower()
    if normalized == "en":
        return "ENGLISH_TRANSLATION"
    if normalized:
        return f"TRANSLATION_{normalized.upper()}"
    return "TRANSLATION"


def render_message_block(
    message: TelegramMessage,
    attachments: list[MediaAttachment] | None = None,
    translation: MessageTranslation | None = None,
) -> MessageBlock:
    """Render one Telegram message into a retrieval-ready text block.

    The block keeps original metadata visible and appends available media
    descriptions. Missing or permanently failed media are kept explicit so later
    answers and reports can show that evidence was unavailable.
    """
    attachments = attachments or []
    header_parts = [
        f"[{iso_timestamp(message.timestamp)}]",
        f"[msg_id={message.telegram_message_id}]",
        f"[user={message.sender_name or 'unknown'}]",
    ]
    if message.sender_id:
        header_parts.append(f"[sender_id={message.sender_id}]")
    if message.message_type:
        header_parts.append(f"[type={message.message_type}]")

    lines: list[str] = [" ".join(header_parts)]
    if message.reply_to_message_id is not None:
        lines.append(f"Reply-To: {message.reply_to_message_id}")
    if message.forwarded_from:
        lines.append(f"Forwarded-From: {message.forwarded_from}")
    if message.edited_timestamp is not None:
        lines.append(f"Edited-At: {iso_timestamp(message.edited_timestamp)}")
    if message.reactions:
        lines.append(f"Reactions: {message.reactions}")

    lines.append(_clean_text(message.text))
    if translation and translation.translated_text.strip():
        lines.append("")
        lines.append(f"{_translation_label(translation.target_language)}:")
        lines.append(translation.translated_text.strip())

    media_ids: list[str] = []
    media_types: list[str] = []
    media_paths: list[str] = []

    for attachment in attachments:
        media = attachment.media
        media_ids.append(str(media.id))
        media_types.append(media.media_type)
        media_paths.append(media.original_path)

        label = _media_label(media.media_type)
        if attachment.analysis and attachment.analysis.description.strip():
            lines.append("")
            lines.append(f"{label}:")
            lines.append(attachment.analysis.description.strip())
            lines.append(f"MEDIA_PATH: {media.original_path}")
            continue

        if media.status == StepStatus.failed_permanent:
            lines.append("")
            lines.append(f"[{label}_MISSING]")
            lines.append("Datei konnte nicht analysiert werden.")
            lines.append(f"Grund: {media.missing_reason or 'unknown'}")
            lines.append(f"MEDIA_PATH: {media.original_path}")
            continue

        lines.append("")
        lines.append(f"[{label}_UNANALYZED]")
        lines.append("Datei hat keine gespeicherte Medienbeschreibung.")
        lines.append(f"MEDIA_PATH: {media.original_path}")

    return MessageBlock(
        message=message,
        text="\n".join(lines).strip(),
        media_ids=media_ids,
        media_types=media_types,
        media_paths=media_paths,
        has_media=bool(media_ids),
    )


def _unique_preserve_order(values: list[Any]) -> list[Any]:
    seen: set[Any] = set()
    result: list[Any] = []
    for value in values:
        if value is None or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _build_chunk_from_blocks(index: int, blocks: list[MessageBlock]) -> BuiltChunk:
    text = "\n\n---\n\n".join(block.text for block in blocks).strip()
    timestamps = [block.message.timestamp for block in blocks if block.message.timestamp is not None]
    media_ids: list[str] = []
    media_types: list[str] = []
    media_paths: list[str] = []
    sender_ids: list[str] = []
    sender_names: list[str] = []
    message_types: list[str] = []

    for block in blocks:
        media_ids.extend(block.media_ids)
        media_types.extend(block.media_types)
        media_paths.extend(block.media_paths)
        if block.message.sender_id:
            sender_ids.append(block.message.sender_id)
        if block.message.sender_name:
            sender_names.append(block.message.sender_name)
        if block.message.message_type:
            message_types.append(block.message.message_type)

    return BuiltChunk(
        chunk_index=index,
        text=text,
        chunk_hash=stable_text_hash(text),
        message_db_ids=[str(block.message.id) for block in blocks],
        telegram_message_ids=[block.message.telegram_message_id for block in blocks],
        sender_ids=_unique_preserve_order(sender_ids),
        sender_names=_unique_preserve_order(sender_names),
        message_types=_unique_preserve_order(message_types),
        media_ids=_unique_preserve_order(media_ids),
        media_types=_unique_preserve_order(media_types),
        media_paths=_unique_preserve_order(media_paths),
        start_timestamp=min(timestamps) if timestamps else None,
        end_timestamp=max(timestamps) if timestamps else None,
        has_media=any(block.has_media for block in blocks),
    )


def build_chunks(
    blocks: list[MessageBlock],
    *,
    target_chars: int = 8000,
    overlap_messages: int = 2,
) -> list[BuiltChunk]:
    """Build chronological chunks from rendered message blocks.

    ``target_chars`` is intentionally character-based for the MVP. It avoids a
    tokenizer dependency while keeping chunk sizes stable enough for early RAG
    tests. ``overlap_messages`` keeps local context across boundaries.
    """
    if not blocks:
        return []
    if target_chars < 1000:
        raise ValueError("target_chars must be at least 1000")
    if overlap_messages < 0:
        raise ValueError("overlap_messages must not be negative")

    chunks: list[BuiltChunk] = []
    current: list[MessageBlock] = []
    current_chars = 0

    for block in blocks:
        block_chars = len(block.text)
        separator_chars = 8 if current else 0
        would_exceed = current and current_chars + separator_chars + block_chars > target_chars

        if would_exceed:
            chunks.append(_build_chunk_from_blocks(len(chunks), current))
            if overlap_messages:
                current = current[-overlap_messages:]
                current_chars = sum(len(item.text) for item in current) + max(0, len(current) - 1) * 8
            else:
                current = []
                current_chars = 0

        separator_chars = 8 if current else 0
        current.append(block)
        current_chars += separator_chars + block_chars

    if current:
        chunks.append(_build_chunk_from_blocks(len(chunks), current))

    return chunks

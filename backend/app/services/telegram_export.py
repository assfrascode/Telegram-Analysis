
import json
import posixpath
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, BinaryIO, Iterator

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"}
AUDIO_EXTENSIONS = {".flac", ".m4a", ".mp3", ".mpga", ".ogg", ".wav", ".webm"}
MEDIA_PATH_FIELDS = ("photo", "file")

MISSING_FILE_MARKERS = (
    "file not included",
    "not included",
    "file was not exported",
    "too big",
    "too large",
)


class TelegramExportError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedMediaReference:
    media_type: str
    original_path: str
    missing_reason: str | None = None


@dataclass(frozen=True)
class ParsedMessage:
    telegram_message_id: int
    timestamp: datetime | None
    edited_timestamp: datetime | None
    sender_id: str | None
    sender_name: str | None
    message_type: str | None
    text: str
    reply_to_message_id: int | None
    forwarded_from: str | None
    reactions: list[dict[str, Any]]
    raw: dict[str, Any]
    media: list[ParsedMediaReference]


def normalize_export_path(path: str) -> str:
    """Normalize a Telegram-export-relative path and reject unsafe paths."""
    if not isinstance(path, str):
        raise TelegramExportError("Path must be a string")

    cleaned = path.replace("\\", "/").strip()
    if not cleaned:
        raise TelegramExportError("Path is empty")
    if cleaned.startswith("/"):
        raise TelegramExportError(f"Absolute paths are not allowed: {path!r}")

    normalized = posixpath.normpath(cleaned)
    if normalized in {".", ""}:
        raise TelegramExportError("Path is empty after normalization")

    pure = PurePosixPath(normalized)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise TelegramExportError(f"Unsafe path component in {path!r}")

    return str(pure)


def is_probable_missing_media_marker(value: str) -> bool:
    lowered = value.lower()
    if value.startswith("(") and value.endswith(")"):
        return any(marker in lowered for marker in MISSING_FILE_MARKERS)
    return False


def parse_datetime(value: Any, unix_value: Any = None) -> datetime | None:
    if unix_value not in (None, ""):
        try:
            return datetime.fromtimestamp(int(unix_value), tz=timezone.utc)
        except (TypeError, ValueError, OSError):
            pass

    if value in (None, ""):
        return None

    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        except (ValueError, OSError):
            return None

    if not isinstance(value, str):
        return None

    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        # Telegram Desktop exports normally use ISO strings, but some old exports
        # used a space between date and time.
        try:
            parsed = datetime.fromisoformat(candidate.replace(" ", "T", 1))
        except ValueError:
            return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_text(value: Any) -> str:
    """Convert Telegram's mixed text/entity representation into plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts)
    return str(value)


def parse_reactions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    reactions: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            reactions.append(dict(item))
    return reactions


def json_safe_export_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: json_safe_export_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe_export_value(item) for item in value]
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    return value


def parse_reply_to_message_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def classify_media(path: str | None, message: dict[str, Any], field_name: str | None = None) -> str | None:
    mime_type = str(message.get("mime_type") or "").lower()
    media_type = str(message.get("media_type") or "").lower()
    message_type = str(message.get("type") or "").lower()
    is_voice = "voice" in media_type or "voice" in message_type

    suffix = PurePosixPath(path or "").suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"

    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "voice" if is_voice else "audio"

    if field_name == "photo" or "photo" in media_type:
        return "image"
    if "video" in media_type or "animation" in media_type or "video" in message_type:
        return "video"
    if is_voice:
        return "voice"
    if "audio" in media_type or "audio" in message_type:
        return "audio"

    if suffix in VIDEO_EXTENSIONS:
        return "video"
    if suffix in AUDIO_EXTENSIONS:
        return "voice" if is_voice else "audio"

    return None


def extract_media_references(message: dict[str, Any]) -> list[ParsedMediaReference]:
    references: list[ParsedMediaReference] = []
    seen: set[tuple[str, str | None]] = set()

    for field_name in MEDIA_PATH_FIELDS:
        value = message.get(field_name)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            continue

        if is_probable_missing_media_marker(value):
            media_type = classify_media(None, message, field_name) or "unknown"
            if media_type in {"image", "video", "audio", "voice", "unknown"}:
                key = (value, "not_included_in_export")
                if key not in seen:
                    references.append(
                        ParsedMediaReference(
                            media_type=media_type,
                            original_path=value,
                            missing_reason="not_included_in_export",
                        )
                    )
                    seen.add(key)
            continue

        try:
            normalized = normalize_export_path(value)
        except TelegramExportError:
            media_type = classify_media(value, message, field_name) or "unknown"
            key = (value, "unsafe_path")
            if key not in seen and media_type in {"image", "video", "audio", "voice", "unknown"}:
                references.append(
                    ParsedMediaReference(
                        media_type=media_type,
                        original_path=value,
                        missing_reason="unsafe_path",
                    )
                )
                seen.add(key)
            continue

        media_type = classify_media(normalized, message, field_name)
        if media_type not in {"image", "video", "audio", "voice"}:
            continue

        key = (normalized, None)
        if key in seen:
            continue
        references.append(ParsedMediaReference(media_type=media_type, original_path=normalized))
        seen.add(key)

    return references


def parse_message(message: dict[str, Any]) -> ParsedMessage | None:
    raw_id = message.get("id")
    if raw_id is None:
        return None
    try:
        telegram_message_id = int(raw_id)
    except (TypeError, ValueError):
        return None

    safe_message = json_safe_export_value(message)

    return ParsedMessage(
        telegram_message_id=telegram_message_id,
        timestamp=parse_datetime(safe_message.get("date"), safe_message.get("date_unixtime")),
        edited_timestamp=parse_datetime(safe_message.get("edited"), safe_message.get("edited_unixtime")),
        sender_id=str(safe_message.get("from_id")) if safe_message.get("from_id") is not None else None,
        sender_name=str(safe_message.get("from")) if safe_message.get("from") is not None else None,
        message_type=str(safe_message.get("type")) if safe_message.get("type") is not None else None,
        text=parse_text(safe_message.get("text")),
        reply_to_message_id=parse_reply_to_message_id(safe_message.get("reply_to_message_id")),
        forwarded_from=(
            str(safe_message.get("forwarded_from")) if safe_message.get("forwarded_from") is not None else None
        ),
        reactions=parse_reactions(safe_message.get("reactions")),
        raw=safe_message,
        media=extract_media_references(safe_message),
    )


def iter_result_messages(file_obj: BinaryIO) -> Iterator[dict[str, Any]]:
    """Yield Telegram messages from result.json.

    Uses ijson when installed. Falls back to json.load for development setups.
    """
    try:
        import ijson  # type: ignore
    except ImportError:
        data = json.load(file_obj)
        messages = data.get("messages", []) if isinstance(data, dict) else []
        for message in messages:
            if isinstance(message, dict):
                yield message
        return

    for message in ijson.items(file_obj, "messages.item"):
        if isinstance(message, dict):
            yield message


def count_result_messages(file_obj: BinaryIO) -> int | None:
    """Best-effort message count for progress. Returns None when streaming count is unavailable."""
    try:
        import ijson  # type: ignore
    except ImportError:
        try:
            data = json.load(file_obj)
            messages = data.get("messages", []) if isinstance(data, dict) else []
            return len(messages) if isinstance(messages, list) else None
        except Exception:
            return None

    count = 0
    try:
        for _ in ijson.items(file_obj, "messages.item"):
            count += 1
        return count
    except Exception:
        return None


def telegram_export_root_from_result_key(extracted_prefix: str, result_object_key: str) -> str:
    """Return the MinIO prefix that paths inside result.json are relative to."""
    if not result_object_key.startswith(extracted_prefix):
        raise TelegramExportError("result.json object is outside extracted prefix")
    result_relative = result_object_key[len(extracted_prefix) :]
    result_dir = posixpath.dirname(result_relative)
    if result_dir:
        return f"{extracted_prefix}{result_dir}/"
    return extracted_prefix


def relative_path_from_root(root_prefix: str, object_key: str) -> str:
    if not object_key.startswith(root_prefix):
        raise TelegramExportError("object key is outside export root")
    return object_key[len(root_prefix) :]

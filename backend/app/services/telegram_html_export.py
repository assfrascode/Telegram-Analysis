import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup
from bs4.element import Tag

from app.services.telegram_export import (
    AUDIO_EXTENSIONS,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    TelegramExportError,
    normalize_export_name,
    normalize_export_path,
    parse_datetime,
)

MESSAGE_ID_RE = re.compile(r"^message(\d+)$")
MESSAGE_LINK_ID_RE = re.compile(r"(?:go_to_message|message)(\d+)")
MESSAGES_PAGE_RE = re.compile(r"^messages(?P<index>\d*)\.html$", re.IGNORECASE)
HTML_DATETIME_RE = re.compile(
    r"^(?P<date>\d{1,2}\.\d{1,2}\.\d{4})\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?)"
    r"(?:\s+(?:UTC)?(?P<tz>Z|[+-]\d{2}:?\d{2}))?$",
    re.IGNORECASE,
)
MEDIA_CLASS_MARKERS = (
    "photo_wrap",
    "media_wrap",
    "file_wrap",
    "video_file",
    "audio_file",
    "voice_message",
)
IGNORED_LINK_SUFFIXES = {".html", ".htm", ".css", ".js"}


class TelegramHtmlExportError(ValueError):
    pass


@dataclass(frozen=True)
class TelegramHtmlPage:
    relative_path: str
    html: str | bytes


@dataclass(frozen=True)
class TelegramHtmlConversionResult:
    data: dict[str, Any]
    messages_total: int
    pages_total: int
    source_name: str | None


def is_messages_html_page_name(name: str) -> bool:
    return MESSAGES_PAGE_RE.fullmatch(name) is not None


def messages_html_page_sort_key(path: str) -> tuple[int, str]:
    name = PurePosixPath(path).name.lower()
    match = MESSAGES_PAGE_RE.fullmatch(name)
    if match is None:
        return (10**9, name)
    raw_index = match.group("index")
    return (1 if raw_index == "" else int(raw_index), name)


def find_html_export_pages(relative_paths: Iterable[str]) -> list[str]:
    paths = sorted(relative_paths)
    root_candidates: set[str] = set()
    for path in paths:
        pure = PurePosixPath(path)
        if pure.name.lower() == "messages.html":
            parent = "" if str(pure.parent) == "." else f"{pure.parent}/"
            root_candidates.add(parent)

    if not root_candidates:
        return []

    root = sorted(root_candidates, key=lambda value: (value.count("/"), len(value), value))[0]
    pages: list[str] = []
    for path in paths:
        if not path.startswith(root):
            continue
        child = path[len(root) :]
        if "/" in child:
            continue
        if is_messages_html_page_name(child):
            pages.append(path)
    return sorted(pages, key=messages_html_page_sort_key)


def convert_html_export_pages(pages: Iterable[TelegramHtmlPage]) -> TelegramHtmlConversionResult:
    messages: list[dict[str, Any]] = []
    page_count = 0
    chat_name: str | None = None

    for page in pages:
        page_count += 1
        soup = BeautifulSoup(page.html, "html.parser")
        chat_name = chat_name or _extract_chat_name(soup)
        messages.extend(_parse_messages_from_page(soup, page.relative_path))

    if page_count == 0:
        raise TelegramHtmlExportError("Telegram HTML export has no message pages")

    chat_name = normalize_export_name(chat_name)

    return TelegramHtmlConversionResult(
        data={
            "name": chat_name or "Telegram HTML export",
            "type": "telegram_html_export",
            "exported_from": "telegram_desktop_html",
            "messages": messages,
        },
        messages_total=len(messages),
        pages_total=page_count,
        source_name=chat_name,
    )


def html_conversion_result_to_json_bytes(result: TelegramHtmlConversionResult) -> bytes:
    return json.dumps(result.data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _parse_messages_from_page(soup: BeautifulSoup, page_relative_path: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for tag in soup.find_all(id=MESSAGE_ID_RE):
        if not isinstance(tag, Tag):
            continue
        raw_id = tag.get("id")
        match = MESSAGE_ID_RE.fullmatch(str(raw_id or ""))
        if match is None:
            continue
        message_id = int(match.group(1))
        if message_id in seen_ids:
            continue
        parsed = _parse_message_tag(tag, message_id, page_relative_path)
        if parsed is not None:
            messages.append(parsed)
            seen_ids.add(message_id)
    return messages


def _parse_message_tag(tag: Tag, message_id: int, page_relative_path: str) -> dict[str, Any] | None:
    message: dict[str, Any] = {
        "id": message_id,
        "type": _message_type(tag),
        "source": "telegram_html_export",
        "source_html": page_relative_path,
    }

    date_value = _extract_date(tag)
    if date_value:
        message["date"] = date_value

    sender = _extract_sender(tag)
    if sender:
        message["from"] = sender

    text = _extract_message_text(tag)
    if text:
        message["text"] = text
    else:
        message["text"] = ""

    reply_to = _extract_reply_to_message_id(tag)
    if reply_to is not None:
        message["reply_to_message_id"] = reply_to

    forwarded_from = _extract_forwarded_from(tag)
    if forwarded_from:
        message["forwarded_from"] = forwarded_from

    media = _extract_media_links(tag, page_relative_path)
    if media.photo:
        message["photo"] = media.photo
    if media.file:
        message["file"] = media.file
    if media.media_type:
        message["media_type"] = media.media_type

    return message


def _extract_chat_name(soup: BeautifulSoup) -> str | None:
    page_header = soup.select_one(".page_header .text") or soup.select_one(".page_header")
    value = _clean_tag_text(page_header) if page_header is not None else ""
    if value:
        return value
    title = soup.find("title")
    return _clean_tag_text(title) if isinstance(title, Tag) else None


def _message_type(tag: Tag) -> str:
    classes = _class_set(tag)
    if "service" in classes:
        return "service"
    return "message"


def _extract_date(tag: Tag) -> str | None:
    date_tag = _first_descendant_with_classes(tag, {"date", "details"})
    if date_tag is None:
        return None

    raw_value = str(date_tag.get("title") or "").strip() or _clean_tag_text(date_tag)
    parsed = _parse_html_datetime(raw_value)
    return parsed.isoformat() if parsed is not None else raw_value or None


def _parse_html_datetime(value: str) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed

    cleaned = re.sub(r"\s+", " ", value).strip()
    if cleaned.endswith(" UTC"):
        cleaned = cleaned[:-4]
        timezone_part = "+00:00"
    else:
        match = HTML_DATETIME_RE.fullmatch(cleaned)
        if match is None:
            return None
        timezone_part = match.group("tz") or "+00:00"
        cleaned = f"{match.group('date')} {match.group('time')}"

    if timezone_part.upper() == "Z":
        timezone_part = "+00:00"
    if len(timezone_part) == 5 and timezone_part[3] != ":":
        timezone_part = f"{timezone_part[:3]}:{timezone_part[3:]}"

    for date_format in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            naive = datetime.strptime(cleaned, date_format)
        except ValueError:
            continue
        return parse_datetime(f"{naive.isoformat()}{timezone_part}") or naive.replace(tzinfo=timezone.utc)
    return None


def _extract_sender(tag: Tag) -> str | None:
    for candidate in tag.find_all(class_="from_name"):
        if isinstance(candidate, Tag) and not _has_ancestor_with_class(candidate, tag, {"forwarded", "reply_to"}):
            value = _clean_tag_text(candidate)
            if value:
                return value
    return None


def _extract_message_text(tag: Tag) -> str:
    text_tag = _first_descendant_with_classes(tag, {"text"})
    if text_tag is not None:
        return _clean_tag_text(text_tag)

    body_tag = _first_descendant_with_classes(tag, {"body", "details"})
    if body_tag is not None:
        cloned = BeautifulSoup(str(body_tag), "html.parser")
        for selector in (".date", ".from_name", ".reply_to", ".forwarded"):
            for node in cloned.select(selector):
                node.decompose()
        return _clean_tag_text(cloned)
    return ""


def _extract_reply_to_message_id(tag: Tag) -> int | None:
    reply_tag = _first_descendant_with_classes(tag, {"reply_to"})
    if reply_tag is None:
        return None
    for anchor in reply_tag.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        for attr in ("href", "onclick"):
            value = str(anchor.get(attr) or "")
            match = MESSAGE_LINK_ID_RE.search(value)
            if match is not None:
                return int(match.group(1))
    return None


def _extract_forwarded_from(tag: Tag) -> str | None:
    forwarded_tag = _first_descendant_with_classes(tag, {"forwarded"})
    if forwarded_tag is None:
        return None

    from_name = forwarded_tag.find(class_="from_name")
    value = _clean_tag_text(from_name) if isinstance(from_name, Tag) else _clean_tag_text(forwarded_tag)
    value = re.sub(r"^Forwarded from\s+", "", value, flags=re.IGNORECASE).strip()
    return value or None


@dataclass(frozen=True)
class _MediaSelection:
    photo: str | None = None
    file: str | None = None
    media_type: str | None = None


def _extract_media_links(tag: Tag, page_relative_path: str) -> _MediaSelection:
    photo: str | None = None
    file: str | None = None
    media_type: str | None = None

    for anchor in tag.find_all("a"):
        if not isinstance(anchor, Tag):
            continue
        field_name = _classify_media_link(anchor)
        if field_name is None:
            continue

        href = str(anchor.get("href") or "")
        media_path = _media_path_from_href(href, page_relative_path)
        if media_path is None:
            continue

        if field_name == "photo" and photo is None:
            photo = media_path
        elif field_name == "file" and file is None:
            file = media_path
            media_type = _media_type_from_link(anchor, media_path)

        if photo and file:
            break

    return _MediaSelection(photo=photo, file=file, media_type=media_type)


def _classify_media_link(anchor: Tag) -> str | None:
    href = str(anchor.get("href") or "").strip()
    if not href:
        return None

    split = urlsplit(href)
    if split.scheme and split.scheme.lower() not in {"", "file"}:
        return None
    if href.startswith("#"):
        return None

    path = unquote(split.path).replace("\\", "/")
    suffix = PurePosixPath(path).suffix.lower()
    classes = _class_set(anchor)
    has_media_class = any(marker in classes for marker in MEDIA_CLASS_MARKERS)
    if suffix in IGNORED_LINK_SUFFIXES and not has_media_class:
        return None

    if "photo_wrap" in classes or suffix in IMAGE_EXTENSIONS or "/photos/" in f"/{path}":
        return "photo"
    if has_media_class:
        return "file"
    if suffix in VIDEO_EXTENSIONS or suffix in AUDIO_EXTENSIONS:
        return "file"
    if f"/{path}".startswith(("/files/", "/video_files/", "/voice_messages/", "/audio_files/")):
        return "file"
    return None


def _media_path_from_href(href: str, page_relative_path: str) -> str | None:
    split = urlsplit(href.strip())
    raw_path = unquote(split.path).replace("\\", "/").strip()
    if not raw_path:
        return None
    if raw_path.startswith("/"):
        raw_path = raw_path.lstrip("/")
    elif "/" in page_relative_path:
        page_dir = str(PurePosixPath(page_relative_path).parent)
        raw_path = f"{page_dir}/{raw_path}"

    try:
        return normalize_export_path(raw_path)
    except TelegramExportError:
        return raw_path


def _media_type_from_link(anchor: Tag, path: str) -> str:
    classes = _class_set(anchor)
    suffix = PurePosixPath(path).suffix.lower()
    if "voice_message" in classes:
        return "voice_message"
    if "audio_file" in classes or suffix in AUDIO_EXTENSIONS:
        return "audio_file"
    if "video_file" in classes or suffix in VIDEO_EXTENSIONS:
        return "video_file"
    return "file"


def _clean_tag_text(tag: Tag | BeautifulSoup | None) -> str:
    if tag is None:
        return ""
    cloned = BeautifulSoup(str(tag), "html.parser")
    for br in cloned.find_all("br"):
        br.replace_with("\n")
    text = cloned.get_text("", strip=False).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


def _class_set(tag: Tag) -> set[str]:
    classes = tag.get("class") or []
    return {str(item) for item in classes}


def _first_descendant_with_classes(tag: Tag, required: set[str]) -> Tag | None:
    for candidate in tag.find_all(class_=lambda value: value is not None):
        if isinstance(candidate, Tag) and required.issubset(_class_set(candidate)):
            return candidate
    return None


def _has_ancestor_with_class(tag: Tag, stop_at: Tag, classes: set[str]) -> bool:
    parent = tag.parent
    while isinstance(parent, Tag) and parent is not stop_at:
        if _class_set(parent).intersection(classes):
            return True
        parent = parent.parent
    return False

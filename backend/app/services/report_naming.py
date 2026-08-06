import re
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobSourceType, TelegramChat, Upload


_SEPARATOR_RE = re.compile(r"-+")
_ASCII_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SOURCE_COMPONENT_MAX_BYTES = 120


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    encoded = encoded[:max_bytes]
    while encoded:
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError:
            encoded = encoded[:-1]
    return ""


def sanitize_report_source_name(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    characters = [character if character.isalnum() else "-" for character in normalized]
    slug = _SEPARATOR_RE.sub("-", "".join(characters)).strip("-")
    slug = _truncate_utf8(slug, _SOURCE_COMPONENT_MAX_BYTES).rstrip("-")
    return slug or "telegram-chat"


def build_report_filename(source_name: str | None, report_date: date) -> str:
    source_component = sanitize_report_source_name(source_name)
    return f"chat-analysis-{source_component}-{report_date.isoformat()}.zip"


def build_download_all_filename(upload_filename: str | None) -> str:
    safe_name = Path(str(upload_filename or "").replace("\\", "/")).name.strip()
    stem = (
        safe_name[:-4].strip()
        if safe_name.lower().endswith(".zip")
        else Path(safe_name).stem.strip()
    )
    return f"{stem or 'telegram-export'}-with-report.zip"


def report_date_for_job(job: Job, fallback: datetime) -> date:
    value = fallback
    if job.source_type == JobSourceType.telegram_chat and job.report_end_at is not None:
        value = job.report_end_at
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


async def resolve_report_source_name(session: AsyncSession, job: Job) -> str:
    source_name = (job.source_name or "").strip()
    if source_name:
        return source_name

    if job.telegram_chat_id is not None:
        title = (
            await session.execute(
                select(TelegramChat.title).where(TelegramChat.id == job.telegram_chat_id)
            )
        ).scalar_one_or_none()
        if title and title.strip():
            return title.strip()

    if job.upload_id is not None:
        upload_filename = (
            await session.execute(select(Upload.filename).where(Upload.id == job.upload_id))
        ).scalar_one_or_none()
        if upload_filename:
            upload_stem = Path(upload_filename).stem.strip()
            if upload_stem:
                return upload_stem

    return "telegram-chat"


def attachment_content_disposition(filename: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    )
    ascii_name = _ASCII_FILENAME_RE.sub("-", ascii_name).strip("-.")
    if not ascii_name.lower().endswith(".zip"):
        ascii_name = "chat-analysis-report.zip"
    encoded_name = quote(filename, safe="")
    return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}'

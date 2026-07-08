import asyncio
import hashlib
import os
import secrets
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    CollectedTelegramMedia,
    CollectedTelegramMessage,
    Job,
    JobSourceType,
    JobStatus,
    StepStatus,
    TelegramChat,
    TelegramChatStatus,
    TelegramIngestMode,
    TelegramIngestToken,
    TelegramSyncRun,
    TelegramSyncStatus,
)
from app.schemas import (
    TelegramIngestChatUpsertRequest,
    TelegramIngestMessageInput,
    TelegramIngestRunCompleteRequest,
)
from app.services.minio_store import put_stream
from app.services.telegram_sync import periodic_sync_start

settings = get_settings()
TOKEN_PREFIX = "tg_ingest_"


@dataclass(slots=True)
class IngestPrincipal:
    token_id: uuid.UUID
    owner_user_id: uuid.UUID


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hash_ingest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_ingest_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def lease_owner_for_run(run_id: uuid.UUID) -> str:
    return f"external:{run_id}"


def chat_covers_interval(chat: TelegramChat, start_at: datetime, end_at: datetime) -> bool:
    return bool(
        chat.coverage_start
        and chat.coverage_end
        and ensure_utc(chat.coverage_start) <= ensure_utc(start_at)
        and ensure_utc(chat.coverage_end) >= ensure_utc(end_at)
    )


def job_allows_partial_telegram_sync(job: Job) -> bool:
    return bool((getattr(job, "options", None) or {}).get("allow_partial_telegram_sync"))


def job_still_needs_report_coverage(job: Job, chat: TelegramChat) -> bool:
    if not job.report_start_at or not job.report_end_at:
        return False
    if chat_covers_interval(chat, job.report_start_at, job.report_end_at):
        return False

    status = getattr(job, "status", JobStatus.queued)
    if status in {JobStatus.queued, JobStatus.running}:
        return True
    return status == JobStatus.completed and job_allows_partial_telegram_sync(job)


def safe_filename(value: str) -> str:
    name = Path(value).name.replace("\x00", "").strip()
    return name[:900] or "attachment"


def parse_access_hash(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="access_hash must be a decimal string",
        ) from exc


async def create_ingest_token(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    name: str,
) -> tuple[TelegramIngestToken, str]:
    raw_token = new_ingest_token()
    token = TelegramIngestToken(
        owner_user_id=owner_user_id,
        name=name.strip(),
        token_hash=hash_ingest_token(raw_token),
    )
    session.add(token)
    await session.flush()
    return token, raw_token


async def revoke_ingest_token(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    token_id: uuid.UUID,
) -> None:
    token = (
        await session.execute(
            select(TelegramIngestToken).where(
                TelegramIngestToken.id == token_id,
                TelegramIngestToken.owner_user_id == owner_user_id,
                TelegramIngestToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    token.revoked_at = utc_now()
    await session.flush()


async def authenticate_ingest_token(session: AsyncSession, raw_token: str) -> IngestPrincipal | None:
    token = (
        await session.execute(
            select(TelegramIngestToken).where(
                TelegramIngestToken.token_hash == hash_ingest_token(raw_token),
                TelegramIngestToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if token is None:
        return None
    token.last_used_at = utc_now()
    await session.flush()
    return IngestPrincipal(token_id=token.id, owner_user_id=token.owner_user_id)


async def upsert_external_chat(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
    payload: TelegramIngestChatUpsertRequest,
) -> TelegramChat:
    now = utc_now()
    chat = (
        await session.execute(
            select(TelegramChat).where(
                TelegramChat.owner_user_id == principal.owner_user_id,
                TelegramChat.telegram_chat_id == payload.telegram_chat_id,
            )
        )
    ).scalar_one_or_none()
    was_external = chat is not None and chat.ingest_mode == TelegramIngestMode.external_push
    values = {
        "connection_id": None,
        "ingest_mode": TelegramIngestMode.external_push,
        "access_hash": parse_access_hash(payload.access_hash),
        "title": payload.title,
        "username": payload.username,
        "chat_type": payload.chat_type,
        "initial_sync_from": ensure_utc(payload.initial_sync_from),
        "sync_interval_minutes": payload.sync_interval_minutes,
        "status": TelegramChatStatus.active,
        "last_error": None,
        "lease_owner": None,
        "lease_expires_at": None,
        "updated_at": now,
    }
    if chat is None:
        chat = TelegramChat(
            owner_user_id=principal.owner_user_id,
            telegram_chat_id=payload.telegram_chat_id,
            next_sync_at=now,
            **values,
        )
        session.add(chat)
    else:
        for key, value in values.items():
            setattr(chat, key, value)
        if chat.next_sync_at is None or not was_external:
            chat.next_sync_at = now
    await session.flush()
    return chat


async def claim_next_external_chat(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
) -> tuple[TelegramSyncRun, TelegramChat] | None:
    now = utc_now()
    chat = (
        await session.execute(
            select(TelegramChat)
            .where(
                TelegramChat.owner_user_id == principal.owner_user_id,
                TelegramChat.ingest_mode == TelegramIngestMode.external_push,
                TelegramChat.status.in_(
                    [
                        TelegramChatStatus.active,
                        TelegramChatStatus.error,
                        TelegramChatStatus.syncing,
                    ]
                ),
                TelegramChat.next_sync_at <= now,
                or_(
                    TelegramChat.lease_expires_at.is_(None),
                    TelegramChat.lease_expires_at < now,
                ),
            )
            .order_by(TelegramChat.next_sync_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if chat is None:
        return None

    report_job = await report_job_needing_coverage(session, chat)
    if report_job is not None:
        requested_start = ensure_utc(report_job.report_start_at)
        requested_end = ensure_utc(report_job.report_end_at)
        job_id = report_job.id
    else:
        requested_start = periodic_sync_start(chat)
        requested_end = now
        job_id = None

    run = TelegramSyncRun(
        chat_id=chat.id,
        owner_user_id=chat.owner_user_id,
        job_id=job_id,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    session.add(run)
    await session.flush()

    chat.status = TelegramChatStatus.syncing
    chat.last_error = None
    chat.lease_owner = lease_owner_for_run(run.id)
    chat.lease_expires_at = now + timedelta(minutes=settings.telegram_sync_lease_minutes)
    chat.updated_at = now
    await session.flush()
    return run, chat


async def external_report_job_needing_coverage(
    session: AsyncSession,
    chat: TelegramChat,
) -> Job | None:
    return await report_job_needing_coverage(session, chat)


async def report_job_needing_coverage(
    session: AsyncSession,
    chat: TelegramChat,
) -> Job | None:
    jobs = list(
        (
            await session.execute(
                select(Job)
                .where(
                    Job.owner_user_id == chat.owner_user_id,
                    Job.telegram_chat_id == chat.id,
                    Job.source_type == JobSourceType.telegram_chat,
                    Job.status.in_([JobStatus.queued, JobStatus.running, JobStatus.completed]),
                    Job.report_start_at.is_not(None),
                    Job.report_end_at.is_not(None),
                )
                .order_by(Job.created_at)
            )
        )
        .scalars()
        .all()
    )
    for job in jobs:
        if job_still_needs_report_coverage(job, chat):
            return job
    return None


async def load_running_external_run(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
    run_id: uuid.UUID,
    require_active_lease: bool = True,
) -> tuple[TelegramSyncRun, TelegramChat]:
    run = await session.get(TelegramSyncRun, run_id)
    if run is None or run.owner_user_id != principal.owner_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if run.status != TelegramSyncStatus.running:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync run is not running")

    chat = await session.get(TelegramChat, run.chat_id)
    if chat is None or chat.owner_user_id != principal.owner_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    if chat.ingest_mode != TelegramIngestMode.external_push:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Sync run is not for an externally ingested chat",
        )
    if chat.lease_owner != lease_owner_for_run(run.id):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync run lease is not active")
    if require_active_lease and chat.lease_expires_at and chat.lease_expires_at <= utc_now():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Sync run lease expired")
    return run, chat


async def heartbeat_external_run(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
    run_id: uuid.UUID,
) -> TelegramSyncRun:
    run, chat = await load_running_external_run(session, principal=principal, run_id=run_id)
    chat.lease_expires_at = utc_now() + timedelta(minutes=settings.telegram_sync_lease_minutes)
    chat.updated_at = utc_now()
    await session.flush()
    return run


async def upsert_external_messages(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
    run_id: uuid.UUID,
    messages: list[TelegramIngestMessageInput],
) -> int:
    _run, chat = await load_running_external_run(session, principal=principal, run_id=run_id)
    now = utc_now()
    for item in messages:
        row = (
            await session.execute(
                select(CollectedTelegramMessage).where(
                    CollectedTelegramMessage.chat_id == chat.id,
                    CollectedTelegramMessage.telegram_message_id == item.telegram_message_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            row = CollectedTelegramMessage(
                chat_id=chat.id,
                owner_user_id=chat.owner_user_id,
                telegram_message_id=item.telegram_message_id,
                timestamp=ensure_utc(item.timestamp),
            )
            session.add(row)

        row.timestamp = ensure_utc(item.timestamp)
        row.edited_timestamp = ensure_utc(item.edited_timestamp) if item.edited_timestamp else None
        row.sender_id = item.sender_id
        row.sender_name = item.sender_name
        row.message_type = item.message_type or "message"
        row.reply_to_message_id = item.reply_to_message_id
        row.forwarded_from = item.forwarded_from
        row.reactions = item.reactions
        row.text = item.text or ""
        row.raw = item.raw
        row.collected_at = now
    chat.lease_expires_at = now + timedelta(minutes=settings.telegram_sync_lease_minutes)
    chat.updated_at = now
    await session.flush()
    return len(messages)


async def _copy_upload_to_temp(file: UploadFile) -> tuple[str, int, str]:
    temp = tempfile.NamedTemporaryFile(prefix="telegram-ingest-media-", delete=False)
    temp_path = temp.name
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
            temp.write(chunk)
    finally:
        temp.close()
    return temp_path, size, digest.hexdigest()


async def _put_temp_file(object_key: str, temp_path: str, size: int, content_type: str) -> None:
    with open(temp_path, "rb") as source:
        await asyncio.to_thread(put_stream, object_key, source, size, content_type)


async def upsert_external_media(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
    run_id: uuid.UUID,
    telegram_message_id: int,
    telegram_media_key: str,
    media_type: str,
    filename: str,
    mime_type: str | None,
    declared_size_bytes: int | None,
    declared_sha256: str | None,
    file: UploadFile | None,
    error_message: str | None,
) -> CollectedTelegramMedia:
    _run, chat = await load_running_external_run(session, principal=principal, run_id=run_id)
    message = (
        await session.execute(
            select(CollectedTelegramMessage).where(
                CollectedTelegramMessage.chat_id == chat.id,
                CollectedTelegramMessage.telegram_message_id == telegram_message_id,
            )
        )
    ).scalar_one_or_none()
    if message is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram message must be ingested before its media",
        )

    row = (
        await session.execute(
            select(CollectedTelegramMedia).where(
                CollectedTelegramMedia.message_id == message.id,
                CollectedTelegramMedia.telegram_media_key == telegram_media_key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = CollectedTelegramMedia(
            chat_id=chat.id,
            owner_user_id=chat.owner_user_id,
            message_id=message.id,
            telegram_media_key=telegram_media_key,
            media_type=media_type,
            filename=safe_filename(filename),
            mime_type=mime_type,
        )
        session.add(row)
        await session.flush()

    row.media_type = media_type
    row.filename = safe_filename(filename)
    row.mime_type = mime_type
    row.updated_at = utc_now()

    if file is None:
        if not error_message:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either file or error_message must be supplied",
            )
        row.minio_object_key = None
        row.size_bytes = declared_size_bytes
        row.sha256 = declared_sha256
        row.status = StepStatus.failed_retryable
        row.error_message = error_message[:4000]
        await session.flush()
        return row

    temp_path = None
    try:
        temp_path, actual_size, actual_sha256 = await _copy_upload_to_temp(file)
        if declared_size_bytes is not None and actual_size != declared_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded media size does not match declared size",
            )
        if declared_sha256 is not None and actual_sha256.lower() != declared_sha256.lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded media sha256 does not match declared hash",
            )
        object_key = (
            f"users/{chat.owner_user_id}/telegram/chats/{chat.id}/"
            f"messages/{message.telegram_message_id}/{row.id}-{row.filename}"
        )
        await _put_temp_file(
            object_key,
            temp_path,
            actual_size,
            mime_type or file.content_type or "application/octet-stream",
        )
        row.minio_object_key = object_key
        row.size_bytes = actual_size
        row.sha256 = actual_sha256
        row.status = StepStatus.completed
        row.error_message = None
        await session.flush()
        return row
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


async def complete_external_run(
    session: AsyncSession,
    *,
    principal: IngestPrincipal,
    run_id: uuid.UUID,
    payload: TelegramIngestRunCompleteRequest,
) -> TelegramSyncRun:
    run, chat = await load_running_external_run(
        session,
        principal=principal,
        run_id=run_id,
        require_active_lease=False,
    )
    now = utc_now()
    run.messages_seen = payload.messages_seen
    run.attachments_seen = payload.attachments_seen
    run.attachments_failed = payload.attachments_failed
    run.completed_at = now

    if payload.status == "completed":
        run.status = TelegramSyncStatus.completed
        run.error_message = None
        chat.status = TelegramChatStatus.active
        chat.last_error = None
        chat.last_sync_at = now
        chat.next_sync_at = now + timedelta(minutes=chat.sync_interval_minutes)
        chat.coverage_start = min(
            [value for value in (chat.coverage_start, run.requested_start) if value is not None]
        )
        chat.coverage_end = max(
            [value for value in (chat.coverage_end, run.requested_end) if value is not None]
        )
    else:
        error = payload.error_message or "External Telegram ingestion failed"
        run.status = TelegramSyncStatus.failed
        run.error_message = error[:4000]
        chat.status = TelegramChatStatus.error
        chat.last_error = run.error_message
        retry_after = (
            timedelta(seconds=payload.retry_after_seconds)
            if payload.retry_after_seconds
            else timedelta(minutes=settings.telegram_sync_retry_minutes)
        )
        chat.next_sync_at = now + retry_after

    chat.lease_owner = None
    chat.lease_expires_at = None
    chat.updated_at = now
    await session.flush()
    return run

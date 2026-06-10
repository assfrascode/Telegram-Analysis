import asyncio
import hashlib
import json
import mimetypes
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon.errors import AuthKeyUnregisteredError, FloodWaitError
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
)
from telethon.utils import get_display_name

from app.config import get_settings
from app.models import (
    CollectedTelegramMedia,
    CollectedTelegramMessage,
    StepStatus,
    TelegramChat,
    TelegramChatStatus,
    TelegramConnection,
    TelegramConnectionStatus,
    TelegramSyncRun,
    TelegramSyncStatus,
)
from app.services.minio_store import put_stream
from app.services.telegram_accounts import connected_client

settings = get_settings()


class TelegramSyncError(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


async def _resolve_entity(client, chat: TelegramChat):
    """Resolve the chat from the live dialog list and refresh its access hash.

    StringSession stores authorization state but not Telethon's entity cache.
    Reconstructing InputPeerChannel from browser-submitted data is also unsafe
    because a 64-bit access hash may have been rounded by JavaScript.
    """
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if int(entity.id) != int(chat.telegram_chat_id):
            continue

        if isinstance(entity, Channel):
            if entity.broadcast:
                chat_type = "channel"
            elif entity.megagroup:
                chat_type = "megagroup"
            else:
                continue
        elif isinstance(entity, Chat):
            chat_type = "group"
        else:
            continue

        chat.access_hash = getattr(entity, "access_hash", None)
        chat.title = dialog.name or get_display_name(entity) or str(entity.id)
        chat.username = getattr(entity, "username", None)
        chat.chat_type = chat_type
        chat.updated_at = utc_now()
        return entity

    raise TelegramSyncError(
        "Telegram group or channel is no longer present in the connected account's dialogs"
    )


def _message_type(message) -> str:
    if message.action:
        return "service"
    if message.photo:
        return "photo"
    if message.document:
        return "document"
    return "message"


def _reactions(message) -> list[dict[str, Any]]:
    if not message.reactions:
        return []
    results = getattr(message.reactions, "results", None) or []
    return [json_safe(item.to_dict()) for item in results]


def _forwarded_from(message) -> str | None:
    forward = message.forward
    if forward is None:
        return None
    return (
        getattr(forward, "post_author", None)
        or getattr(forward, "from_name", None)
        or (str(getattr(forward, "from_id", "")) or None)
    )


def _document_filename(message) -> str | None:
    document = message.document
    if document is None:
        return None
    for attribute in document.attributes or []:
        if isinstance(attribute, DocumentAttributeFilename):
            return attribute.file_name
    return None


def _media_metadata(message) -> tuple[str, str, str | None, str] | None:
    if message.photo is not None:
        return "image", f"photo-{message.photo.id}.jpg", "image/jpeg", f"photo:{message.photo.id}"
    document = message.document
    if document is None:
        return None

    mime_type = document.mime_type or "application/octet-stream"
    media_type = "document"
    for attribute in document.attributes or []:
        if isinstance(attribute, DocumentAttributeSticker):
            media_type = "sticker"
            break
        if isinstance(attribute, DocumentAttributeAnimated):
            media_type = "animation"
            break
        if isinstance(attribute, DocumentAttributeAudio):
            media_type = "voice" if attribute.voice else "audio"
            break
    else:
        if mime_type.startswith("image/"):
            media_type = "image"
        elif mime_type.startswith("video/"):
            media_type = "video"

    filename = _document_filename(message)
    if not filename:
        suffix = mimetypes.guess_extension(mime_type) or ""
        filename = f"document-{document.id}{suffix}"
    return media_type, filename, mime_type, f"document:{document.id}"


def _safe_filename(value: str) -> str:
    name = Path(value).name.replace("\x00", "").strip()
    return name[:900] or "attachment"


async def _upsert_message(
    session: AsyncSession,
    *,
    chat: TelegramChat,
    message,
) -> CollectedTelegramMessage:
    row = (
        await session.execute(
            select(CollectedTelegramMessage).where(
                CollectedTelegramMessage.chat_id == chat.id,
                CollectedTelegramMessage.telegram_message_id == message.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = CollectedTelegramMessage(
            chat_id=chat.id,
            owner_user_id=chat.owner_user_id,
            telegram_message_id=message.id,
            timestamp=ensure_utc(message.date),
        )
        session.add(row)

    sender = await message.get_sender()
    row.timestamp = ensure_utc(message.date)
    row.edited_timestamp = ensure_utc(message.edit_date) if message.edit_date else None
    row.sender_id = str(message.sender_id) if message.sender_id is not None else None
    row.sender_name = (
        get_display_name(sender) if sender is not None else getattr(message, "post_author", None)
    )
    row.message_type = _message_type(message)
    row.reply_to_message_id = message.reply_to_msg_id
    row.forwarded_from = _forwarded_from(message)
    row.reactions = _reactions(message)
    row.text = message.message or ""
    row.raw = json_safe(message.to_dict())
    row.collected_at = utc_now()
    await session.flush()
    return row


async def _download_media(
    session: AsyncSession,
    *,
    chat: TelegramChat,
    message_row: CollectedTelegramMessage,
    message,
) -> tuple[bool, bool]:
    metadata = _media_metadata(message)
    if metadata is None:
        return False, False
    media_type, filename, mime_type, media_key = metadata
    row = (
        await session.execute(
            select(CollectedTelegramMedia).where(
                CollectedTelegramMedia.message_id == message_row.id,
                CollectedTelegramMedia.telegram_media_key == media_key,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = CollectedTelegramMedia(
            chat_id=chat.id,
            owner_user_id=chat.owner_user_id,
            message_id=message_row.id,
            telegram_media_key=media_key,
            media_type=media_type,
            filename=_safe_filename(filename),
            mime_type=mime_type,
        )
        session.add(row)
        await session.flush()
    if row.status == StepStatus.completed and row.minio_object_key:
        return True, False

    temp = tempfile.NamedTemporaryFile(prefix="telegram-media-", delete=False)
    temp_path = temp.name
    downloaded_path = temp_path
    temp.close()
    try:
        try:
            async with asyncio.timeout(settings.telegram_media_download_timeout_seconds):
                downloaded = await message.download_media(file=temp_path)
        except TimeoutError as exc:
            raise TelegramSyncError(
                "Attachment download timed out after "
                f"{settings.telegram_media_download_timeout_seconds} seconds"
            ) from exc
        if not downloaded or not os.path.exists(downloaded):
            raise TelegramSyncError("Telegram returned no downloadable attachment")
        downloaded_path = downloaded
        size = os.path.getsize(downloaded)
        digest = hashlib.sha256()
        with open(downloaded, "rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        object_key = (
            f"users/{chat.owner_user_id}/telegram/chats/{chat.id}/"
            f"messages/{message_row.telegram_message_id}/{row.id}-{row.filename}"
        )
        with open(downloaded, "rb") as source:
            await asyncio.to_thread(
                put_stream,
                object_key,
                source,
                size,
                mime_type or "application/octet-stream",
            )
        row.minio_object_key = object_key
        row.size_bytes = size
        row.sha256 = digest.hexdigest()
        row.status = StepStatus.completed
        row.error_message = None
        row.updated_at = utc_now()
        return True, False
    except Exception as exc:
        row.status = StepStatus.failed_retryable
        row.error_message = str(exc)[:4000]
        row.updated_at = utc_now()
        return True, True
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        if downloaded_path != temp_path:
            try:
                os.unlink(downloaded_path)
            except FileNotFoundError:
                pass


async def synchronize_chat(
    session: AsyncSession,
    *,
    chat: TelegramChat,
    requested_start: datetime,
    requested_end: datetime,
    job_id: uuid.UUID | None = None,
) -> TelegramSyncRun:
    requested_start = ensure_utc(requested_start)
    requested_end = ensure_utc(requested_end)
    if requested_start >= requested_end:
        raise TelegramSyncError("Synchronization start must be before end")

    connection = await session.get(TelegramConnection, chat.connection_id)
    if connection is None or connection.status != TelegramConnectionStatus.connected:
        raise TelegramSyncError("Telegram connection is not available")

    run = TelegramSyncRun(
        chat_id=chat.id,
        owner_user_id=chat.owner_user_id,
        job_id=job_id,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    session.add(run)
    chat.status = TelegramChatStatus.syncing
    chat.last_error = None
    await session.commit()

    client = None
    try:
        async with asyncio.timeout(settings.telegram_sync_timeout_seconds):
            client = await connected_client(connection)
            entity = await _resolve_entity(client, chat)
            chat.lease_expires_at = utc_now() + timedelta(
                minutes=settings.telegram_sync_lease_minutes
            )
            await session.commit()
            print(
                f"Telegram sync connected chat_id={chat.id} "
                f"telegram_chat_id={chat.telegram_chat_id}",
                flush=True,
            )
            messages_seen = 0
            attachments_seen = 0
            attachments_failed = 0
            async for message in client.iter_messages(
                entity,
                offset_date=requested_end,
                reverse=False,
            ):
                message_date = ensure_utc(message.date)
                if message_date < requested_start:
                    break
                if message_date >= requested_end:
                    continue
                message_row = await _upsert_message(session, chat=chat, message=message)
                has_attachment, failed = await _download_media(
                    session,
                    chat=chat,
                    message_row=message_row,
                    message=message,
                )
                messages_seen += 1
                attachments_seen += int(has_attachment)
                attachments_failed += int(failed)
                if messages_seen % 25 == 0:
                    run.messages_seen = messages_seen
                    run.attachments_seen = attachments_seen
                    run.attachments_failed = attachments_failed
                    chat.lease_expires_at = utc_now() + timedelta(
                        minutes=settings.telegram_sync_lease_minutes
                    )
                    await session.commit()
                    print(
                        f"Telegram sync progress chat_id={chat.id} messages={messages_seen} "
                        f"attachments={attachments_seen} "
                        f"attachment_failures={attachments_failed}",
                        flush=True,
                    )

        now = utc_now()
        run.status = TelegramSyncStatus.completed
        run.messages_seen = messages_seen
        run.attachments_seen = attachments_seen
        run.attachments_failed = attachments_failed
        run.completed_at = now
        chat.status = TelegramChatStatus.active
        chat.last_sync_at = now
        chat.next_sync_at = now + timedelta(minutes=chat.sync_interval_minutes)
        chat.coverage_start = min(
            [value for value in (chat.coverage_start, requested_start) if value is not None]
        )
        chat.coverage_end = max(
            [value for value in (chat.coverage_end, requested_end) if value is not None]
        )
        chat.lease_owner = None
        chat.lease_expires_at = None
        chat.updated_at = now
        await session.commit()
        return run
    except FloodWaitError as exc:
        run.status = TelegramSyncStatus.failed
        run.error_message = f"Telegram flood wait: retry after {exc.seconds} seconds"
        run.completed_at = utc_now()
        chat.status = TelegramChatStatus.error
        chat.last_error = run.error_message
        chat.next_sync_at = utc_now() + timedelta(seconds=exc.seconds)
        chat.lease_owner = None
        chat.lease_expires_at = None
        await session.commit()
        raise TelegramSyncError(run.error_message) from exc
    except AuthKeyUnregisteredError as exc:
        connection.status = TelegramConnectionStatus.invalid
        connection.last_error = "Telegram session was revoked"
        run.status = TelegramSyncStatus.failed
        run.error_message = connection.last_error
        run.completed_at = utc_now()
        chat.status = TelegramChatStatus.error
        chat.last_error = connection.last_error
        chat.lease_owner = None
        chat.lease_expires_at = None
        await session.commit()
        raise TelegramSyncError(connection.last_error) from exc
    except Exception as exc:
        error_message = (
            "Telegram synchronization timed out after "
            f"{settings.telegram_sync_timeout_seconds} seconds"
            if isinstance(exc, TimeoutError)
            else str(exc) or exc.__class__.__name__
        )
        run.status = TelegramSyncStatus.failed
        run.error_message = error_message[:4000]
        run.completed_at = utc_now()
        chat.status = TelegramChatStatus.error
        chat.last_error = run.error_message
        chat.next_sync_at = utc_now() + timedelta(
            minutes=settings.telegram_sync_retry_minutes
        )
        chat.lease_owner = None
        chat.lease_expires_at = None
        await session.commit()
        raise TelegramSyncError(run.error_message) from exc
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), timeout=10)
            except Exception as exc:
                print(
                    f"Telegram client disconnect failed chat_id={chat.id}: {exc}",
                    flush=True,
                )


def periodic_sync_start(chat: TelegramChat) -> datetime:
    if chat.coverage_end is None:
        return ensure_utc(chat.initial_sync_from)
    return max(
        ensure_utc(chat.initial_sync_from),
        ensure_utc(chat.coverage_end) - timedelta(hours=settings.telegram_sync_overlap_hours),
    )

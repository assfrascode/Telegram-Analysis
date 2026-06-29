import asyncio
import hashlib
import json
import mimetypes
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
)
from telethon.utils import get_display_name


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


BACKEND_URL = env("BACKEND_URL", "http://localhost:8000").rstrip("/")
INGEST_TOKEN = env("TELEGRAM_INGEST_TOKEN")
API_ID = int(env("TELEGRAM_API_ID", "0"))
API_HASH = env("TELEGRAM_API_HASH")
PHONE = env("TELEGRAM_PHONE")
SESSION_PATH = env("TELEGRAM_SESSION_PATH", "telegram-external.session")
POLL_SECONDS = int(env("POLL_SECONDS", "15"))
BATCH_SIZE = int(env("MESSAGE_BATCH_SIZE", "100"))
REGISTER_CHAT_IDS = {
    int(value.strip())
    for value in env("TELEGRAM_CHAT_IDS").split(",")
    if value.strip()
}
INITIAL_SYNC_FROM = env("INITIAL_SYNC_FROM", "2026-01-01T00:00:00+00:00")
SYNC_INTERVAL_MINUTES = int(env("SYNC_INTERVAL_MINUTES", "60"))


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def message_type(message) -> str:
    if message.action:
        return "service"
    if message.photo:
        return "photo"
    if message.document:
        return "document"
    return "message"


def reactions(message) -> list[dict[str, Any]]:
    if not message.reactions:
        return []
    results = getattr(message.reactions, "results", None) or []
    return [json_safe(item.to_dict()) for item in results]


def forwarded_from(message) -> str | None:
    forward = message.forward
    if forward is None:
        return None
    return (
        getattr(forward, "post_author", None)
        or getattr(forward, "from_name", None)
        or (str(getattr(forward, "from_id", "")) or None)
    )


def document_filename(message) -> str | None:
    document = message.document
    if document is None:
        return None
    for attribute in document.attributes or []:
        if isinstance(attribute, DocumentAttributeFilename):
            return attribute.file_name
    return None


def media_metadata(message) -> tuple[str, str, str | None, str] | None:
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

    filename = document_filename(message)
    if not filename:
        suffix = mimetypes.guess_extension(mime_type) or ""
        filename = f"document-{document.id}{suffix}"
    return media_type, filename, mime_type, f"document:{document.id}"


def normalize_message(message, sender) -> dict[str, Any]:
    return {
        "telegram_message_id": message.id,
        "timestamp": ensure_utc(message.date).isoformat(),
        "edited_timestamp": ensure_utc(message.edit_date).isoformat()
        if message.edit_date
        else None,
        "sender_id": str(message.sender_id) if message.sender_id is not None else None,
        "sender_name": get_display_name(sender)
        if sender is not None
        else getattr(message, "post_author", None),
        "message_type": message_type(message),
        "reply_to_message_id": message.reply_to_msg_id,
        "forwarded_from": forwarded_from(message),
        "reactions": reactions(message),
        "text": message.message or "",
        "raw": json_safe(message.to_dict()),
    }


def chat_type(entity) -> str | None:
    if isinstance(entity, Channel):
        if entity.broadcast:
            return "channel"
        if entity.megagroup:
            return "megagroup"
    if isinstance(entity, Chat):
        return "group"
    return None


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class Backend:
    def __init__(self) -> None:
        if not INGEST_TOKEN:
            raise RuntimeError("TELEGRAM_INGEST_TOKEN is required")
        self.client = httpx.AsyncClient(
            base_url=BACKEND_URL,
            headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
            timeout=httpx.Timeout(60.0, read=300.0),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def upsert_chat(self, dialog) -> None:
        entity = dialog.entity
        kind = chat_type(entity)
        if kind is None:
            return
        if REGISTER_CHAT_IDS and int(entity.id) not in REGISTER_CHAT_IDS:
            return
        payload = {
            "telegram_chat_id": int(entity.id),
            "access_hash": str(getattr(entity, "access_hash", ""))
            if getattr(entity, "access_hash", None) is not None
            else None,
            "title": dialog.name or get_display_name(entity) or str(entity.id),
            "username": getattr(entity, "username", None),
            "chat_type": kind,
            "initial_sync_from": INITIAL_SYNC_FROM,
            "sync_interval_minutes": SYNC_INTERVAL_MINUTES,
        }
        response = await self.client.post("/telegram/ingest/chats", json=payload)
        response.raise_for_status()
        print(f"Registered external Telegram chat {payload['title']} ({payload['telegram_chat_id']})")

    async def claim_next(self) -> dict[str, Any] | None:
        response = await self.client.post("/telegram/ingest/claims/next")
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    async def heartbeat(self, run_id: str) -> None:
        response = await self.client.post(f"/telegram/ingest/runs/{run_id}/heartbeat")
        response.raise_for_status()

    async def post_messages(self, run_id: str, messages: list[dict[str, Any]]) -> None:
        if not messages:
            return
        response = await self.client.post(
            f"/telegram/ingest/runs/{run_id}/messages",
            json={"messages": messages},
        )
        response.raise_for_status()

    async def post_media_error(
        self,
        run_id: str,
        message_id: int,
        metadata: tuple[str, str, str | None, str],
        error: str,
    ) -> None:
        media_type, filename, mime_type, media_key = metadata
        response = await self.client.post(
            f"/telegram/ingest/runs/{run_id}/media",
            data={
                "telegram_message_id": str(message_id),
                "telegram_media_key": media_key,
                "media_type": media_type,
                "filename": filename,
                "mime_type": mime_type or "",
                "error_message": error[:4000],
            },
        )
        response.raise_for_status()

    async def post_media_file(
        self,
        run_id: str,
        message_id: int,
        metadata: tuple[str, str, str | None, str],
        path: str,
    ) -> None:
        media_type, filename, mime_type, media_key = metadata
        size = os.path.getsize(path)
        digest = sha256_file(path)
        with open(path, "rb") as source:
            response = await self.client.post(
                f"/telegram/ingest/runs/{run_id}/media",
                data={
                    "telegram_message_id": str(message_id),
                    "telegram_media_key": media_key,
                    "media_type": media_type,
                    "filename": Path(filename).name or "attachment",
                    "mime_type": mime_type or "application/octet-stream",
                    "size_bytes": str(size),
                    "sha256": digest,
                },
                files={
                    "file": (
                        Path(filename).name or "attachment",
                        source,
                        mime_type or "application/octet-stream",
                    )
                },
            )
        response.raise_for_status()

    async def complete(
        self,
        run_id: str,
        *,
        status: str,
        messages_seen: int = 0,
        attachments_seen: int = 0,
        attachments_failed: int = 0,
        error_message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        payload = {
            "status": status,
            "messages_seen": messages_seen,
            "attachments_seen": attachments_seen,
            "attachments_failed": attachments_failed,
            "error_message": error_message,
            "retry_after_seconds": retry_after_seconds,
        }
        response = await self.client.post(
            f"/telegram/ingest/runs/{run_id}/complete",
            json=payload,
        )
        response.raise_for_status()


async def resolve_entity(client: TelegramClient, telegram_chat_id: int):
    async for dialog in client.iter_dialogs():
        if int(dialog.entity.id) == int(telegram_chat_id):
            return dialog.entity
    raise RuntimeError(f"Telegram chat {telegram_chat_id} is not available in this session")


async def heartbeat_loop(backend: Backend, run_id: str, stopped: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stopped.wait(), timeout=60)
            return
        except TimeoutError:
            await backend.heartbeat(run_id)


async def process_claim(backend: Backend, client: TelegramClient, claim: dict[str, Any]) -> None:
    run_id = claim["run_id"]
    chat = claim["chat"]
    requested_start = datetime.fromisoformat(claim["requested_start"])
    requested_end = datetime.fromisoformat(claim["requested_end"])
    entity = await resolve_entity(client, chat["telegram_chat_id"])

    stopped = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_loop(backend, run_id, stopped))
    messages: list[dict[str, Any]] = []
    messages_seen = 0
    attachments_seen = 0
    attachments_failed = 0

    async def flush_messages() -> None:
        nonlocal messages
        if messages:
            await backend.post_messages(run_id, messages)
            messages = []

    try:
        async for message in client.iter_messages(entity, offset_date=requested_end, reverse=False):
            message_date = ensure_utc(message.date)
            if message_date < requested_start:
                break
            if message_date >= requested_end:
                continue

            sender = await message.get_sender()
            messages.append(normalize_message(message, sender))
            messages_seen += 1
            metadata = media_metadata(message)
            if len(messages) >= BATCH_SIZE or metadata is not None:
                await flush_messages()

            if metadata is not None:
                attachments_seen += 1
                temp = tempfile.NamedTemporaryFile(prefix="telegram-external-media-", delete=False)
                temp_path = temp.name
                temp.close()
                downloaded_path = temp_path
                try:
                    downloaded = await message.download_media(file=temp_path)
                    if not downloaded:
                        raise RuntimeError("Telegram returned no downloadable attachment")
                    downloaded_path = downloaded
                    await backend.post_media_file(run_id, message.id, metadata, downloaded_path)
                except Exception as exc:
                    attachments_failed += 1
                    await backend.post_media_error(run_id, message.id, metadata, str(exc))
                finally:
                    for path in {temp_path, downloaded_path}:
                        try:
                            os.unlink(path)
                        except FileNotFoundError:
                            pass

        await flush_messages()
        await backend.complete(
            run_id,
            status="completed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
        )
        print(
            f"Completed external sync run={run_id} chat={chat['title']!r} "
            f"messages={messages_seen} attachments={attachments_seen} failures={attachments_failed}",
            flush=True,
        )
    except FloodWaitError as exc:
        await backend.complete(
            run_id,
            status="failed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
            error_message=f"Telegram flood wait: retry after {exc.seconds} seconds",
            retry_after_seconds=exc.seconds,
        )
    except Exception as exc:
        await backend.complete(
            run_id,
            status="failed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
            error_message=str(exc) or exc.__class__.__name__,
        )
        raise
    finally:
        stopped.set()
        await heartbeat_task


async def main() -> None:
    if not API_ID or not API_HASH:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")

    backend = Backend()
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start(phone=PHONE or None)
    try:
        if REGISTER_CHAT_IDS:
            async for dialog in client.iter_dialogs():
                await backend.upsert_chat(dialog)

        while True:
            claim = await backend.claim_next()
            if claim is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            await process_claim(backend, client, claim)
    finally:
        await backend.close()
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())

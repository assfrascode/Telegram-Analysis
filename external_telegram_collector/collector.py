import asyncio
import hashlib
import json
import mimetypes
import os
import tempfile
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status as http_status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    TakeoutInitDelayError,
)
from telethon.tl.types import (
    Channel,
    Chat,
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
)
from telethon.utils import get_display_name, get_peer_id


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


CONFIG_ERRORS: list[str] = []


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    value = env(name)
    if not value:
        return default
    try:
        result = int(value)
    except ValueError:
        CONFIG_ERRORS.append(f"{name} must be an integer")
        return default
    if minimum is not None and result < minimum:
        CONFIG_ERRORS.append(f"{name} must be at least {minimum}")
        return default
    if maximum is not None and result > maximum:
        CONFIG_ERRORS.append(f"{name} must be at most {maximum}")
        return default
    return result


def env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    value = env(name)
    if not value:
        return default
    try:
        result = float(value)
    except ValueError:
        CONFIG_ERRORS.append(f"{name} must be a number")
        return default
    if minimum is not None and result < minimum:
        CONFIG_ERRORS.append(f"{name} must be at least {minimum}")
        return default
    return result


def env_chat_ids() -> set[int]:
    result: set[int] = set()
    for value in env("TELEGRAM_CHAT_IDS").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            result.add(int(value))
        except ValueError:
            CONFIG_ERRORS.append(
                "TELEGRAM_CHAT_IDS must contain comma-separated integers"
            )
            return set()
    return result


BACKEND_URL = env("BACKEND_URL", "http://localhost:8000").rstrip("/")
INGEST_TOKEN = env("TELEGRAM_INGEST_TOKEN")
API_ID = env_int("TELEGRAM_API_ID", 0, minimum=1)
API_HASH = env("TELEGRAM_API_HASH")
PHONE = env("TELEGRAM_PHONE")
SESSION_PATH = (
    env("TELEGRAM_SESSION_PATH", "telegram-external.session")
    or "telegram-external.session"
)
USE_TAKEOUT = env_bool("TELEGRAM_USE_TAKEOUT")
TAKEOUT_WAIT_TIME = env_float("TELEGRAM_TAKEOUT_WAIT_TIME", 0, minimum=0)
POLL_SECONDS = env_int("POLL_SECONDS", 15, minimum=1)
BATCH_SIZE = env_int("MESSAGE_BATCH_SIZE", 100, minimum=1)
IDLE_LOG_EVERY = env_int("IDLE_LOG_EVERY", 20, minimum=0)
MESSAGE_PROGRESS_EVERY = env_int("MESSAGE_PROGRESS_EVERY", 250, minimum=0)
REGISTER_CHAT_IDS = env_chat_ids()
WEB_ENABLED = env_bool("COLLECTOR_WEB_ENABLED", True)
WEB_HOST = env("COLLECTOR_WEB_HOST", "127.0.0.1") or "127.0.0.1"
WEB_PORT = env_int("COLLECTOR_WEB_PORT", 8787, minimum=1, maximum=65535)
WEB_ASSET_DIR = Path(__file__).resolve().parent / "web"
EVENT_LIMIT = 200
RETRY_INITIAL_SECONDS = 5
RETRY_MAX_SECONDS = 60


def default_initial_sync_from() -> str:
    return (
        (datetime.now(timezone.utc) - timedelta(days=30))
        .replace(microsecond=0)
        .isoformat()
    )


INITIAL_SYNC_FROM = env("INITIAL_SYNC_FROM") or default_initial_sync_from()
SYNC_INTERVAL_MINUTES = env_int("SYNC_INTERVAL_MINUTES", 60, minimum=1)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CollectorStatus:
    def __init__(self, event_limit: int = EVENT_LIMIT) -> None:
        self.phase = "starting"
        self.message = "Collector is starting"
        self.updated_at = utc_iso()
        self.account: dict[str, Any] | None = None
        self.registration: dict[str, Any] | None = None
        self.current_run: dict[str, Any] | None = None
        self.last_run: dict[str, Any] | None = None
        self.retry: dict[str, Any] | None = None
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._event_id = 0

    def set_phase(self, phase: str, message: str) -> None:
        self.phase = phase
        self.message = message
        self.updated_at = utc_iso()
        if phase != "retrying":
            self.retry = None

    def add_event(self, message: str, level: str = "info") -> None:
        self._event_id += 1
        self._events.append(
            {
                "id": self._event_id,
                "timestamp": utc_iso(),
                "level": level,
                "message": message,
            }
        )
        self.updated_at = utc_iso()

    def set_account(self, account: Any) -> None:
        self.account = {
            "id": int(account.id),
            "display_name": get_display_name(account) or str(account.id),
            "username": getattr(account, "username", None),
            "phone": getattr(account, "phone", None),
        }
        self.updated_at = utc_iso()

    def set_registration(self, summary: dict[str, Any]) -> None:
        self.registration = summary
        self.updated_at = utc_iso()

    def start_run(self, claim: dict[str, Any]) -> None:
        chat = claim["chat"]
        self.current_run = {
            "run_id": str(claim["run_id"]),
            "chat_title": chat["title"],
            "telegram_chat_id": chat["telegram_chat_id"],
            "requested_start": claim["requested_start"],
            "requested_end": claim["requested_end"],
            "messages_seen": 0,
            "attachments_seen": 0,
            "attachments_failed": 0,
            "started_at": utc_iso(),
        }
        self.set_phase("syncing", f"Synchronizing {chat['title']}")

    def update_run(
        self,
        *,
        messages_seen: int,
        attachments_seen: int,
        attachments_failed: int,
    ) -> None:
        if self.current_run is None:
            return
        self.current_run.update(
            {
                "messages_seen": messages_seen,
                "attachments_seen": attachments_seen,
                "attachments_failed": attachments_failed,
            }
        )
        self.updated_at = utc_iso()

    def finish_run(
        self,
        result: str,
        *,
        error: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        if self.current_run is None:
            return
        self.last_run = {
            **self.current_run,
            "status": result,
            "error": error,
            "retry_after_seconds": retry_after_seconds,
            "completed_at": utc_iso(),
        }
        self.current_run = None
        self.updated_at = utc_iso()

    def set_retry(self, attempt: int, delay_seconds: int, message: str) -> None:
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        self.set_phase("retrying", message)
        self.retry = {
            "attempt": attempt,
            "delay_seconds": delay_seconds,
            "retry_at": retry_at.isoformat(),
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "message": self.message,
            "updated_at": self.updated_at,
            "account": self.account,
            "registration": self.registration,
            "current_run": self.current_run,
            "last_run": self.last_run,
            "retry": self.retry,
            "events": list(self._events),
        }


STATUS = CollectorStatus()


def log(message: str, level: str = "info") -> None:
    print(message, flush=True)
    STATUS.add_event(message, level)


def publish(phase: str, message: str, level: str = "info") -> None:
    STATUS.set_phase(phase, message)
    log(message, level)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def message_scan_kwargs(
    requested_end: datetime,
    after_message_id: int | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "offset_date": requested_end,
        "reverse": False,
    }
    if after_message_id is not None:
        kwargs["min_id"] = int(after_message_id)
    if USE_TAKEOUT:
        kwargs["wait_time"] = TAKEOUT_WAIT_TIME
    return kwargs


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
        return (
            "image",
            f"photo-{message.photo.id}.jpg",
            "image/jpeg",
            f"photo:{message.photo.id}",
        )

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


def dialog_ids(entity) -> set[int]:
    ids = {int(entity.id)}
    try:
        ids.add(int(get_peer_id(entity)))
    except Exception:
        pass
    return ids


def dialog_id_label(entity) -> str:
    return ", ".join(str(value) for value in sorted(dialog_ids(entity)))


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

    async def upsert_chat(self, dialog) -> bool:
        entity = dialog.entity
        kind = chat_type(entity)
        if kind is None:
            return False
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
        data = response.json()
        backend_chat_id = data.get("chat", {}).get("id", "unknown")
        log(
            "Registered external Telegram chat "
            f"{payload['title']!r} raw_id={payload['telegram_chat_id']} "
            f"ids=[{dialog_id_label(entity)}] backend_chat_id={backend_chat_id}"
        )
        return True

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
    raise RuntimeError(
        f"Telegram chat {telegram_chat_id} is not available in this session"
    )


def dialog_summary(dialog) -> str:
    entity = dialog.entity
    return (
        f"{dialog.name or get_display_name(entity) or entity.id!r} "
        f"type={chat_type(entity)} ids=[{dialog_id_label(entity)}]"
    )


async def register_dialogs(backend: Backend, client: TelegramClient) -> dict[str, Any]:
    if not REGISTER_CHAT_IDS:
        log(
            "TELEGRAM_CHAT_IDS is empty; registering every visible Telegram group/channel "
            "for this account."
        )
    else:
        log(f"TELEGRAM_CHAT_IDS allowlist active: {sorted(REGISTER_CHAT_IDS)}")

    scanned = 0
    supported = 0
    matched = 0
    registered = 0
    matched_requested_ids: set[int] = set()
    available: list[str] = []

    async for dialog in client.iter_dialogs():
        scanned += 1
        entity = dialog.entity
        if chat_type(entity) is None:
            continue
        supported += 1
        ids = dialog_ids(entity)
        if len(available) < 30:
            available.append(dialog_summary(dialog))
        matched_ids = REGISTER_CHAT_IDS.intersection(ids) if REGISTER_CHAT_IDS else ids
        if REGISTER_CHAT_IDS and not matched_ids:
            log(f"Skipping Telegram dialog outside allowlist: {dialog_summary(dialog)}")
            continue
        matched += 1
        matched_requested_ids.update(matched_ids)
        if await backend.upsert_chat(dialog):
            registered += 1

    log(
        "External collector registration summary: "
        f"scanned={scanned} supported_groups_or_channels={supported} "
        f"matched={matched} registered={registered}"
    )
    unmatched = (
        REGISTER_CHAT_IDS - matched_requested_ids if REGISTER_CHAT_IDS else set()
    )
    if unmatched:
        log(
            f"No Telegram dialog matched TELEGRAM_CHAT_IDS allowlist entries: {sorted(unmatched)}"
        )
    if available and (unmatched or registered == 0):
        log("Available group/channel IDs visible to this Telegram session:")
        for item in available:
            log(f"  - {item}")
    summary = {
        "scanned": scanned,
        "supported": supported,
        "matched": matched,
        "registered": registered,
        "unmatched_chat_ids": sorted(unmatched),
    }
    STATUS.set_registration(summary)
    return summary


async def heartbeat_loop(backend: Backend, run_id: str, stopped: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stopped.wait(), timeout=60)
            return
        except TimeoutError:
            await backend.heartbeat(run_id)
            log(f"Heartbeat sent for external sync run={run_id}")


async def process_claim(
    backend: Backend, client: TelegramClient, claim: dict[str, Any]
) -> None:
    run_id = claim["run_id"]
    chat = claim["chat"]
    requested_start = datetime.fromisoformat(claim["requested_start"])
    requested_end = datetime.fromisoformat(claim["requested_end"])
    after_message_id = claim.get("after_message_id")
    STATUS.start_run(claim)
    log(
        f"Received external sync claim run={run_id} chat={chat['title']!r} "
        f"telegram_chat_id={chat['telegram_chat_id']} "
        f"range={requested_start.isoformat()}..{requested_end.isoformat()} "
        f"after_message_id={after_message_id}"
    )
    entity = await resolve_entity(client, chat["telegram_chat_id"])
    log(
        f"Resolved Telegram entity for run={run_id} "
        f"title={get_display_name(entity) or getattr(entity, 'title', chat['title'])!r} "
        f"ids=[{dialog_id_label(entity)}]"
    )

    stopped = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_loop(backend, run_id, stopped))
    messages: list[dict[str, Any]] = []
    messages_seen = 0
    attachments_seen = 0
    attachments_failed = 0

    async def flush_messages() -> None:
        nonlocal messages
        if messages:
            first_id = messages[0]["telegram_message_id"]
            last_id = messages[-1]["telegram_message_id"]
            log(
                f"Posting message batch run={run_id} count={len(messages)} "
                f"message_ids={first_id}..{last_id}"
            )
            await backend.post_messages(run_id, messages)
            log(
                f"Posted message batch run={run_id} count={len(messages)} "
                f"total_messages_seen={messages_seen}"
            )
            messages = []

    async def scan_messages(scan_client: TelegramClient) -> None:
        nonlocal messages_seen, attachments_seen, attachments_failed
        mode = "takeout" if scan_client is not client else "regular"
        iter_kwargs = message_scan_kwargs(requested_end, after_message_id)
        log(f"Starting Telegram message scan run={run_id} mode={mode}")
        async for message in scan_client.iter_messages(entity, **iter_kwargs):
            message_date = ensure_utc(message.date)
            if message_date < requested_start:
                log(
                    f"Stopping scan run={run_id}: message_id={message.id} "
                    f"date={message_date.isoformat()} is before requested_start"
                )
                break
            if message_date >= requested_end:
                continue

            sender = await message.get_sender()
            messages.append(normalize_message(message, sender))
            messages_seen += 1
            STATUS.update_run(
                messages_seen=messages_seen,
                attachments_seen=attachments_seen,
                attachments_failed=attachments_failed,
            )
            if MESSAGE_PROGRESS_EVERY and messages_seen % MESSAGE_PROGRESS_EVERY == 0:
                log(
                    f"Scan progress run={run_id} messages_seen={messages_seen} "
                    f"latest_message_id={message.id} latest_date={message_date.isoformat()} "
                    f"attachments_seen={attachments_seen} attachments_failed={attachments_failed}"
                )
            metadata = media_metadata(message)
            if len(messages) >= BATCH_SIZE or metadata is not None:
                await flush_messages()

            if metadata is not None:
                attachments_seen += 1
                STATUS.update_run(
                    messages_seen=messages_seen,
                    attachments_seen=attachments_seen,
                    attachments_failed=attachments_failed,
                )
                temp = tempfile.NamedTemporaryFile(
                    prefix="telegram-external-media-", delete=False
                )
                temp_path = temp.name
                temp.close()
                downloaded_path = temp_path
                media_type, filename, _mime_type, media_key = metadata
                log(
                    f"Downloading media run={run_id} message_id={message.id} "
                    f"media_key={media_key} type={media_type} filename={filename!r}"
                )
                try:
                    downloaded = await message.download_media(file=temp_path)
                    if not downloaded:
                        raise RuntimeError(
                            "Telegram returned no downloadable attachment"
                        )
                    downloaded_path = downloaded
                    log(
                        f"Uploading media run={run_id} message_id={message.id} "
                        f"path={downloaded_path} size_bytes={os.path.getsize(downloaded_path)}"
                    )
                    await backend.post_media_file(
                        run_id, message.id, metadata, downloaded_path
                    )
                    log(
                        f"Uploaded media run={run_id} message_id={message.id} "
                        f"media_key={media_key}"
                    )
                except Exception as exc:
                    attachments_failed += 1
                    STATUS.update_run(
                        messages_seen=messages_seen,
                        attachments_seen=attachments_seen,
                        attachments_failed=attachments_failed,
                    )
                    log(
                        f"Media failed run={run_id} message_id={message.id} "
                        f"media_key={media_key}: {exc}"
                    )
                    await backend.post_media_error(
                        run_id, message.id, metadata, str(exc)
                    )
                finally:
                    for path in {temp_path, downloaded_path}:
                        try:
                            os.unlink(path)
                        except FileNotFoundError:
                            pass

    try:
        if USE_TAKEOUT:
            log(f"Opening Telegram takeout session run={run_id}")
            async with client.takeout(
                users=True,
                chats=True,
                megagroups=True,
                channels=True,
                files=True,
            ) as takeout:
                await scan_messages(takeout)
        else:
            await scan_messages(client)

        await flush_messages()
        log(
            f"Completing external sync run={run_id} status=completed "
            f"messages_seen={messages_seen} attachments_seen={attachments_seen} "
            f"attachments_failed={attachments_failed}"
        )
        await backend.complete(
            run_id,
            status="completed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
        )
        STATUS.finish_run("completed")
        log(
            f"Completed external sync run={run_id} chat={chat['title']!r} "
            f"messages={messages_seen} attachments={attachments_seen} failures={attachments_failed}"
        )
    except TakeoutInitDelayError as exc:
        log(
            f"External sync takeout init delay run={run_id} retry_after_seconds={exc.seconds}"
        )
        await backend.complete(
            run_id,
            status="failed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
            error_message=f"Telegram takeout init delay: retry after {exc.seconds} seconds",
            retry_after_seconds=exc.seconds,
        )
        STATUS.finish_run(
            "failed",
            error=f"Telegram takeout init delay: retry after {exc.seconds} seconds",
            retry_after_seconds=exc.seconds,
        )
    except FloodWaitError as exc:
        log(f"External sync flood wait run={run_id} retry_after_seconds={exc.seconds}")
        await backend.complete(
            run_id,
            status="failed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
            error_message=f"Telegram flood wait: retry after {exc.seconds} seconds",
            retry_after_seconds=exc.seconds,
        )
        STATUS.finish_run(
            "failed",
            error=f"Telegram flood wait: retry after {exc.seconds} seconds",
            retry_after_seconds=exc.seconds,
        )
    except Exception as exc:
        log(f"External sync failed run={run_id}: {exc or exc.__class__.__name__}")
        await backend.complete(
            run_id,
            status="failed",
            messages_seen=messages_seen,
            attachments_seen=attachments_seen,
            attachments_failed=attachments_failed,
            error_message=str(exc) or exc.__class__.__name__,
        )
        STATUS.finish_run("failed", error=str(exc) or exc.__class__.__name__)
        raise
    finally:
        stopped.set()
        await heartbeat_task


class ConfigurationError(RuntimeError):
    pass


class LoginRejected(ValueError):
    pass


class LoginPhaseError(RuntimeError):
    pass


def configuration_errors() -> list[str]:
    errors = list(CONFIG_ERRORS)
    if not API_ID:
        errors.append("TELEGRAM_API_ID is required")
    if not API_HASH:
        errors.append("TELEGRAM_API_HASH is required")
    if not INGEST_TOKEN:
        errors.append("TELEGRAM_INGEST_TOKEN is required")
    if not BACKEND_URL or not BACKEND_URL.startswith(("http://", "https://")):
        errors.append("BACKEND_URL must be an http:// or https:// URL")
    try:
        datetime.fromisoformat(INITIAL_SYNC_FROM)
    except ValueError:
        errors.append("INITIAL_SYNC_FROM must be an ISO-8601 date or timestamp")
    if SYNC_INTERVAL_MINUTES not in {15, 60, 360, 1440}:
        errors.append("SYNC_INTERVAL_MINUTES must be one of 15, 60, 360, 1440")
    return list(dict.fromkeys(errors))


def startup_description() -> str:
    return (
        "External Telegram collector starting "
        f"backend={BACKEND_URL} session_path={SESSION_PATH} "
        f"poll_seconds={POLL_SECONDS} batch_size={BATCH_SIZE} "
        f"message_progress_every={MESSAGE_PROGRESS_EVERY} "
        f"use_takeout={USE_TAKEOUT} takeout_wait_time={TAKEOUT_WAIT_TIME} "
        f"register_chat_ids={sorted(REGISTER_CHAT_IDS) if REGISTER_CHAT_IDS else 'ALL'}"
    )


class CollectorRuntime:
    def __init__(
        self,
        *,
        status_store: CollectorStatus = STATUS,
        client_factory=TelegramClient,
        backend_factory=Backend,
        sleep=asyncio.sleep,
    ) -> None:
        self.status = status_store
        self.client_factory = client_factory
        self.backend_factory = backend_factory
        self.sleep = sleep
        self.client: TelegramClient | None = None
        self.phone_code_hash: str | None = None
        self.authorized = asyncio.Event()
        self.login_lock = asyncio.Lock()

    def _set_phase(self, phase: str, message: str, level: str = "info") -> None:
        self.status.set_phase(phase, message)
        log(message, level)

    def _require_phase(self, expected: str) -> TelegramClient:
        if self.status.phase != expected or self.client is None:
            raise LoginPhaseError(
                f"Collector is not currently {expected.replace('_', ' ')}"
            )
        return self.client

    def _raise_login_flood_wait(self, phase: str, exc: FloodWaitError) -> None:
        message = f"Telegram asked the collector to wait {exc.seconds} seconds before retrying."
        self._set_phase(phase, message, "warning")
        raise LoginRejected(message)

    async def _send_code(self, client: TelegramClient) -> None:
        if not PHONE:
            raise ConfigurationError(
                "TELEGRAM_PHONE is required when the saved Telegram session is not authorized"
            )
        try:
            sent = await client.send_code_request(PHONE)
        except ApiIdInvalidError as exc:
            raise ConfigurationError(
                "Telegram rejected TELEGRAM_API_ID or TELEGRAM_API_HASH"
            ) from exc
        except PhoneNumberInvalidError as exc:
            raise ConfigurationError("Telegram rejected TELEGRAM_PHONE") from exc
        self.phone_code_hash = sent.phone_code_hash
        self._set_phase(
            "awaiting_code",
            "Telegram sent a verification code. Enter it in the collector web page.",
        )

    async def _complete_authorization(self, client: TelegramClient) -> None:
        me = await client.get_me()
        if me is None:
            raise LoginRejected("Telegram login did not return an account")
        self.status.set_account(me)
        self.phone_code_hash = None
        self.authorized.set()
        self._set_phase(
            "authorized",
            f"Telegram session authorized as {get_display_name(me) or getattr(me, 'id', 'unknown')}",
        )

    async def submit_code(self, code: str) -> None:
        if self.login_lock.locked():
            raise LoginPhaseError(
                "Another Telegram login request is already in progress"
            )
        async with self.login_lock:
            client = self._require_phase("awaiting_code")
            if not self.phone_code_hash:
                raise LoginPhaseError("No active Telegram verification code exists")
            try:
                await client.sign_in(
                    phone=PHONE,
                    code=code.strip(),
                    phone_code_hash=self.phone_code_hash,
                )
            except SessionPasswordNeededError:
                self._set_phase(
                    "awaiting_password",
                    "Telegram two-step verification is enabled. Enter the account password.",
                )
                return
            except PhoneCodeInvalidError as exc:
                self._set_phase(
                    "awaiting_code",
                    "Telegram rejected the verification code. Check it and try again.",
                    "warning",
                )
                raise LoginRejected("Telegram verification code is invalid") from exc
            except PhoneCodeExpiredError as exc:
                self._set_phase(
                    "awaiting_code",
                    "The Telegram verification code expired. Request a new code.",
                    "warning",
                )
                raise LoginRejected("Telegram verification code expired") from exc
            except FloodWaitError as exc:
                self._raise_login_flood_wait("awaiting_code", exc)
            await self._complete_authorization(client)

    async def submit_password(self, password: str) -> None:
        if self.login_lock.locked():
            raise LoginPhaseError(
                "Another Telegram login request is already in progress"
            )
        async with self.login_lock:
            client = self._require_phase("awaiting_password")
            try:
                await client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                self._set_phase(
                    "awaiting_password",
                    "Telegram rejected the two-step verification password. Try again.",
                    "warning",
                )
                raise LoginRejected(
                    "Telegram two-step verification password is invalid"
                ) from exc
            except FloodWaitError as exc:
                self._raise_login_flood_wait("awaiting_password", exc)
            await self._complete_authorization(client)

    async def resend_code(self) -> None:
        if self.login_lock.locked():
            raise LoginPhaseError(
                "Another Telegram login request is already in progress"
            )
        async with self.login_lock:
            client = self._require_phase("awaiting_code")
            try:
                await self._send_code(client)
            except ConfigurationError as exc:
                raise LoginRejected(str(exc)) from exc
            except FloodWaitError as exc:
                self._raise_login_flood_wait("awaiting_code", exc)
            log("Requested a new Telegram verification code")

    async def _poll_claims(self, backend: Backend, client: TelegramClient) -> None:
        idle_polls = 0
        while True:
            claim = await backend.claim_next()
            if claim is None:
                idle_polls += 1
                if idle_polls == 1:
                    self.status.set_phase(
                        "idle", "Collector is connected and waiting for sync work"
                    )
                if idle_polls == 1 or (
                    IDLE_LOG_EVERY and idle_polls % IDLE_LOG_EVERY == 0
                ):
                    log("No external sync claim available; collector is waiting")
                await self.sleep(POLL_SECONDS)
                continue
            idle_polls = 0
            await process_claim(backend, client, claim)
            self.status.set_phase(
                "idle", "Collector is connected and waiting for sync work"
            )

    async def run_once(self) -> None:
        errors = configuration_errors()
        if errors:
            raise ConfigurationError("; ".join(errors))

        self._set_phase("connecting", "Connecting to Telegram")
        log(startup_description())
        backend: Backend | None = None
        client = self.client_factory(SESSION_PATH, API_ID, API_HASH)
        self.client = client
        self.authorized.clear()
        try:
            await client.connect()
            if await client.is_user_authorized():
                await self._complete_authorization(client)
            else:
                await self._send_code(client)
                await self.authorized.wait()

            self._set_phase(
                "registering", "Registering visible Telegram groups and channels"
            )
            backend = self.backend_factory()
            await register_dialogs(backend, client)
            await self._poll_claims(backend, client)
        finally:
            self.phone_code_hash = None
            self.client = None
            if backend is not None:
                await backend.close()
            await client.disconnect()

    async def supervise(self) -> None:
        attempt = 0
        delay = RETRY_INITIAL_SECONDS
        while True:
            try:
                await self.run_once()
                return
            except ConfigurationError as exc:
                self.status.set_phase(
                    "configuration_error",
                    f"{exc}. Fix the environment and restart the collector.",
                )
                log(f"Collector configuration error: {exc}", "error")
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                attempt += 1
                error = str(exc) or exc.__class__.__name__
                if self.status.current_run is not None:
                    self.status.finish_run("failed", error=error)
                message = f"Collector error: {error}. Retrying in {delay} seconds."
                self.status.set_retry(attempt, delay, message)
                log(message, "error")
                await self.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_SECONDS)


class LoginCodeInput(BaseModel):
    code: str = Field(min_length=3, max_length=32)


class LoginPasswordInput(BaseModel):
    password: str = Field(min_length=1, max_length=512)


def require_json(request: Request) -> None:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type != "application/json":
        raise HTTPException(
            status_code=http_status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/json",
        )


def login_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, LoginRejected):
        return HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )
    return HTTPException(status_code=http_status.HTTP_409_CONFLICT, detail=str(exc))


def create_app(
    runtime: CollectorRuntime | None = None,
    *,
    asset_dir: Path = WEB_ASSET_DIR,
) -> FastAPI:
    collector_runtime = runtime or CollectorRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(collector_runtime.supervise())
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(
        title="External Telegram Collector",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.collector_runtime = collector_runtime
    index_html = (asset_dir / "index.html").read_text(encoding="utf-8")
    stylesheet = (asset_dir / "styles.css").read_text(encoding="utf-8")
    javascript = (asset_dir / "app.js").read_text(encoding="utf-8")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return index_html

    @app.get("/assets/styles.css")
    async def styles() -> Response:
        return Response(stylesheet, media_type="text/css")

    @app.get("/assets/app.js")
    async def script() -> Response:
        return Response(javascript, media_type="text/javascript")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "phase": collector_runtime.status.phase}

    @app.get("/api/status")
    async def collector_status() -> dict[str, Any]:
        return collector_runtime.status.snapshot()

    @app.post("/api/login/code", dependencies=[Depends(require_json)])
    async def submit_login_code(payload: LoginCodeInput) -> dict[str, Any]:
        try:
            await collector_runtime.submit_code(payload.code)
        except (LoginRejected, LoginPhaseError) as exc:
            raise login_http_error(exc) from exc
        return collector_runtime.status.snapshot()

    @app.post("/api/login/password", dependencies=[Depends(require_json)])
    async def submit_login_password(payload: LoginPasswordInput) -> dict[str, Any]:
        try:
            await collector_runtime.submit_password(payload.password)
        except (LoginRejected, LoginPhaseError) as exc:
            raise login_http_error(exc) from exc
        return collector_runtime.status.snapshot()

    @app.post("/api/login/resend", dependencies=[Depends(require_json)])
    async def resend_login_code() -> dict[str, Any]:
        try:
            await collector_runtime.resend_code()
        except (LoginRejected, LoginPhaseError) as exc:
            raise login_http_error(exc) from exc
        return collector_runtime.status.snapshot()

    return app


async def main() -> None:
    """Run the legacy terminal-login collector when the web UI is disabled."""
    errors = configuration_errors()
    if errors:
        raise RuntimeError("; ".join(errors))

    log(startup_description())
    backend = Backend()
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    await client.start(phone=PHONE or None)
    try:
        me = await client.get_me()
        STATUS.set_account(me)
        publish(
            "authorized",
            f"Telegram session authorized as {get_display_name(me) or getattr(me, 'id', 'unknown')}",
        )
        publish("registering", "Registering visible Telegram groups and channels")
        await register_dialogs(backend, client)

        idle_polls = 0
        while True:
            claim = await backend.claim_next()
            if claim is None:
                idle_polls += 1
                STATUS.set_phase(
                    "idle", "Collector is connected and waiting for sync work"
                )
                if idle_polls == 1 or (
                    IDLE_LOG_EVERY and idle_polls % IDLE_LOG_EVERY == 0
                ):
                    log("No external sync claim available; collector is waiting")
                await asyncio.sleep(POLL_SECONDS)
                continue
            idle_polls = 0
            await process_claim(backend, client, claim)
    finally:
        await backend.close()
        await client.disconnect()


def run_web_server() -> None:
    log(f"Collector web interface available at http://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run(
        create_app(),
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    if WEB_ENABLED:
        run_web_server()
    else:
        asyncio.run(main())

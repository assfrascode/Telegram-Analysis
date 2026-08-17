import asyncio
import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
import secrets
import stat
import tempfile
import time
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, NamedTuple
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status as http_status
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
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


_TELEGRAM_CLIENT_CLASS = TelegramClient


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
            chat_id = int(value)
        except ValueError:
            CONFIG_ERRORS.append(
                "TELEGRAM_CHAT_IDS must contain comma-separated integers"
            )
            return set()
        if chat_id >= 0:
            CONFIG_ERRORS.append(
                "TELEGRAM_CHAT_IDS must use canonical marked Telegram peer IDs "
                "(negative -... or -100... values)"
            )
            return set()
        result.add(chat_id)
    return result


def env_csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(
        value.strip() for value in env(name, default).split(",") if value.strip()
    )


def default_session_path() -> str:
    state_root = env("XDG_STATE_HOME")
    if state_root:
        root = Path(state_root).expanduser()
    else:
        root = Path.home() / ".local" / "state"
    return str(root / "telegram-external-collector" / "telegram-external.session")


def normalized_session_path(value: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        CONFIG_ERRORS.append("TELEGRAM_SESSION_PATH must be an absolute path")
    if not str(path).endswith(".session"):
        path = Path(f"{path}.session")
    return str(path)


BACKEND_URL = env("BACKEND_URL", "https://localhost:8000").rstrip("/")
ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP = env_bool(
    "TELEGRAM_ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP"
)
INGEST_TOKEN = env("TELEGRAM_INGEST_TOKEN")
API_ID = env_int("TELEGRAM_API_ID", 0, minimum=1)
API_HASH = env("TELEGRAM_API_HASH")
PHONE = env("TELEGRAM_PHONE")
SESSION_PATH = normalized_session_path(
    env("TELEGRAM_SESSION_PATH", default_session_path()) or default_session_path()
)
USE_TAKEOUT = env_bool("TELEGRAM_USE_TAKEOUT")
TAKEOUT_WAIT_TIME = env_float("TELEGRAM_TAKEOUT_WAIT_TIME", 0, minimum=0)
POLL_SECONDS = env_int("POLL_SECONDS", 15, minimum=1)
BATCH_SIZE = env_int("MESSAGE_BATCH_SIZE", 100, minimum=1)
IDLE_LOG_EVERY = env_int("IDLE_LOG_EVERY", 20, minimum=0)
MESSAGE_PROGRESS_EVERY = env_int("MESSAGE_PROGRESS_EVERY", 250, minimum=0)
REGISTER_CHAT_IDS = env_chat_ids()
ALL_CHATS = env_bool("TELEGRAM_ALL_CHATS")
INCLUDE_RAW_METADATA = env_bool("TELEGRAM_INCLUDE_RAW_METADATA")
WEB_ENABLED = env_bool("COLLECTOR_WEB_ENABLED", True)
WEB_HOST = env("COLLECTOR_WEB_HOST", "127.0.0.1") or "127.0.0.1"
WEB_PORT = env_int("COLLECTOR_WEB_PORT", 8787, minimum=1, maximum=65535)
WEB_ALLOW_REMOTE = env_bool("COLLECTOR_WEB_ALLOW_REMOTE")
WEB_TLS_CERT_FILE = env("COLLECTOR_WEB_TLS_CERT_FILE")
WEB_TLS_KEY_FILE = env("COLLECTOR_WEB_TLS_KEY_FILE")
WEB_AUTH_TOKEN = env("COLLECTOR_WEB_AUTH_TOKEN") or secrets.token_urlsafe(32)
WEB_AUTH_TOKEN_GENERATED = not bool(env("COLLECTOR_WEB_AUTH_TOKEN"))
WEB_ALLOWED_HOSTS = env_csv("COLLECTOR_WEB_ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]")
WEB_ALLOWED_ORIGINS = env_csv(
    "COLLECTOR_WEB_ALLOWED_ORIGINS",
    f"{'https' if WEB_TLS_CERT_FILE and WEB_TLS_KEY_FILE else 'http'}://127.0.0.1:{WEB_PORT},"
    f"{'https' if WEB_TLS_CERT_FILE and WEB_TLS_KEY_FILE else 'http'}://localhost:{WEB_PORT}",
)
WEB_MAX_BODY_BYTES = env_int(
    "COLLECTOR_WEB_MAX_BODY_BYTES", 4096, minimum=256, maximum=1024 * 1024
)
WEB_API_REQUESTS_PER_MINUTE = env_int(
    "COLLECTOR_WEB_API_REQUESTS_PER_MINUTE", 120, minimum=1, maximum=10_000
)
WEB_LOGIN_ATTEMPTS_PER_MINUTE = env_int(
    "COLLECTOR_WEB_LOGIN_ATTEMPTS_PER_MINUTE", 6, minimum=1, maximum=1_000
)
WEB_ASSET_DIR = Path(__file__).resolve().parent / "web"
EVENT_LIMIT = 200
RETRY_INITIAL_SECONDS = 5
RETRY_MAX_SECONDS = 60
DEFAULT_INITIAL_SYNC_DAYS = 31
MAX_SYNC_RANGE_DAYS = env_int(
    "TELEGRAM_MAX_SYNC_RANGE_DAYS", 31, minimum=1, maximum=366
)
MAX_MEDIA_FILE_BYTES = env_int(
    "TELEGRAM_MAX_MEDIA_FILE_BYTES",
    256 * 1024 * 1024,
    minimum=1,
    maximum=4 * 1024 * 1024 * 1024,
)
MAX_MEDIA_BYTES_PER_RUN = env_int(
    "TELEGRAM_MAX_MEDIA_BYTES_PER_RUN",
    1024 * 1024 * 1024,
    minimum=1,
    maximum=16 * 1024 * 1024 * 1024,
)
MAX_MEDIA_FILES_PER_RUN = env_int(
    "TELEGRAM_MAX_MEDIA_FILES_PER_RUN", 200, minimum=1, maximum=10_000
)
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = env_float(
    "TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 300, minimum=1
)


def default_initial_sync_from(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return (
        (current - timedelta(days=DEFAULT_INITIAL_SYNC_DAYS))
        .replace(microsecond=0)
        .isoformat()
    )


INITIAL_SYNC_FROM = env("INITIAL_SYNC_FROM") or default_initial_sync_from()
SYNC_INTERVAL_MINUTES = env_int("SYNC_INTERVAL_MINUTES", 60, minimum=1)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConfigurationError(RuntimeError):
    pass


class LoginRejected(ValueError):
    pass


class LoginPhaseError(RuntimeError):
    pass


def is_loopback_hostname(hostname: str | None) -> bool:
    if not hostname:
        return False
    hostname = hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def validate_backend_transport() -> None:
    parsed = urlsplit(BACKEND_URL)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("BACKEND_URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "BACKEND_URL must not contain credentials, a query string, or a fragment"
        )
    if parsed.scheme == "http" and not (
        is_loopback_hostname(parsed.hostname) and ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP
    ):
        raise ConfigurationError(
            "BACKEND_URL must use HTTPS. Plain HTTP is permitted only for a loopback "
            "host when TELEGRAM_ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP=true"
        )


def validate_web_binding() -> None:
    if len(WEB_AUTH_TOKEN) < 32:
        raise ConfigurationError(
            "COLLECTOR_WEB_AUTH_TOKEN must contain at least 32 characters"
        )
    if is_loopback_hostname(WEB_HOST):
        return
    if not WEB_ALLOW_REMOTE:
        raise ConfigurationError(
            "A non-loopback COLLECTOR_WEB_HOST requires COLLECTOR_WEB_ALLOW_REMOTE=true"
        )
    if not env("COLLECTOR_WEB_AUTH_TOKEN"):
        raise ConfigurationError(
            "Remote collector web access requires an explicit COLLECTOR_WEB_AUTH_TOKEN"
        )
    if not env("COLLECTOR_WEB_ALLOWED_HOSTS") or not env(
        "COLLECTOR_WEB_ALLOWED_ORIGINS"
    ):
        raise ConfigurationError(
            "Remote collector web access requires explicit COLLECTOR_WEB_ALLOWED_HOSTS "
            "and COLLECTOR_WEB_ALLOWED_ORIGINS"
        )
    if "*" in WEB_ALLOWED_HOSTS or "*" in WEB_ALLOWED_ORIGINS:
        raise ConfigurationError(
            "Remote collector web host/origin allowlists cannot use *"
        )
    if not WEB_TLS_CERT_FILE or not WEB_TLS_KEY_FILE:
        raise ConfigurationError(
            "Remote collector web access requires COLLECTOR_WEB_TLS_CERT_FILE and "
            "COLLECTOR_WEB_TLS_KEY_FILE"
        )
    for label, value in (
        ("COLLECTOR_WEB_TLS_CERT_FILE", WEB_TLS_CERT_FILE),
        ("COLLECTOR_WEB_TLS_KEY_FILE", WEB_TLS_KEY_FILE),
    ):
        path = Path(value)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ConfigurationError(
                f"{label} must be an absolute, regular, non-symlink file"
            )


def harden_session_files(session_path: str) -> None:
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None
    for candidate in (
        Path(session_path),
        Path(f"{session_path}-journal"),
        Path(f"{session_path}-wal"),
        Path(f"{session_path}-shm"),
    ):
        if not os.path.lexists(candidate):
            continue
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise ConfigurationError(
                f"Refusing symlinked Telegram session state: {candidate}"
            )
        if not stat.S_ISREG(info.st_mode):
            raise ConfigurationError(
                f"Telegram session state is not a regular file: {candidate}"
            )
        if effective_uid is not None and info.st_uid != effective_uid:
            raise ConfigurationError(
                f"Telegram session state must be owned by the collector user: {candidate}"
            )
        candidate.chmod(0o600)


def prepare_session_path(session_path: str) -> None:
    path = Path(session_path)
    if not path.is_absolute():
        raise ConfigurationError("TELEGRAM_SESSION_PATH must be an absolute path")
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ConfigurationError(f"Telegram session directory is unsafe: {parent}")
    effective_uid = os.geteuid() if hasattr(os, "geteuid") else None
    if effective_uid is not None and info.st_uid != effective_uid:
        raise ConfigurationError(
            f"Telegram session directory must be owned by the collector user: {parent}"
        )
    parent.chmod(0o700)
    os.umask(0o077)
    harden_session_files(session_path)


def secure_telegram_client_factory(
    session_path: str, api_id: int, api_hash: str
) -> TelegramClient:
    secure_state = TelegramClient is _TELEGRAM_CLIENT_CLASS
    if secure_state:
        prepare_session_path(session_path)
    client = TelegramClient(session_path, api_id, api_hash)
    if secure_state:
        harden_session_files(session_path)
    return client


class ClaimChatInput(BaseModel):
    id: UUID
    telegram_chat_id: int
    title: str = Field(min_length=1, max_length=512)
    chat_type: Literal["group", "megagroup", "channel"]


class ClaimInput(BaseModel):
    run_id: UUID
    chat: ClaimChatInput
    requested_start: datetime
    requested_end: datetime
    after_message_id: int | None = Field(default=None, ge=0)


def configured_initial_sync_from() -> datetime:
    value = datetime.fromisoformat(INITIAL_SYNC_FROM)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def validate_claim_payload(payload: Any) -> ClaimInput:
    try:
        claim = ClaimInput.model_validate(payload)
    except ValidationError as exc:
        raise RuntimeError("Backend returned a malformed Telegram sync claim") from exc
    if claim.requested_start.tzinfo is None or claim.requested_end.tzinfo is None:
        raise RuntimeError("Backend claim timestamps must include a timezone")
    requested_start = claim.requested_start.astimezone(timezone.utc)
    requested_end = claim.requested_end.astimezone(timezone.utc)
    if requested_end <= requested_start:
        raise RuntimeError("Backend claim end must be after its start")
    if requested_start < configured_initial_sync_from():
        raise RuntimeError(
            "Backend claim starts before the collector's local sync boundary"
        )
    if requested_end - requested_start > timedelta(days=MAX_SYNC_RANGE_DAYS):
        raise RuntimeError("Backend claim exceeds TELEGRAM_MAX_SYNC_RANGE_DAYS")
    if requested_end > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise RuntimeError("Backend claim ends too far in the future")
    return claim


class MediaMetadata(NamedTuple):
    media_type: str
    filename: str
    mime_type: str | None
    media_key: str
    size_bytes: int | None


def media_download_progress_guard(remaining_run_bytes: int):
    def enforce(received: int, _total: int) -> None:
        if received > MAX_MEDIA_FILE_BYTES:
            raise RuntimeError(
                "Attachment exceeded TELEGRAM_MAX_MEDIA_FILE_BYTES during download"
            )
        if received > remaining_run_bytes:
            raise RuntimeError(
                "Attachment exceeded the remaining media byte quota during download"
            )

    return enforce


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

    def redacted_snapshot(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        if snapshot["account"] is not None:
            snapshot["account"] = {
                "display_name": "Connected Telegram account",
                "username": None,
            }
        if snapshot["registration"] is not None:
            snapshot["registration"] = dict(snapshot["registration"])
            snapshot["registration"].pop("unmatched_chat_ids", None)
        for run_name in ("current_run", "last_run"):
            run = snapshot[run_name]
            if run is None:
                continue
            run = dict(run)
            snapshot[run_name] = run
            run.pop("run_id", None)
            run.pop("telegram_chat_id", None)
            run["chat_title"] = "Approved Telegram chat"
            if run.get("error"):
                run["error"] = (
                    "Synchronization failed; inspect the local collector logs"
                )
        public_messages = {
            "starting": "Collector is starting",
            "connecting": "Connecting to Telegram",
            "awaiting_code": "Telegram verification code required",
            "awaiting_password": "Telegram two-step verification password required",
            "authorized": "Telegram session authorized",
            "registering": "Registering approved Telegram chats",
            "idle": "Collector is connected and waiting for sync work",
            "syncing": "Synchronizing an approved Telegram chat",
            "retrying": "Collector encountered an error and will retry",
            "configuration_error": "Collector configuration requires attention",
        }
        snapshot["message"] = public_messages.get(
            snapshot["phase"], "Collector status updated"
        )
        snapshot["events"] = []
        return snapshot


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


def photo_size_bytes(photo: Any) -> int | None:
    sizes: list[int] = []
    for item in getattr(photo, "sizes", None) or []:
        size = getattr(item, "size", None)
        if isinstance(size, int) and size >= 0:
            sizes.append(size)
        progressive = getattr(item, "sizes", None)
        if progressive:
            sizes.extend(
                value for value in progressive if isinstance(value, int) and value >= 0
            )
    return max(sizes) if sizes else None


def media_metadata(message) -> MediaMetadata | None:
    if message.photo is not None:
        return MediaMetadata(
            "image",
            f"photo-{message.photo.id}.jpg",
            "image/jpeg",
            f"photo:{message.photo.id}",
            photo_size_bytes(message.photo),
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
    size_bytes = getattr(document, "size", None)
    if not isinstance(size_bytes, int) or size_bytes < 0:
        size_bytes = None
    return MediaMetadata(
        media_type,
        filename,
        mime_type,
        f"document:{document.id}",
        size_bytes,
    )


def normalize_message(message, sender) -> dict[str, Any]:
    normalized = {
        "telegram_message_id": message.id,
        "timestamp": ensure_utc(message.date).isoformat(),
        "edited_timestamp": (
            ensure_utc(message.edit_date).isoformat() if message.edit_date else None
        ),
        "sender_id": str(message.sender_id) if message.sender_id is not None else None,
        "sender_name": (
            get_display_name(sender)
            if sender is not None
            else getattr(message, "post_author", None)
        ),
        "message_type": message_type(message),
        "reply_to_message_id": message.reply_to_msg_id,
        "forwarded_from": forwarded_from(message),
        "reactions": reactions(message),
        "text": message.message or "",
    }
    if INCLUDE_RAW_METADATA:
        normalized["raw"] = json_safe(message.to_dict())
    return normalized


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


def canonical_dialog_id(entity) -> int:
    return int(get_peer_id(entity))


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
            raise ConfigurationError("TELEGRAM_INGEST_TOKEN is required")
        validate_backend_transport()
        self.client = httpx.AsyncClient(
            base_url=BACKEND_URL,
            headers={"Authorization": f"Bearer {INGEST_TOKEN}"},
            timeout=httpx.Timeout(60.0, read=300.0),
            trust_env=False,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def upsert_chat(self, dialog) -> bool:
        entity = dialog.entity
        kind = chat_type(entity)
        if kind is None:
            return False
        payload = {
            "telegram_chat_id": canonical_dialog_id(entity),
            "access_hash": (
                str(getattr(entity, "access_hash", ""))
                if INCLUDE_RAW_METADATA
                and getattr(entity, "access_hash", None) is not None
                else None
            ),
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
            f"{payload['title']!r} canonical_id={payload['telegram_chat_id']} "
            f"ids=[{dialog_id_label(entity)}] backend_chat_id={backend_chat_id}"
        )
        return True

    async def claim_next(self) -> ClaimInput | None:
        response = await self.client.post("/telegram/ingest/claims/next")
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return validate_claim_payload(response.json())

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
        metadata: MediaMetadata,
        error: str,
    ) -> None:
        media_type, filename, mime_type, media_key, _size_bytes = metadata
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
        metadata: MediaMetadata,
        path: str,
    ) -> None:
        media_type, filename, mime_type, media_key, _size_bytes = metadata
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


def approved_entity_for_claim(
    claim: ClaimInput, approved_entities: dict[int, Any]
) -> Any:
    telegram_chat_id = claim.chat.telegram_chat_id
    entity = approved_entities.get(telegram_chat_id)
    if entity is None:
        raise RuntimeError("Backend claim targets a chat outside the local allowlist")
    kind = chat_type(entity)
    if kind is None or kind != claim.chat.chat_type:
        raise RuntimeError(
            "Backend claim Telegram peer type does not match local registration"
        )
    if canonical_dialog_id(entity) != telegram_chat_id:
        raise RuntimeError(
            "Backend claim Telegram peer ID does not match local registration"
        )
    return entity


def dialog_summary(dialog) -> str:
    entity = dialog.entity
    return (
        f"{dialog.name or get_display_name(entity) or entity.id!r} "
        f"type={chat_type(entity)} ids=[{dialog_id_label(entity)}]"
    )


async def register_dialogs(
    backend: Backend,
    client: TelegramClient,
    approved_entities: dict[int, Any] | None = None,
) -> dict[str, Any]:
    approved = approved_entities if approved_entities is not None else {}
    approved.clear()
    fail_closed = not REGISTER_CHAT_IDS and not ALL_CHATS
    if fail_closed:
        log(
            "TELEGRAM_CHAT_IDS is empty and TELEGRAM_ALL_CHATS is false; "
            "fail-closed mode will only list locally visible canonical chat IDs and "
            "will not register or synchronize any chats."
        )
    elif ALL_CHATS:
        log(
            "TELEGRAM_ALL_CHATS is explicitly enabled; registering every group/channel."
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
        canonical_id = canonical_dialog_id(entity)
        if len(available) < 30:
            available.append(dialog_summary(dialog))
        if fail_closed:
            continue
        matched_ids = (
            REGISTER_CHAT_IDS.intersection({canonical_id})
            if REGISTER_CHAT_IDS
            else {canonical_id}
        )
        if not ALL_CHATS and not matched_ids:
            log(f"Skipping Telegram dialog outside allowlist: {dialog_summary(dialog)}")
            continue
        matched += 1
        matched_requested_ids.update(matched_ids)
        if await backend.upsert_chat(dialog):
            registered += 1
            approved[canonical_id] = entity

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
    if available and (fail_closed or unmatched or registered == 0):
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
    backend: Backend,
    client: TelegramClient,
    claim: ClaimInput | dict[str, Any],
    approved_entities: dict[int, Any] | None = None,
) -> None:
    validated_claim = (
        claim if isinstance(claim, ClaimInput) else validate_claim_payload(claim)
    )
    run_id = str(validated_claim.run_id)
    chat = validated_claim.chat
    requested_start = validated_claim.requested_start.astimezone(timezone.utc)
    requested_end = validated_claim.requested_end.astimezone(timezone.utc)
    after_message_id = validated_claim.after_message_id
    STATUS.start_run(validated_claim.model_dump(mode="json"))
    log(
        f"Received external sync claim run={run_id} chat={chat.title!r} "
        f"telegram_chat_id={chat.telegram_chat_id} "
        f"range={requested_start.isoformat()}..{requested_end.isoformat()} "
        f"after_message_id={after_message_id}"
    )
    entity = approved_entity_for_claim(validated_claim, approved_entities or {})
    log(
        f"Resolved Telegram entity for run={run_id} "
        f"title={get_display_name(entity) or getattr(entity, 'title', chat.title)!r} "
        f"ids=[{dialog_id_label(entity)}]"
    )

    stopped = asyncio.Event()
    heartbeat_task = asyncio.create_task(heartbeat_loop(backend, run_id, stopped))
    messages: list[dict[str, Any]] = []
    messages_seen = 0
    attachments_seen = 0
    attachments_failed = 0
    media_bytes_consumed = 0

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
        nonlocal messages_seen, attachments_seen, attachments_failed, media_bytes_consumed
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
                media_type, filename, _mime_type, media_key, declared_size = metadata
                quota_error: str | None = None
                if attachments_seen > MAX_MEDIA_FILES_PER_RUN:
                    quota_error = "Collector media file-count limit exceeded"
                elif declared_size is not None and declared_size > MAX_MEDIA_FILE_BYTES:
                    quota_error = "Attachment exceeds TELEGRAM_MAX_MEDIA_FILE_BYTES"
                elif (
                    declared_size is not None
                    and media_bytes_consumed + declared_size > MAX_MEDIA_BYTES_PER_RUN
                ):
                    quota_error = (
                        "Collector media byte limit for this sync run exceeded"
                    )
                if quota_error is not None:
                    attachments_failed += 1
                    STATUS.update_run(
                        messages_seen=messages_seen,
                        attachments_seen=attachments_seen,
                        attachments_failed=attachments_failed,
                    )
                    log(
                        f"Skipping media run={run_id} message_id={message.id} "
                        f"media_key={media_key}: {quota_error}",
                        "warning",
                    )
                    await backend.post_media_error(
                        run_id, message.id, metadata, quota_error
                    )
                    continue
                temp = tempfile.NamedTemporaryFile(
                    prefix="telegram-external-media-", delete=False
                )
                temp_path = temp.name
                temp.close()
                downloaded_path = temp_path
                log(
                    f"Downloading media run={run_id} message_id={message.id} "
                    f"media_key={media_key} type={media_type} filename={filename!r}"
                )
                try:
                    remaining_run_bytes = MAX_MEDIA_BYTES_PER_RUN - media_bytes_consumed
                    async with asyncio.timeout(MEDIA_DOWNLOAD_TIMEOUT_SECONDS):
                        downloaded = await message.download_media(
                            file=temp_path,
                            progress_callback=media_download_progress_guard(
                                remaining_run_bytes
                            ),
                        )
                    if not downloaded:
                        raise RuntimeError(
                            "Telegram returned no downloadable attachment"
                        )
                    downloaded_path = downloaded
                    actual_size = os.path.getsize(downloaded_path)
                    if actual_size > MAX_MEDIA_FILE_BYTES:
                        raise RuntimeError(
                            "Downloaded attachment exceeds TELEGRAM_MAX_MEDIA_FILE_BYTES"
                        )
                    if media_bytes_consumed + actual_size > MAX_MEDIA_BYTES_PER_RUN:
                        raise RuntimeError(
                            "Downloaded attachment exceeds the media byte limit for this sync run"
                        )
                    media_bytes_consumed += actual_size
                    log(
                        f"Uploading media run={run_id} message_id={message.id} "
                        f"size_bytes={actual_size}"
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
            f"Completed external sync run={run_id} chat={chat.title!r} "
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


def configuration_errors() -> list[str]:
    errors = list(CONFIG_ERRORS)
    if not API_ID:
        errors.append("TELEGRAM_API_ID is required")
    if not API_HASH:
        errors.append("TELEGRAM_API_HASH is required")
    if not INGEST_TOKEN:
        errors.append("TELEGRAM_INGEST_TOKEN is required")
    parsed_backend = urlsplit(BACKEND_URL)
    if parsed_backend.scheme not in {"http", "https"} or not parsed_backend.hostname:
        errors.append("BACKEND_URL must be an absolute http:// or https:// URL")
    if (
        parsed_backend.username
        or parsed_backend.password
        or parsed_backend.query
        or parsed_backend.fragment
    ):
        errors.append(
            "BACKEND_URL must not contain credentials, a query string, or a fragment"
        )
    try:
        validate_backend_transport()
    except ConfigurationError as exc:
        errors.append(str(exc))
    try:
        datetime.fromisoformat(INITIAL_SYNC_FROM)
    except ValueError:
        errors.append("INITIAL_SYNC_FROM must be an ISO-8601 date or timestamp")
    if SYNC_INTERVAL_MINUTES not in {15, 60, 360, 1440}:
        errors.append("SYNC_INTERVAL_MINUTES must be one of 15, 60, 360, 1440")
    if ALL_CHATS and REGISTER_CHAT_IDS:
        errors.append("Set either TELEGRAM_ALL_CHATS or TELEGRAM_CHAT_IDS, not both")
    if env("COLLECTOR_WEB_AUTH_TOKEN") and len(WEB_AUTH_TOKEN) < 32:
        errors.append("COLLECTOR_WEB_AUTH_TOKEN must contain at least 32 characters")
    if WEB_ENABLED:
        try:
            validate_web_binding()
        except ConfigurationError as exc:
            errors.append(str(exc))
    if MAX_MEDIA_BYTES_PER_RUN < MAX_MEDIA_FILE_BYTES:
        errors.append(
            "TELEGRAM_MAX_MEDIA_BYTES_PER_RUN must be at least "
            "TELEGRAM_MAX_MEDIA_FILE_BYTES"
        )
    return list(dict.fromkeys(errors))


def startup_description() -> str:
    return (
        "External Telegram collector starting "
        f"backend={BACKEND_URL} session_path={SESSION_PATH} "
        f"poll_seconds={POLL_SECONDS} batch_size={BATCH_SIZE} "
        f"message_progress_every={MESSAGE_PROGRESS_EVERY} "
        f"use_takeout={USE_TAKEOUT} takeout_wait_time={TAKEOUT_WAIT_TIME} "
        f"register_chat_ids={sorted(REGISTER_CHAT_IDS) if REGISTER_CHAT_IDS else 'NONE'} "
        f"all_chats={ALL_CHATS} include_raw_metadata={INCLUDE_RAW_METADATA}"
    )


class CollectorRuntime:
    def __init__(
        self,
        *,
        status_store: CollectorStatus = STATUS,
        client_factory=secure_telegram_client_factory,
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
        self.approved_entities: dict[int, Any] = {}

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
            await process_claim(backend, client, claim, self.approved_entities)
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
                "registering", "Registering approved Telegram groups and channels"
            )
            backend = self.backend_factory()
            await register_dialogs(backend, client, self.approved_entities)
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


class RequestBodyLimitMiddleware:
    def __init__(self, app, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") not in {"POST", "PUT", "PATCH"}
            or not scope.get("path", "").startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                response = Response("Invalid Content-Length", status_code=400)
                await response(scope, receive, send)
                return
            if declared_length < 0 or declared_length > self.max_body_bytes:
                response = Response("Request body too large", status_code=413)
                await response(scope, receive, send)
                return

        consumed = 0
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            consumed += len(chunk)
            if consumed > self.max_body_bytes:
                response = Response("Request body too large", status_code=413)
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": b"".join(chunks),
                    "more_body": False,
                }
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                            b"form-action 'self'; object-src 'none'",
                        ),
                        (b"referrer-policy", b"no-referrer"),
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (
                            b"permissions-policy",
                            b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
                        ),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class AsyncRateLimiter:
    def __init__(self, limit: int, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            bucket = self._requests.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, int(bucket[0] + self.window_seconds - now) + 1)
                raise HTTPException(
                    status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            if len(self._requests) > 1024:
                self._requests = {
                    request_key: requests
                    for request_key, requests in self._requests.items()
                    if requests and requests[-1] > cutoff
                }


def request_client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "local"


def basic_credentials(request: Request) -> tuple[str, str] | None:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
        username, password = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    return username, password


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
    auth_token: str = WEB_AUTH_TOKEN,
    allowed_hosts: tuple[str, ...] = WEB_ALLOWED_HOSTS,
    allowed_origins: tuple[str, ...] = WEB_ALLOWED_ORIGINS,
    max_body_bytes: int = WEB_MAX_BODY_BYTES,
    api_requests_per_minute: int = WEB_API_REQUESTS_PER_MINUTE,
    login_attempts_per_minute: int = WEB_LOGIN_ATTEMPTS_PER_MINUTE,
) -> FastAPI:
    collector_runtime = runtime or CollectorRuntime()
    if len(auth_token) < 32:
        raise ConfigurationError(
            "Collector web authentication token must contain at least 32 characters"
        )
    api_limiter = AsyncRateLimiter(api_requests_per_minute)
    login_limiter = AsyncRateLimiter(login_attempts_per_minute)

    async def require_api_rate_limit(request: Request) -> None:
        await api_limiter.check(request_client_key(request))

    async def require_login_rate_limit(request: Request) -> None:
        await login_limiter.check(request_client_key(request))

    async def require_web_auth(request: Request) -> None:
        credentials = basic_credentials(request)
        username = credentials[0] if credentials is not None else ""
        password = credentials[1] if credentials is not None else ""
        username_matches = secrets.compare_digest(username, "collector")
        token_matches = secrets.compare_digest(password, auth_token)
        if not (username_matches and token_matches):
            raise HTTPException(
                status_code=http_status.HTTP_401_UNAUTHORIZED,
                detail="Collector web authentication required",
                headers={"WWW-Authenticate": 'Basic realm="Telegram Collector"'},
            )

    async def require_allowed_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            raise HTTPException(
                status_code=http_status.HTTP_403_FORBIDDEN,
                detail="Request origin is not allowed",
            )

    async def require_json_async(request: Request) -> None:
        require_json(request)

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
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(allowed_hosts))
    app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=max_body_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.state.collector_runtime = collector_runtime
    index_html = (asset_dir / "index.html").read_text(encoding="utf-8")
    stylesheet = (asset_dir / "styles.css").read_text(encoding="utf-8")
    javascript = (asset_dir / "app.js").read_text(encoding="utf-8")

    authenticated = [Depends(require_web_auth)]
    api_authenticated = [Depends(require_api_rate_limit), Depends(require_web_auth)]
    login_protected = [
        Depends(require_api_rate_limit),
        Depends(require_login_rate_limit),
        Depends(require_web_auth),
        Depends(require_allowed_origin),
        Depends(require_json_async),
    ]

    @app.get("/", response_class=HTMLResponse, dependencies=authenticated)
    async def index() -> str:
        return index_html

    @app.get("/assets/styles.css", dependencies=authenticated)
    async def styles() -> Response:
        return Response(stylesheet, media_type="text/css")

    @app.get("/assets/app.js", dependencies=authenticated)
    async def script() -> Response:
        return Response(javascript, media_type="text/javascript")

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "phase": collector_runtime.status.phase}

    @app.get("/api/status", dependencies=api_authenticated)
    async def collector_status() -> dict[str, Any]:
        return collector_runtime.status.redacted_snapshot()

    @app.post("/api/login/code", dependencies=login_protected)
    async def submit_login_code(payload: LoginCodeInput) -> dict[str, Any]:
        try:
            await collector_runtime.submit_code(payload.code)
        except (LoginRejected, LoginPhaseError) as exc:
            raise login_http_error(exc) from exc
        return collector_runtime.status.redacted_snapshot()

    @app.post("/api/login/password", dependencies=login_protected)
    async def submit_login_password(payload: LoginPasswordInput) -> dict[str, Any]:
        try:
            await collector_runtime.submit_password(payload.password)
        except (LoginRejected, LoginPhaseError) as exc:
            raise login_http_error(exc) from exc
        return collector_runtime.status.redacted_snapshot()

    @app.post("/api/login/resend", dependencies=login_protected)
    async def resend_login_code() -> dict[str, Any]:
        try:
            await collector_runtime.resend_code()
        except (LoginRejected, LoginPhaseError) as exc:
            raise login_http_error(exc) from exc
        return collector_runtime.status.redacted_snapshot()

    return app


async def main() -> None:
    """Run the legacy terminal-login collector when the web UI is disabled."""
    errors = configuration_errors()
    if errors:
        raise RuntimeError("; ".join(errors))

    log(startup_description())
    backend = Backend()
    client = secure_telegram_client_factory(SESSION_PATH, API_ID, API_HASH)
    await client.start(phone=PHONE or None)
    try:
        me = await client.get_me()
        STATUS.set_account(me)
        publish(
            "authorized",
            f"Telegram session authorized as {get_display_name(me) or getattr(me, 'id', 'unknown')}",
        )
        publish("registering", "Registering approved Telegram groups and channels")
        approved_entities: dict[int, Any] = {}
        await register_dialogs(backend, client, approved_entities)

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
            await process_claim(backend, client, claim, approved_entities)
    finally:
        await backend.close()
        await client.disconnect()


def run_web_server() -> None:
    validate_web_binding()
    if WEB_AUTH_TOKEN_GENERATED:
        print(
            "Generated collector web credentials (shown once): "
            f"username=collector password={WEB_AUTH_TOKEN}",
            flush=True,
        )
    scheme = "https" if WEB_TLS_CERT_FILE and WEB_TLS_KEY_FILE else "http"
    log(f"Collector web interface available at {scheme}://{WEB_HOST}:{WEB_PORT}")
    uvicorn.run(
        create_app(),
        host=WEB_HOST,
        port=WEB_PORT,
        log_level="info",
        ssl_certfile=WEB_TLS_CERT_FILE or None,
        ssl_keyfile=WEB_TLS_KEY_FILE or None,
    )


if __name__ == "__main__":
    if WEB_ENABLED:
        run_web_server()
    else:
        asyncio.run(main())

import asyncio
import base64
import importlib.util
import os
import stat
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from telethon.tl.types import Channel, ChatPhotoEmpty, User
from telethon.utils import get_peer_id


COLLECTOR_PATH = Path(__file__).resolve().parents[1] / "collector.py"
SPEC = importlib.util.spec_from_file_location(
    "external_collector_security", COLLECTOR_PATH
)
assert SPEC and SPEC.loader
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def channel(channel_id: int = 123) -> Channel:
    return Channel(
        id=channel_id,
        title="Approved channel",
        photo=ChatPhotoEmpty(),
        date=datetime.now(timezone.utc),
        broadcast=True,
        access_hash=999,
    )


class FakeDialog:
    def __init__(self, entity) -> None:
        self.entity = entity
        self.name = entity.title


class FakeDialogClient:
    def __init__(self, dialogs) -> None:
        self.dialogs = dialogs
        self.iterated = False

    async def iter_dialogs(self):
        self.iterated = True
        for dialog in self.dialogs:
            yield dialog


class FakeRegistrationBackend:
    def __init__(self) -> None:
        self.entities = []

    async def upsert_chat(self, dialog) -> bool:
        self.entities.append(dialog.entity)
        return True


def valid_claim(*, telegram_chat_id: int, chat_type: str = "channel") -> dict:
    end = datetime.now(timezone.utc) - timedelta(minutes=1)
    return {
        "run_id": str(uuid.uuid4()),
        "chat": {
            "id": str(uuid.uuid4()),
            "telegram_chat_id": telegram_chat_id,
            "title": "Approved channel",
            "chat_type": chat_type,
        },
        "requested_start": (end - timedelta(hours=1)).isoformat(),
        "requested_end": end.isoformat(),
        "after_message_id": 0,
    }


def test_empty_allowlist_only_lists_dialogs_locally_and_fails_closed(
    monkeypatch,
) -> None:
    async def scenario():
        monkeypatch.setattr(collector, "REGISTER_CHAT_IDS", set())
        monkeypatch.setattr(collector, "ALL_CHATS", False)
        client = FakeDialogClient([FakeDialog(channel())])
        backend = FakeRegistrationBackend()
        approved = {}

        summary = await collector.register_dialogs(backend, client, approved)

        assert summary["registered"] == 0
        assert summary["supported"] == 1
        assert client.iterated is True
        assert backend.entities == []
        assert approved == {}

    asyncio.run(scenario())


def test_registration_retains_only_canonical_approved_peers(monkeypatch) -> None:
    async def scenario():
        entity = channel()
        canonical_id = int(get_peer_id(entity))
        monkeypatch.setattr(collector, "REGISTER_CHAT_IDS", {canonical_id})
        monkeypatch.setattr(collector, "ALL_CHATS", False)
        client = FakeDialogClient([FakeDialog(entity)])
        backend = FakeRegistrationBackend()
        approved = {}

        summary = await collector.register_dialogs(backend, client, approved)

        assert summary["registered"] == 1
        assert approved == {canonical_id: entity}
        assert canonical_id < 0

    asyncio.run(scenario())


def test_backend_registration_uses_canonical_id_and_omits_access_hash(
    monkeypatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"chat": {"id": "backend-chat"}}

    class FakeHttpClient:
        def __init__(self) -> None:
            self.payload = None

        async def post(self, _path, *, json):
            self.payload = json
            return FakeResponse()

    async def scenario():
        monkeypatch.setattr(collector, "INCLUDE_RAW_METADATA", False)
        backend = object.__new__(collector.Backend)
        backend.client = FakeHttpClient()
        entity = channel()

        assert await backend.upsert_chat(FakeDialog(entity)) is True
        assert backend.client.payload["telegram_chat_id"] == int(get_peer_id(entity))
        assert backend.client.payload["access_hash"] is None

    asyncio.run(scenario())


def test_claim_requires_local_approval_type_and_bounded_time(monkeypatch) -> None:
    entity = channel()
    canonical_id = int(get_peer_id(entity))
    monkeypatch.setattr(
        collector,
        "INITIAL_SYNC_FROM",
        (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(),
    )
    monkeypatch.setattr(collector, "MAX_SYNC_RANGE_DAYS", 2)
    claim = collector.validate_claim_payload(valid_claim(telegram_chat_id=canonical_id))

    assert collector.approved_entity_for_claim(claim, {canonical_id: entity}) is entity
    with pytest.raises(RuntimeError, match="outside the local allowlist"):
        collector.approved_entity_for_claim(claim, {})

    user = User(id=42, first_name="Private", last_name="Conversation")
    user_claim = collector.validate_claim_payload(
        valid_claim(telegram_chat_id=int(get_peer_id(user)))
    )
    with pytest.raises(RuntimeError, match="peer type"):
        collector.approved_entity_for_claim(user_claim, {int(get_peer_id(user)): user})

    overbroad = valid_claim(telegram_chat_id=canonical_id)
    overbroad["requested_start"] = (
        datetime.now(timezone.utc) - timedelta(days=3)
    ).isoformat()
    with pytest.raises(RuntimeError, match="MAX_SYNC_RANGE_DAYS"):
        collector.validate_claim_payload(overbroad)

    naive = valid_claim(telegram_chat_id=canonical_id)
    naive["requested_start"] = datetime.now().replace(microsecond=0).isoformat()
    with pytest.raises(RuntimeError, match="timezone"):
        collector.validate_claim_payload(naive)


def test_backend_transport_requires_tls_or_explicit_loopback_override(
    monkeypatch,
) -> None:
    monkeypatch.setattr(collector, "BACKEND_URL", "http://192.168.1.50:8000")
    monkeypatch.setattr(collector, "ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP", True)
    with pytest.raises(collector.ConfigurationError, match="must use HTTPS"):
        collector.validate_backend_transport()

    monkeypatch.setattr(collector, "BACKEND_URL", "http://127.0.0.1:8000")
    monkeypatch.setattr(collector, "ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP", False)
    assert any("must use HTTPS" in error for error in collector.configuration_errors())
    monkeypatch.setattr(collector, "ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP", True)
    collector.validate_backend_transport()


def test_default_initial_sync_boundary_has_buffer_for_thirty_day_reports() -> None:
    now = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)

    assert datetime.fromisoformat(collector.default_initial_sync_from(now)) == (
        now - timedelta(days=31)
    )


def test_remote_web_binding_requires_direct_tls(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(collector, "WEB_HOST", "0.0.0.0")
    monkeypatch.setattr(collector, "WEB_ALLOW_REMOTE", True)
    monkeypatch.setenv("COLLECTOR_WEB_AUTH_TOKEN", "x" * 32)
    monkeypatch.setenv("COLLECTOR_WEB_ALLOWED_HOSTS", "collector.example.com")
    monkeypatch.setenv("COLLECTOR_WEB_ALLOWED_ORIGINS", "https://collector.example.com")
    monkeypatch.setattr(collector, "WEB_TLS_CERT_FILE", "")
    monkeypatch.setattr(collector, "WEB_TLS_KEY_FILE", "")
    with pytest.raises(collector.ConfigurationError, match="TLS_CERT_FILE"):
        collector.validate_web_binding()

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("certificate", encoding="utf-8")
    key.write_text("private-key", encoding="utf-8")
    monkeypatch.setattr(collector, "WEB_TLS_CERT_FILE", str(cert))
    monkeypatch.setattr(collector, "WEB_TLS_KEY_FILE", str(key))
    collector.validate_web_binding()


def test_session_state_is_private_and_symlinks_are_rejected(tmp_path) -> None:
    previous_umask = os.umask(0o077)
    os.umask(previous_umask)
    try:
        session_path = tmp_path / "state" / "account.session"
        collector.prepare_session_path(str(session_path))
        assert stat.S_IMODE(session_path.parent.stat().st_mode) == 0o700

        session_path.write_bytes(b"session")
        session_path.chmod(0o666)
        collector.harden_session_files(str(session_path))
        assert stat.S_IMODE(session_path.stat().st_mode) == 0o600

        target = tmp_path / "target"
        target.write_bytes(b"target")
        symlink_path = tmp_path / "linked.session"
        symlink_path.symlink_to(target)
        with pytest.raises(collector.ConfigurationError, match="symlinked"):
            collector.harden_session_files(str(symlink_path))
    finally:
        os.umask(previous_umask)


def test_raw_telegram_objects_are_opt_in(monkeypatch) -> None:
    message = SimpleNamespace(
        id=1,
        date=datetime.now(timezone.utc),
        edit_date=None,
        sender_id=2,
        post_author=None,
        action=None,
        photo=None,
        document=None,
        reactions=None,
        forward=None,
        reply_to_msg_id=None,
        message="hello",
        to_dict=lambda: {"sensitive_protocol_field": "value"},
    )
    monkeypatch.setattr(collector, "INCLUDE_RAW_METADATA", False)
    assert "raw" not in collector.normalize_message(message, None)
    monkeypatch.setattr(collector, "INCLUDE_RAW_METADATA", True)
    assert collector.normalize_message(message, None)["raw"] == {
        "sensitive_protocol_field": "value"
    }


def test_media_progress_guard_stops_before_disk_quota(monkeypatch) -> None:
    monkeypatch.setattr(collector, "MAX_MEDIA_FILE_BYTES", 100)
    guard = collector.media_download_progress_guard(80)
    guard(80, 1000)
    with pytest.raises(RuntimeError, match="remaining media byte quota"):
        guard(81, 1000)

    guard = collector.media_download_progress_guard(1000)
    with pytest.raises(RuntimeError, match="MAX_MEDIA_FILE_BYTES"):
        guard(101, 1000)


def test_web_requires_basic_auth_redacts_status_and_caps_chunked_bodies() -> None:
    async def scenario():
        status = collector.CollectorStatus()
        status.account = {
            "id": 1,
            "display_name": "Collector User",
            "username": "collector_user",
            "phone": "4912345",
        }
        status.add_event("Sensitive local path /tmp/private")
        runtime = collector.CollectorRuntime(
            status_store=status, client_factory=lambda *_args: None
        )
        token = "x" * 32
        app = collector.create_app(
            runtime,
            auth_token=token,
            allowed_hosts=("collector.test",),
            allowed_origins=("http://collector.test",),
            max_body_bytes=64,
            api_requests_per_minute=100,
            login_attempts_per_minute=100,
        )
        authorization = (
            "Basic " + base64.b64encode(f"collector:{token}".encode()).decode()
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://collector.test"
        ) as client:
            unauthorized = await client.get("/api/status")
            assert unauthorized.status_code == 401
            assert unauthorized.headers["www-authenticate"].startswith("Basic")

            response = await client.get(
                "/api/status", headers={"Authorization": authorization}
            )
            assert response.status_code == 200
            assert response.json()["account"] == {
                "display_name": "Connected Telegram account",
                "username": None,
            }
            assert response.json()["events"] == []
            assert "no-store" in response.headers["cache-control"]
            assert (
                "frame-ancestors 'none'" in response.headers["content-security-policy"]
            )

            bad_origin = await client.post(
                "/api/login/resend",
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Origin": "http://evil.test",
                },
                content=b"{}",
            )
            assert bad_origin.status_code == 403

            async def oversized_chunks():
                yield b"{" + b"x" * 40
                yield b"x" * 40 + b"}"

            oversized = await client.post(
                "/api/login/resend",
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Origin": "http://collector.test",
                },
                content=oversized_chunks(),
            )
            assert oversized.status_code == 413

            bad_host = await client.get(
                "/api/status",
                headers={"Authorization": authorization, "Host": "evil.test"},
            )
            assert bad_host.status_code == 400

    asyncio.run(scenario())


def test_rate_limiter_is_async_safe() -> None:
    async def scenario():
        limiter = collector.AsyncRateLimiter(1)
        await limiter.check("client")
        with pytest.raises(collector.HTTPException) as error:
            await limiter.check("client")
        assert error.value.status_code == 429

    asyncio.run(scenario())


def test_generated_web_password_is_printed_once_for_basic_prompt(
    monkeypatch, capsys
) -> None:
    calls = []
    monkeypatch.setattr(collector, "WEB_HOST", "127.0.0.1")
    generated_secret = "generated-secret-with-32-characters"
    monkeypatch.setattr(collector, "WEB_AUTH_TOKEN", generated_secret)
    monkeypatch.setattr(collector, "WEB_AUTH_TOKEN_GENERATED", True)
    monkeypatch.setattr(collector, "WEB_TLS_CERT_FILE", "")
    monkeypatch.setattr(collector, "WEB_TLS_KEY_FILE", "")
    monkeypatch.setattr(
        collector.uvicorn, "run", lambda *args, **kwargs: calls.append(kwargs)
    )

    collector.run_web_server()

    output = capsys.readouterr().out
    assert output.count(f"password={generated_secret}") == 1
    assert calls[0]["ssl_certfile"] is None

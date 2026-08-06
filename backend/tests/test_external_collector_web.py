import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.types import User


collector_spec = importlib.util.spec_from_file_location(
    "external_telegram_collector_web",
    Path(__file__).resolve().parents[2] / "external_telegram_collector" / "collector.py",
)
assert collector_spec and collector_spec.loader
collector = importlib.util.module_from_spec(collector_spec)
collector_spec.loader.exec_module(collector)


class StopPolling(RuntimeError):
    pass


class FakeClient:
    def __init__(self, authorized: bool = False, sign_in_errors=None) -> None:
        self.authorized = authorized
        self.sign_in_errors = list(sign_in_errors or [])
        self.code_requests = 0
        self.sign_in_calls = []
        self.start_calls = []
        self.connected = False
        self.disconnected = False
        self.user = User(
            id=12345,
            first_name="Collector",
            last_name="User",
            username="collector_user",
            phone="4912345",
        )

    async def connect(self):
        self.connected = True

    async def start(self, phone=None):
        self.start_calls.append(phone)
        self.authorized = True
        return self

    async def is_user_authorized(self):
        return self.authorized

    async def send_code_request(self, _phone):
        self.code_requests += 1
        return SimpleNamespace(phone_code_hash=f"hash-{self.code_requests}")

    async def sign_in(self, **kwargs):
        self.sign_in_calls.append(kwargs)
        if self.sign_in_errors:
            error = self.sign_in_errors.pop(0)
            if error is not None:
                raise error
        self.authorized = True

    async def get_me(self):
        return self.user

    async def iter_dialogs(self):
        if False:
            yield None

    async def disconnect(self):
        self.disconnected = True


class FakeBackend:
    def __init__(self) -> None:
        self.closed = False

    async def claim_next(self):
        raise StopPolling("polling reached")

    async def close(self):
        self.closed = True


@pytest.fixture
def valid_collector_config(monkeypatch):
    monkeypatch.setattr(collector, "CONFIG_ERRORS", [])
    monkeypatch.setattr(collector, "API_ID", 123456)
    monkeypatch.setattr(collector, "API_HASH", "api-hash")
    monkeypatch.setattr(collector, "INGEST_TOKEN", "tg_ingest_secret")
    monkeypatch.setattr(collector, "PHONE", "+4912345")
    monkeypatch.setattr(collector, "INITIAL_SYNC_FROM", "2026-01-01T00:00:00+00:00")
    monkeypatch.setattr(collector, "SYNC_INTERVAL_MINUTES", 60)


def runtime_for(client, status=None):
    return collector.CollectorRuntime(
        status_store=status or collector.CollectorStatus(),
        client_factory=lambda *_args: client,
        backend_factory=FakeBackend,
        sleep=asyncio.sleep,
    )


def test_existing_session_bypasses_web_login(valid_collector_config) -> None:
    async def scenario():
        client = FakeClient(authorized=True)
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)

        with pytest.raises(StopPolling):
            await runtime.run_once()

        assert client.code_requests == 0
        assert status.account["display_name"] == "Collector User"
        assert client.disconnected is True

    asyncio.run(scenario())


def test_fresh_session_accepts_code_without_exposing_it(valid_collector_config) -> None:
    async def scenario():
        client = FakeClient()
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)
        task = asyncio.create_task(runtime.run_once())

        while status.phase != "awaiting_code":
            await asyncio.sleep(0)
        await runtime.submit_code("777888")

        with pytest.raises(StopPolling):
            await task
        assert client.sign_in_calls[0]["code"] == "777888"
        assert "777888" not in json.dumps(status.snapshot())
        assert status.account["username"] == "collector_user"

    asyncio.run(scenario())


def test_login_transitions_to_two_step_password(valid_collector_config) -> None:
    async def scenario():
        client = FakeClient(sign_in_errors=[SessionPasswordNeededError(None), None])
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)
        task = asyncio.create_task(runtime.run_once())

        while status.phase != "awaiting_code":
            await asyncio.sleep(0)
        await runtime.submit_code("777888")
        assert status.phase == "awaiting_password"
        await runtime.submit_password("account-secret")

        with pytest.raises(StopPolling):
            await task
        snapshot = json.dumps(status.snapshot())
        assert "777888" not in snapshot
        assert "account-secret" not in snapshot

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PhoneCodeInvalidError(None), "invalid"),
        (PhoneCodeExpiredError(None), "expired"),
    ],
)
def test_rejected_codes_remain_retryable(error, expected, valid_collector_config) -> None:
    async def scenario():
        client = FakeClient(sign_in_errors=[error])
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)
        runtime.client = client
        runtime.phone_code_hash = "active-hash"
        status.set_phase("awaiting_code", "Waiting")

        with pytest.raises(collector.LoginRejected, match=expected):
            await runtime.submit_code("99999")
        assert status.phase == "awaiting_code"

    asyncio.run(scenario())


def test_rejected_password_remains_retryable(valid_collector_config) -> None:
    async def scenario():
        client = FakeClient(sign_in_errors=[PasswordHashInvalidError(None)])
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)
        runtime.client = client
        status.set_phase("awaiting_password", "Waiting")

        with pytest.raises(collector.LoginRejected, match="invalid"):
            await runtime.submit_password("wrong-secret")
        assert status.phase == "awaiting_password"
        assert "wrong-secret" not in json.dumps(status.snapshot())

    asyncio.run(scenario())


def test_resend_replaces_active_phone_code_hash(valid_collector_config) -> None:
    async def scenario():
        client = FakeClient()
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)
        runtime.client = client
        runtime.phone_code_hash = "old-hash"
        status.set_phase("awaiting_code", "Waiting")

        await runtime.resend_code()

        assert client.code_requests == 1
        assert runtime.phone_code_hash == "hash-1"
        assert status.phase == "awaiting_code"

    asyncio.run(scenario())


def test_status_tracks_runs_and_bounds_events() -> None:
    status = collector.CollectorStatus(event_limit=2)
    claim = {
        "run_id": "run-1",
        "chat": {"title": "News", "telegram_chat_id": 42},
        "requested_start": "2026-08-01T00:00:00+00:00",
        "requested_end": "2026-08-02T00:00:00+00:00",
    }

    status.add_event("first")
    status.add_event("second")
    status.add_event("third", "warning")
    status.start_run(claim)
    status.update_run(messages_seen=12, attachments_seen=3, attachments_failed=1)
    status.finish_run("failed", error="flood wait", retry_after_seconds=30)
    snapshot = status.snapshot()

    assert [event["message"] for event in snapshot["events"]] == ["second", "third"]
    assert snapshot["current_run"] is None
    assert snapshot["last_run"]["messages_seen"] == 12
    assert snapshot["last_run"]["retry_after_seconds"] == 30


def test_web_routes_validate_phase_content_type_and_redact_code(valid_collector_config) -> None:
    async def scenario():
        client = FakeClient()
        status = collector.CollectorStatus()
        runtime = runtime_for(client, status)
        app = collector.create_app(runtime)
        endpoints = {
            route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")
        }

        assert "Collector status" in await endpoints["/"]()
        assert b"fetchStatus" in (await endpoints["/assets/app.js"]()).body
        assert (await endpoints["/health"]())["ok"] is True
        assert {
            "/health",
            "/api/status",
            "/api/login/code",
            "/api/login/password",
            "/api/login/resend",
        }.issubset(endpoints)

        non_json_request = Request({"type": "http", "method": "POST", "path": "/", "headers": []})
        with pytest.raises(HTTPException) as content_type_error:
            collector.require_json(non_json_request)
        assert content_type_error.value.status_code == 415

        with pytest.raises(HTTPException) as phase_error:
            await endpoints["/api/login/code"](collector.LoginCodeInput(code="12345"))
        assert phase_error.value.status_code == 409

        runtime.client = client
        runtime.phone_code_hash = "active-hash"
        status.set_phase("awaiting_code", "Waiting")
        response = await endpoints["/api/login/code"](collector.LoginCodeInput(code="777888"))

        assert response["phase"] == "authorized"
        assert "777888" not in json.dumps(response)

    asyncio.run(scenario())


def test_legacy_main_still_uses_telethon_start(monkeypatch, valid_collector_config) -> None:
    async def scenario():
        client = FakeClient()
        backend = FakeBackend()
        monkeypatch.setattr(collector, "TelegramClient", lambda *_args: client)
        monkeypatch.setattr(collector, "Backend", lambda: backend)

        with pytest.raises(StopPolling):
            await collector.main()

        assert client.start_calls == [collector.PHONE]
        assert backend.closed is True
        assert client.disconnected is True

    asyncio.run(scenario())

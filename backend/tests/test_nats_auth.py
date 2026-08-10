import asyncio

from app import nats_client


def test_nats_connect_preserves_authenticated_url(monkeypatch) -> None:
    calls = []

    async def connect(url, **options):
        calls.append((url, options))
        return object()

    monkeypatch.setattr(nats_client.nats, "connect", connect)
    monkeypatch.setattr(nats_client.settings, "nats_url", "nats://app:encoded%21password@nats:4222")
    monkeypatch.setattr(nats_client.settings, "nats_user", "")
    monkeypatch.setattr(nats_client.settings, "nats_password", "")
    monkeypatch.setattr(nats_client.settings, "nats_token", "")

    asyncio.run(nats_client.connect_nats())

    assert calls == [("nats://app:encoded%21password@nats:4222", {})]


def test_nats_connect_supports_explicit_token(monkeypatch) -> None:
    calls = []

    async def connect(url, **options):
        calls.append((url, options))
        return object()

    monkeypatch.setattr(nats_client.nats, "connect", connect)
    monkeypatch.setattr(nats_client.settings, "nats_url", "nats://nats:4222")
    monkeypatch.setattr(nats_client.settings, "nats_user", "")
    monkeypatch.setattr(nats_client.settings, "nats_password", "")
    monkeypatch.setattr(nats_client.settings, "nats_token", "strong-token")

    asyncio.run(nats_client.connect_nats())

    assert calls == [("nats://nats:4222", {"token": "strong-token"})]

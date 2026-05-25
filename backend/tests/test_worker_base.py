import asyncio

from nats.errors import TimeoutError as NATSTimeoutError

from app.workers.base import Worker


class DummyWorker(Worker):
    subject = "jobs.test"
    durable = "test-worker"
    queue = "test"

    async def handle(self, session, payload):
        return None


class TimeoutSubscription:
    async def fetch(self, batch: int, timeout: int):
        raise NATSTimeoutError


class MessageSubscription:
    async def fetch(self, batch: int, timeout: int):
        return ["message"]


def test_fetch_messages_returns_empty_list_on_nats_timeout():
    worker = DummyWorker()
    messages = asyncio.run(worker._fetch_messages(TimeoutSubscription()))
    assert messages == []


def test_fetch_messages_returns_available_messages():
    worker = DummyWorker()
    messages = asyncio.run(worker._fetch_messages(MessageSubscription()))
    assert messages == ["message"]

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

from nats.errors import TimeoutError as NATSTimeoutError

from app.models import JobStatus
from app.workers import base
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


class AsyncioTimeoutSubscription:
    async def fetch(self, batch: int, timeout: int):
        raise asyncio.TimeoutError


class MessageSubscription:
    async def fetch(self, batch: int, timeout: int):
        assert batch == 1
        return ["message"]


def test_fetch_messages_returns_empty_list_on_nats_timeout():
    worker = DummyWorker()
    messages = asyncio.run(worker._fetch_messages(TimeoutSubscription()))
    assert messages == []


def test_fetch_messages_returns_available_messages():
    worker = DummyWorker()
    messages = asyncio.run(worker._fetch_messages(MessageSubscription()))
    assert messages == ["message"]


def test_fetch_messages_returns_empty_list_on_asyncio_timeout():
    worker = DummyWorker()
    messages = asyncio.run(worker._fetch_messages(AsyncioTimeoutSubscription()))
    assert messages == []


def test_worker_persists_attempt_before_handler_failure(monkeypatch):
    operations = []
    job = SimpleNamespace(id=uuid.uuid4(), status=JobStatus.queued)

    class Result:
        def scalar_one_or_none(self):
            return None

    class Session:
        async def execute(self, query):
            return Result()

        def add(self, value):
            operations.append("add")

        async def flush(self):
            operations.append("flush")

        async def commit(self):
            operations.append("commit")

        async def rollback(self):
            operations.append("rollback")

    session = Session()

    @asynccontextmanager
    async def session_local(**kwargs):
        yield session

    async def get_job(session, job_id):
        return job

    class FailingWorker(DummyWorker):
        async def handle(self, session, payload):
            operations.append("handle")
            raise RuntimeError("boom")

        async def _record_failure(self, job_id, task_key, payload, exc, **kwargs):
            operations.append("record_failure")
            return "nak"

    @asynccontextmanager
    async def claim(task_key, **kwargs):
        yield SimpleNamespace(invalidated=False)

    monkeypatch.setattr(base, "claim_worker_task", claim)
    monkeypatch.setattr(base, "SessionLocal", session_local)
    monkeypatch.setattr(base, "get_job", get_job)

    action = asyncio.run(
        FailingWorker()._handle_message(
            {"job_id": str(job.id), "task_key": f"test:{job.id}"}
        )
    )

    assert action == "nak"
    assert operations.index("commit") < operations.index("handle")
    assert operations.index("rollback") < operations.index("record_failure")


def test_busy_claim_does_not_start_or_ack_task(monkeypatch):
    @asynccontextmanager
    async def busy(task_key, **kwargs):
        yield None

    monkeypatch.setattr(base, "claim_worker_task", busy)
    assert asyncio.run(DummyWorker()._handle_message({"job_id": str(uuid.uuid4())})) == "nak"

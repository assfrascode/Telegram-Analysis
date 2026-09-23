"""Real PostgreSQL ownership regressions; requires a disposable migrated database."""
import asyncio
import json
import os
import sys
import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Job, JobStatus, StepStatus, User, WorkerTask
from app.services import worker_control
from app.workers import base
from app.workers.base import Worker

DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Requires disposable PostgreSQL database")


@asynccontextmanager
async def database(monkeypatch):
    if not (make_url(DATABASE_URL).database or "").endswith("_migration_test"):
        raise RuntimeError("Database name must end in _migration_test")
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker_control, "engine", engine)
    monkeypatch.setattr(base, "SessionLocal", factory)
    try:
        async with factory() as session:
            user = User(email=f"{uuid.uuid4()}@ownership.test", password_hash="unused")
            session.add(user)
            await session.flush()
            job = Job(owner_user_id=user.id, status=JobStatus.running, options={})
            session.add(job)
            await session.commit()
        yield job, factory
    finally:
        await engine.dispose()


class ProbeWorker(Worker):
    subject = "jobs.ownership.test"
    durable = "ownership-test"
    queue = "ownership-test"

    async def handle(self, session, payload):
        pass


def test_two_workers_long_task_and_downstream_replay(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as (job, factory):
            entered = asyncio.Event()
            release = asyncio.Event()
            published = []
            executions = []
            downstream = {"job_id": str(job.id), "task_key": f"downstream:{job.id}"}

            async def publish(js, subject, payload):
                published.append(dict(payload))

            monkeypatch.setattr(base, "publish_json", publish)

            class LongWorker(ProbeWorker):
                async def handle(self, session, payload):
                    executions.append(payload["task_key"])
                    # Commits inside a handler must not release ownership.
                    await session.commit()
                    await self.enqueue(self.subject, downstream)
                    entered.set()
                    await release.wait()

            payload = {"job_id": str(job.id), "task_key": f"first:{job.id}"}
            first = asyncio.create_task(LongWorker()._handle_message(dict(payload)))
            await asyncio.wait_for(entered.wait(), 5)
            try:
                assert await LongWorker()._handle_message(dict(payload)) == "nak"
                async with factory() as session:
                    task = (await session.execute(select(WorkerTask).where(
                        WorkerTask.task_key == payload["task_key"],
                    ))).scalar_one()
                    assert task.attempts == 1
                # A separate task remains runnable while the first is held.
                assert await ProbeWorker()._handle_message({
                    "job_id": str(job.id), "task_key": f"second:{job.id}",
                }) == "ack"
            finally:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            # Restart after publication, before the parent completion commit.
            release.set()
            assert await LongWorker()._handle_message(dict(payload)) == "ack"
            assert await LongWorker()._handle_message(dict(payload)) == "ack"
            assert len(executions) == 2  # abandoned attempt and recovery, never overlapping
            assert len(published) == 2

            downstream_runs = []

            class DownstreamWorker(ProbeWorker):
                async def handle(self, session, payload):
                    downstream_runs.append(payload["task_key"])

            results = await asyncio.gather(*(
                DownstreamWorker()._handle_message(dict(message)) for message in published
            ))
            for result, message in zip(results, published):
                if result == "nak":
                    assert await DownstreamWorker()._handle_message(message) == "ack"
            assert downstream_runs == [downstream["task_key"]]
    asyncio.run(scenario())


def test_killed_worker_claim_recovers_without_timeout(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as (job, factory):
            payload = {"job_id": str(job.id), "task_key": f"killed:{job.id}"}
            code = '''
import asyncio, json, os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.services import worker_control
from app.workers import base
async def main():
    engine = create_async_engine(os.environ["MIGRATION_TEST_DATABASE_URL"])
    worker_control.engine = engine
    base.SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    class Child(base.Worker):
        subject = "jobs.ownership.test"
        async def handle(self, session, payload):
            await session.commit()
            print("OWNED", flush=True)
            await asyncio.Event().wait()
    await Child()._handle_message(json.loads(os.environ["OWNERSHIP_PAYLOAD"]))
asyncio.run(main())
'''
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-c", code,
                env={**os.environ, "OWNERSHIP_PAYLOAD": json.dumps(payload)},
                stdout=asyncio.subprocess.PIPE,
            )
            try:
                assert await asyncio.wait_for(process.stdout.readline(), 15) == b"OWNED\n"
                assert await ProbeWorker()._handle_message(dict(payload)) == "nak"
            finally:
                if process.returncode is None:
                    process.kill()
                await process.wait()
            for _ in range(100):
                if await ProbeWorker()._handle_message(dict(payload)) == "ack":
                    break
                await asyncio.sleep(0.02)
            else:
                pytest.fail("Abandoned task did not recover")
            async with factory() as session:
                task = (await session.execute(select(WorkerTask).where(
                    WorkerTask.task_key == payload["task_key"],
                ))).scalar_one()
                assert task.status == StepStatus.completed
                assert task.attempts == 2
    asyncio.run(scenario())


def test_retry_failure_releases_claim_and_preserves_attempts(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as (job, factory):
            class FailingWorker(ProbeWorker):
                async def handle(self, session, payload):
                    raise worker_control.RetryableWorkerError("temporary")

                async def emit_event(self, *args, **kwargs):
                    pass

            payload = {"job_id": str(job.id), "task_key": f"retry:{job.id}"}
            assert await FailingWorker()._handle_message(dict(payload)) == "nak"
            assert await ProbeWorker()._handle_message(dict(payload)) == "ack"
            async with factory() as session:
                task = (await session.execute(select(WorkerTask).where(
                    WorkerTask.task_key == payload["task_key"],
                ))).scalar_one()
                assert task.attempts == 2
                assert task.status == StepStatus.completed
    asyncio.run(scenario())

"""Cancelled restart isolation against running and delayed workers in PostgreSQL."""
import asyncio
import os
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Job, JobStatus, Question, StepStatus, Upload, UploadStatus, User, WorkerTask
from app.services import worker_control
from app.services.jobs import initial_task_payload, restart_cancelled_job
from app.workers import base, subjects

DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Requires disposable PostgreSQL database")


def test_cancelled_restart_isolated_from_running_and_delayed_old_tasks(monkeypatch):
    async def scenario():
        if not (make_url(DATABASE_URL).database or "").endswith("_migration_test"):
            raise RuntimeError("Database name must end in _migration_test")
        engine = create_async_engine(DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(worker_control, "engine", engine)
        monkeypatch.setattr(base, "SessionLocal", factory)
        entered, release = asyncio.Event(), asyncio.Event()
        executions = []

        class LongWorker(base.Worker):
            subject = subjects.VALIDATE

            async def handle(self, session, payload):
                entered.set()
                await release.wait()
                await self.raise_if_cancelled(session, uuid.UUID(payload["job_id"]))

        class RestartedWorker(base.Worker):
            subject = subjects.VALIDATE

            async def handle(self, session, payload):
                executions.append(payload["job_id"])

        running = None
        try:
            async with factory() as session:
                user = User(email=f"{uuid.uuid4()}@restart.test", password_hash="unused")
                session.add(user)
                await session.flush()
                upload = Upload(
                    owner_user_id=user.id, filename="source.zip", size_bytes=1,
                    object_key=f"uploads/{uuid.uuid4()}.zip", status=UploadStatus.uploaded,
                )
                session.add(upload)
                await session.flush()
                original = Job(owner_user_id=user.id, upload_id=upload.id, status=JobStatus.running)
                session.add(original)
                await session.flush()
                session.add(Question(job_id=original.id, question_index=1, text="Original question"))
                await session.commit()
            old_payload = initial_task_payload(original)
            running = asyncio.create_task(LongWorker()._handle_message(dict(old_payload)))
            await asyncio.wait_for(entered.wait(), 5)
            async with factory() as session:
                job = await session.get(Job, original.id)
                await worker_control.mark_job_cancelled(session, job)
                await session.commit()
            # A terminal label alone must not authorize mutation of running workers.
            async with worker_control.claim_job_lifecycle(original.id) as connection:
                assert connection is None
            release.set()
            assert await asyncio.wait_for(running, 5) == "ack"

            async with worker_control.claim_job_lifecycle(original.id) as connection:
                assert connection is not None
                async with factory(bind=connection) as session:
                    original = await session.get(Job, original.id)
                    restarted = await restart_cancelled_job(session, original)
                    await session.commit()
            fresh_payload = initial_task_payload(restarted)
            worker = RestartedWorker()
            assert await worker._handle_message(dict(old_payload)) == "ack"
            assert await worker._handle_message({
                "job_id": str(original.id), "task_key": f"delayed-old:{original.id}",
            }) == "ack"
            assert await worker._handle_message(dict(fresh_payload)) == "ack"
            assert await worker._handle_message(dict(fresh_payload)) == "ack"
            assert executions == [str(restarted.id)]
            async with factory() as session:
                assert (await session.get(Job, original.id)).status == JobStatus.cancelled
                assert (await session.get(Job, restarted.id)).status == JobStatus.queued
                questions = (await session.execute(select(Question).where(
                    Question.job_id == restarted.id,
                ))).scalars().all()
                assert [row.text for row in questions] == ["Original question"]
                tasks = (await session.execute(select(WorkerTask).where(
                    WorkerTask.job_id == original.id,
                ))).scalars().all()
                assert len(tasks) == 2
                assert all(task.status == StepStatus.skipped for task in tasks)
        finally:
            if running is not None and not running.done():
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
            await engine.dispose()

    asyncio.run(scenario())

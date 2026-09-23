"""Artifact lifecycle regressions against a migrated disposable PostgreSQL database."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models as m
from app.services import job_cleanup, worker_control

DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Requires disposable PostgreSQL database")
NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


class Storage:
    def __init__(self):
        self.keys = set()
        self.removed = []
        self.vector_jobs = []
        self.remove_attempts = 0
        self.fail_removal_at = None
        self.removal_error = RuntimeError("object storage unavailable")
        self.fail_vectors = False

    def list_objects(self, prefix):
        return [SimpleNamespace(object_name=key) for key in sorted(self.keys) if key.startswith(prefix)]

    def remove_object(self, key):
        self.remove_attempts += 1
        if self.remove_attempts == self.fail_removal_at:
            raise self.removal_error
        self.keys.discard(key)
        self.removed.append(key)

    async def delete_job_points(self, job_id):
        self.vector_jobs.append(job_id)
        if self.fail_vectors:
            raise RuntimeError("vector storage unavailable")


@asynccontextmanager
async def database(monkeypatch):
    if not (make_url(DATABASE_URL).database or "").endswith("_migration_test"):
        raise RuntimeError("Database name must end in _migration_test")
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    storage = Storage()
    monkeypatch.setattr(job_cleanup, "SessionLocal", factory)
    monkeypatch.setattr(worker_control, "engine", engine)
    monkeypatch.setattr(job_cleanup, "QdrantIndex", lambda: storage)
    monkeypatch.setattr(job_cleanup, "list_objects", storage.list_objects)
    monkeypatch.setattr(job_cleanup, "remove_object", storage.remove_object)
    monkeypatch.setattr(job_cleanup, "utc_now", lambda: NOW)
    monkeypatch.setattr(job_cleanup, "settings", SimpleNamespace(upload_expiry_hours=24))
    try:
        async with factory() as session:
            owner = m.User(email=f"{uuid.uuid4()}@cleanup.test", password_hash="unused")
            session.add(owner)
            await session.commit()
        yield factory, owner, storage
    finally:
        await engine.dispose()


async def add(session, model, **values):
    row = model(**values)
    session.add(row)
    await session.flush()
    return row


async def request(factory, job, owner):
    async with factory() as session:
        result = await job_cleanup.request_job_deletion(session, job.id, owner.id)
        await session.commit()
        return result


async def seed_graph(factory, owner, storage):
    """Include every job-owned FK family and shared records referencing the job."""
    owned = []

    async def own(session, model, **values):
        row = await add(session, model, **values)
        owned.append((model, row.id))
        return row

    async with factory() as session:
        upload = await add(session, m.Upload, owner_user_id=owner.id, filename="export.zip",
                           size_bytes=10, object_key=f"users/{owner.id}/uploads/{uuid.uuid4()}.zip",
                           status=m.UploadStatus.uploaded)
        job = await own(session, m.Job, owner_user_id=owner.id, upload_id=upload.id,
                        status=m.JobStatus.completed, completed_at=NOW)
        sibling = await add(session, m.Job, owner_user_id=owner.id, upload_id=upload.id,
                            status=m.JobStatus.completed, completed_at=NOW)
        prefix = f"users/{owner.id}/jobs/{job.id}/"
        private_key, shared_key, report_key, orphan_key = (
            prefix + suffix for suffix in ("extract/private.jpg", "extract/shared.jpg", "report.zip", "orphan")
        )
        chat = await add(session, m.TelegramChat, owner_user_id=owner.id, telegram_chat_id=1,
                         title="Shared chat", chat_type="group", initial_sync_from=NOW)
        collected_message = await add(session, m.CollectedTelegramMessage, chat_id=chat.id,
                                      owner_user_id=owner.id, telegram_message_id=1, timestamp=NOW)
        collected_media = await add(session, m.CollectedTelegramMedia, chat_id=chat.id,
                                    owner_user_id=owner.id, message_id=collected_message.id,
                                    telegram_media_key="photo", media_type="photo", filename="shared.jpg",
                                    minio_object_key=shared_key)
        collected_analysis = await add(session, m.CollectedMediaAnalysis, media_id=collected_media.id,
                                       model_name="vision", prompt_version="v1", description="Shared")
        collected_transcript = await add(session, m.CollectedMediaTranscript,
                                         media_id=collected_media.id, model_name="audio")
        message = await own(session, m.TelegramMessage, job_id=job.id, telegram_message_id=1)
        await own(session, m.MessageTranslation, job_id=job.id, message_id=message.id,
                  source_text_hash="hash", target_language="en", translated_text="Hello")
        private_media = await own(session, m.TelegramMedia, job_id=job.id, message_id=message.id,
                                  media_type="photo", original_path="private.jpg", minio_object_key=private_key)
        await own(session, m.TelegramMedia, job_id=job.id, message_id=message.id,
                  source_media_id=collected_media.id, media_type="photo", original_path="shared.jpg",
                  minio_object_key=shared_key)
        sibling_media = await add(session, m.TelegramMedia, job_id=sibling.id,
                                  source_media_id=collected_media.id, media_type="photo",
                                  original_path="shared.jpg", minio_object_key=shared_key)
        await own(session, m.MediaAnalysis, media_id=private_media.id, model_name="vision",
                  description="Private")
        transcript = await own(session, m.MediaTranscript, job_id=job.id, media_id=private_media.id,
                               model_name="audio")
        await own(session, m.MediaTranscriptTranslation, job_id=job.id, transcript_id=transcript.id,
                  source_text_hash="hash", translated_text="Transcript")
        chunk = await own(session, m.MessageChunk, job_id=job.id, chunk_index=0,
                          chunk_hash="hash", text="Evidence")
        question = await own(session, m.Question, job_id=job.id, question_index=0, text="Why?")
        question_run = await own(session, m.QuestionRun, job_id=job.id, question_id=question.id,
                                 retrieval_k=1, rerank_k=1)
        await own(session, m.RetrievalHit, question_run_id=question_run.id, chunk_id=chunk.id,
                  retrieval_rank=1)
        await own(session, m.Report, job_id=job.id, object_key=report_key)
        task = await own(session, m.WorkerTask, job_id=job.id, task_key=f"task:{job.id}", subject="test")
        await own(session, m.WorkerDeadLetter, job_id=job.id, worker_task_id=task.id,
                  task_key=task.task_key, subject="test", reason="test", error_message="failed")
        await own(session, m.JobStep, job_id=job.id, step_name="report")
        await own(session, m.JobEvent, job_id=job.id, owner_user_id=owner.id,
                  event_type="test", message="Event")
        await own(session, m.WebSocketTicket, job_id=job.id, owner_user_id=owner.id,
                  token_hash=uuid.uuid4().hex, expires_at=NOW)
        question_set = await add(session, m.QuestionSet, owner_user_id=owner.id, name="Retained")
        schedule = await add(session, m.TelegramReportSchedule, owner_user_id=owner.id,
                             telegram_chat_id=chat.id, question_set_id=question_set.id,
                             run_time_local="12:00", timezone="UTC", rolling_window_days=1,
                             last_job_id=job.id)
        sync_run = await add(session, m.TelegramSyncRun, owner_user_id=owner.id, chat_id=chat.id,
                             job_id=job.id, requested_start=NOW - timedelta(days=1), requested_end=NOW)
        await session.commit()
    storage.keys.update([private_key, shared_key, report_key, orphan_key, upload.object_key])
    return SimpleNamespace(job=job, sibling=sibling, upload=upload, owned=owned,
                           schedule=schedule, sync_run=sync_run, shared_key=shared_key,
                           job_keys={private_key, report_key, orphan_key}, sibling_media=sibling_media,
                           shared=[chat, collected_message, collected_media, collected_analysis,
                                   collected_transcript, question_set])


@pytest.mark.parametrize("status", [m.JobStatus.completed, m.JobStatus.failed, m.JobStatus.cancelled])
def test_only_owner_can_request_eligible_job_deletion_idempotently(monkeypatch, status):
    async def scenario():
        async with database(monkeypatch) as (factory, owner, storage):
            async with factory() as session:
                job = await add(session, m.Job, owner_user_id=owner.id, status=status)
                await session.commit()
            async with factory() as session:
                with pytest.raises(HTTPException) as exc:
                    await job_cleanup.request_job_deletion(session, job.id, uuid.uuid4())
                assert exc.value.status_code == 404
            first = await request(factory, job, owner)
            second = await request(factory, job, owner)
            assert first.deletion_requested_at == second.deletion_requested_at == NOW
            assert storage.removed == []
            assert storage.vector_jobs == []
    asyncio.run(scenario())


@pytest.mark.parametrize("status", [m.JobStatus.queued, m.JobStatus.running, m.JobStatus.cancelling])
def test_active_jobs_cannot_be_deleted(monkeypatch, status):
    async def scenario():
        async with database(monkeypatch) as (factory, owner, storage):
            async with factory() as session:
                job = await add(session, m.Job, owner_user_id=owner.id, status=status)
                await session.commit()
                with pytest.raises(HTTPException) as exc:
                    await job_cleanup.request_job_deletion(session, job.id, owner.id)
                assert exc.value.status_code == 409
                assert job.deletion_requested_at is None
    asyncio.run(scenario())


def test_cleanup_deletes_job_graph_and_preserves_shared_data_until_last_archive_use(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as (factory, owner, storage):
            graph = await seed_graph(factory, owner, storage)
            await request(factory, graph.job, owner)
            assert await job_cleanup.cleanup_job(graph.job.id)
            assert await job_cleanup.cleanup_job(graph.job.id)  # already removed
            async with factory() as session:
                for model, row_id in graph.owned:
                    assert await session.get(model, row_id) is None, model.__name__
                for row in [*graph.shared, graph.sibling, graph.sibling_media]:
                    assert await session.get(type(row), row.id) is not None
                assert (await session.get(m.TelegramReportSchedule, graph.schedule.id)).last_job_id is None
                assert (await session.get(m.TelegramSyncRun, graph.sync_run.id)).job_id is None
                assert (await session.get(m.Upload, graph.upload.id)).deletion_requested_at is None
            assert set(storage.removed) == graph.job_keys
            assert storage.vector_jobs == [graph.job.id]
            assert graph.shared_key in storage.keys
            assert graph.upload.object_key in storage.keys

            await request(factory, graph.sibling, owner)
            assert await job_cleanup.cleanup_job(graph.sibling.id)
            async with factory() as session:
                assert (await session.get(m.Upload, graph.upload.id)).deletion_requested_at == NOW
            assert await job_cleanup.cleanup_upload(graph.upload.id)
            assert not await job_cleanup.cleanup_upload(graph.upload.id)
            async with factory() as session:
                assert await session.get(m.Upload, graph.upload.id) is None
                for row in graph.shared:
                    assert await session.get(type(row), row.id) is not None
            assert graph.upload.object_key not in storage.keys
            assert graph.shared_key in storage.keys
    asyncio.run(scenario())


@pytest.mark.parametrize("failure_stage", ["vectors", "objects", "interrupted"])
def test_external_cleanup_failure_or_interruption_keeps_database_for_retry(monkeypatch, failure_stage):
    async def scenario():
        async with database(monkeypatch) as (factory, owner, storage):
            graph = await seed_graph(factory, owner, storage)
            await request(factory, graph.job, owner)
            if failure_stage == "vectors":
                storage.fail_vectors = True
            else:
                storage.fail_removal_at = 2
                if failure_stage == "interrupted":
                    storage.removal_error = asyncio.CancelledError()
            if failure_stage == "interrupted":
                with pytest.raises(asyncio.CancelledError):
                    await job_cleanup.cleanup_job(graph.job.id)
            else:
                assert not await job_cleanup.cleanup_job(graph.job.id)
            async with factory() as session:
                job = await session.get(m.Job, graph.job.id)
                assert job.deletion_requested_at == NOW
                if failure_stage != "interrupted":
                    assert "storage unavailable" in job.cleanup_error
                for model, row_id in graph.owned:
                    assert await session.get(model, row_id) is not None, model.__name__
                assert (await session.get(m.TelegramSyncRun, graph.sync_run.id)).job_id == graph.job.id
            assert len(storage.removed) == (0 if failure_stage == "vectors" else 1)
            storage.fail_vectors = False
            storage.fail_removal_at = None
            # A new cleanup session after the failed/cancelled attempt recovers the durable intent.
            assert await job_cleanup.cleanup_job(graph.job.id)
            async with factory() as session:
                for model, row_id in graph.owned:
                    assert await session.get(model, row_id) is None, model.__name__
            assert storage.vector_jobs == [graph.job.id, graph.job.id]
            assert set(storage.removed) == graph.job_keys
            assert graph.shared_key in storage.keys
    asyncio.run(scenario())


def test_cleanup_waits_for_worker_claim_and_upload_row_lock(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as (factory, owner, storage):
            async with factory() as session:
                job = await add(session, m.Job, owner_user_id=owner.id, status=m.JobStatus.cancelled)
                upload = await add(session, m.Upload, owner_user_id=owner.id, filename="expired.zip",
                                   size_bytes=1, object_key=f"users/{owner.id}/uploads/expired.zip",
                                   deletion_requested_at=NOW)
                await session.commit()
            await request(factory, job, owner)
            async with worker_control.claim_worker_task(f"stopping:{job.id}", job_id=job.id) as claim:
                assert claim is not None
                assert not await job_cleanup.cleanup_job(job.id)
                assert storage.vector_jobs == []
            assert await job_cleanup.cleanup_job(job.id)
            storage.keys.add(upload.object_key)
            async with factory() as writer:
                await writer.scalar(select(m.Upload).where(m.Upload.id == upload.id).with_for_update())
                assert not await job_cleanup.cleanup_upload(upload.id)
                assert storage.removed == []
                await writer.rollback()
            storage.fail_removal_at = 1
            assert not await job_cleanup.cleanup_upload(upload.id)
            async with factory() as session:
                persisted = await session.get(m.Upload, upload.id)
                assert persisted.deletion_requested_at == NOW
                assert "object storage unavailable" in persisted.cleanup_error
            storage.fail_removal_at = None
            assert await job_cleanup.cleanup_upload(upload.id)
            assert storage.removed == [upload.object_key]
    asyncio.run(scenario())


def test_retention_preview_is_readonly_and_cleanup_respects_cutoffs_references_and_transfer_lock(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as (factory, owner, storage):
            job_cutoff = NOW - timedelta(days=7)
            upload_cutoff = NOW - timedelta(days=3)
            abandoned_cutoff = NOW - timedelta(hours=24)
            expected_jobs, expected_uploads, excluded = set(), set(), []
            async with factory() as session:
                stored_owner = await session.get(m.User, owner.id)
                stored_owner.job_retention_days = 7
                stored_owner.upload_retention_days = 3
                for status, completed_at, created_at, eligible in [
                    (m.JobStatus.completed, job_cutoff, NOW, True),
                    (m.JobStatus.failed, None, job_cutoff, True),
                    (m.JobStatus.cancelled, job_cutoff - timedelta(seconds=1), NOW, True),
                    (m.JobStatus.completed, job_cutoff + timedelta(microseconds=1), job_cutoff, False),
                    (m.JobStatus.running, job_cutoff, job_cutoff, False),
                ]:
                    row = await add(session, m.Job, owner_user_id=owner.id, status=status,
                                    completed_at=completed_at, created_at=created_at)
                    if eligible:
                        expected_jobs.add(row.id)
                    else:
                        excluded.append(row)
                for status, created_at, completed_at, eligible in [
                    (m.UploadStatus.created, abandoned_cutoff, None, True),
                    (m.UploadStatus.rejected, abandoned_cutoff, None, True),
                    (m.UploadStatus.uploading, abandoned_cutoff, None, True),
                    (m.UploadStatus.created, abandoned_cutoff + timedelta(microseconds=1), None, False),
                    (m.UploadStatus.uploaded, upload_cutoff, upload_cutoff, True),
                    (m.UploadStatus.uploaded, upload_cutoff, upload_cutoff + timedelta(microseconds=1), False),
                ]:
                    row = await add(session, m.Upload, owner_user_id=owner.id, status=status,
                                    filename="archive.zip", size_bytes=1,
                                    object_key=f"users/{owner.id}/uploads/{uuid.uuid4()}",
                                    created_at=created_at, completed_at=completed_at)
                    if eligible:
                        expected_uploads.add(row.id)
                    else:
                        excluded.append(row)
                    if status == m.UploadStatus.uploading:
                        active_transfer = row
                in_use = await add(session, m.Upload, owner_user_id=owner.id, filename="in-use.zip",
                                   size_bytes=1, object_key=f"users/{owner.id}/uploads/in-use",
                                   status=m.UploadStatus.uploaded, completed_at=upload_cutoff)
                excluded.append(in_use)
                excluded.append(await add(session, m.Job, owner_user_id=owner.id, upload_id=in_use.id,
                                          status=m.JobStatus.running))
                pending_job = await add(session, m.Job, owner_user_id=owner.id, status=m.JobStatus.failed,
                                        deletion_requested_at=NOW, completed_at=job_cutoff)
                pending_upload = await add(session, m.Upload, owner_user_id=owner.id,
                                           filename="pending.zip", size_bytes=1,
                                           object_key=f"users/{owner.id}/uploads/pending",
                                           deletion_requested_at=NOW, created_at=abandoned_cutoff)
                await session.commit()

            async with factory() as session:
                preview = await job_cleanup.retention_preview(session, owner, 7, 3)
                assert {row["id"] for row in preview["jobs"]} == expected_jobs
                assert {row["id"] for row in preview["uploads"]} == expected_uploads
                assert preview["job_count"] == len(expected_jobs)
                assert preview["upload_count"] == len(expected_uploads)
                assert preview["pending_deletions"] == 2
                assert session.dirty == set()
                disabled = await job_cleanup.retention_preview(session, owner, None, None)
                assert disabled["job_count"] == 0
                assert disabled["upload_count"] == 3  # abandoned uploads expire without retention
                await session.commit()
            async with factory() as session:
                assert (await session.get(m.User, owner.id)).job_retention_days == 7
                for row_id in expected_jobs:
                    assert (await session.get(m.Job, row_id)).deletion_requested_at is None
                for row_id in expected_uploads:
                    assert (await session.get(m.Upload, row_id)).deletion_requested_at is None

            async with factory() as writer:
                await writer.scalar(select(m.Upload).where(
                    m.Upload.id == active_transfer.id,
                ).with_for_update())
                async with factory() as cleaner:
                    await job_cleanup.queue_expired_resources(cleaner)
                async with factory() as session:
                    for row_id in expected_jobs:
                        assert (await session.get(m.Job, row_id)).deletion_requested_at == NOW
                    for row_id in expected_uploads - {active_transfer.id}:
                        assert (await session.get(m.Upload, row_id)).deletion_requested_at == NOW
                    assert (await session.get(m.Upload, active_transfer.id)).deletion_requested_at is None
                    for row in excluded:
                        assert (await session.get(type(row), row.id)).deletion_requested_at is None
                    assert (await session.get(m.Job, pending_job.id)).deletion_requested_at == NOW
                    assert (await session.get(m.Upload, pending_upload.id)).deletion_requested_at == NOW
                await writer.rollback()
            async with factory() as session:
                await job_cleanup.queue_expired_resources(session)
            async with factory() as session:
                assert (await session.get(m.Upload, active_transfer.id)).deletion_requested_at == NOW
            assert storage.removed == storage.vector_jobs == []
    asyncio.run(scenario())

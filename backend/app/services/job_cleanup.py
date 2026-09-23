"""Durable deletion intents; external deletes complete before database rows disappear."""
import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    CollectedTelegramMedia, Job, JobEvent, JobStatus, JobStep, MediaAnalysis,
    MediaTranscript, MediaTranscriptTranslation, MessageChunk, MessageTranslation,
    Question, QuestionRun, Report, RetrievalHit, TelegramMedia, TelegramMessage,
    TelegramReportSchedule, TelegramSyncRun, Upload, UploadStatus, User,
    WebSocketTicket, WorkerDeadLetter, WorkerTask,
)
from app.services.minio_store import list_objects, remove_object
from app.services.qdrant_index import QdrantIndex
from app.services.worker_control import claim_job_lifecycle

settings = get_settings()
logger = logging.getLogger(__name__)
ELIGIBLE_JOB_STATUSES = (JobStatus.completed, JobStatus.failed, JobStatus.cancelled)


def utc_now():
    return datetime.now(timezone.utc)


def expired_jobs(owner_id, days, now):
    return select(Job).where(
        Job.owner_user_id == owner_id,
        Job.deletion_requested_at.is_(None),
        Job.status.in_(ELIGIBLE_JOB_STATUSES),
        func.coalesce(Job.completed_at, Job.created_at) <= now - timedelta(days=days or 36501),
        days is not None,
    )


def expired_uploads(owner_id, days, now):
    abandoned = (
        Upload.status.in_([UploadStatus.created, UploadStatus.uploading, UploadStatus.rejected])
        & (Upload.created_at <= now - timedelta(hours=settings.upload_expiry_hours))
    )
    unused = (
        (Upload.status == UploadStatus.uploaded)
        & (func.coalesce(Upload.completed_at, Upload.created_at) <= now - timedelta(days=days or 36501))
        & (days is not None)
    )
    return select(Upload).where(
        Upload.owner_user_id == owner_id,
        Upload.deletion_requested_at.is_(None),
        ~exists().where(Job.upload_id == Upload.id),
        or_(abandoned, unused),
    )


async def retention_preview(session, user, job_days, upload_days):
    now = utc_now()
    jobs_query = expired_jobs(user.id, job_days, now)
    uploads_query = expired_uploads(user.id, upload_days, now)
    jobs = (await session.scalars(jobs_query.order_by(Job.created_at).limit(200))).all()
    uploads = (await session.scalars(uploads_query.order_by(Upload.created_at).limit(200))).all()
    job_count = await session.scalar(select(func.count()).select_from(jobs_query.subquery()))
    upload_count = await session.scalar(select(func.count()).select_from(uploads_query.subquery()))
    pending = await session.scalar(select(func.count()).select_from(Job).where(
        Job.owner_user_id == user.id, Job.deletion_requested_at.is_not(None),
    ))
    pending += await session.scalar(select(func.count()).select_from(Upload).where(
        Upload.owner_user_id == user.id, Upload.deletion_requested_at.is_not(None),
    ))
    return {
        "job_retention_days": job_days, "upload_retention_days": upload_days,
        "upload_expiry_hours": settings.upload_expiry_hours,
        "jobs": [{"id": j.id, "status": j.status.value, "source_name": j.source_name,
                  "completed_at": j.completed_at} for j in jobs],
        "uploads": [{"id": u.id, "filename": u.filename, "created_at": u.created_at} for u in uploads],
        "job_count": job_count, "upload_count": upload_count,
        "pending_deletions": pending,
    }


async def request_job_deletion(session: AsyncSession, job_id, owner_id):
    job = await session.scalar(select(Job).where(
        Job.id == job_id, Job.owner_user_id == owner_id,
    ).with_for_update().execution_options(populate_existing=True))
    if job is None:
        raise HTTPException(404, "Resource not found")
    if job.deletion_requested_at is None:
        if job.status not in ELIGIBLE_JOB_STATUSES:
            raise HTTPException(409, "Stop the job before deleting it")
        job.deletion_requested_at = utc_now()
        job.cleanup_error = None
    await session.flush()
    return job


async def queue_expired_resources(session: AsyncSession):
    # Lock each policy while scheduling so a concurrent policy change has a
    # precise boundary: already queued deletions remain durable requests.
    users = (await session.scalars(select(User).with_for_update(skip_locked=True))).all()
    now = utc_now()
    for user in users:
        jobs = (await session.scalars(expired_jobs(user.id, user.job_retention_days, now)
                .order_by(Job.created_at).limit(100).with_for_update(skip_locked=True))).all()
        uploads = (await session.scalars(expired_uploads(user.id, user.upload_retention_days, now)
                   .order_by(Upload.created_at).limit(100).with_for_update(skip_locked=True))).all()
        for row in [*jobs, *uploads]:
            row.deletion_requested_at = now
            row.cleanup_error = None
    await session.commit()


async def _delete_job_rows(session, job_id):
    # These are shared collection/schedule records, not job-owned history.
    await session.execute(update(TelegramSyncRun).where(TelegramSyncRun.job_id == job_id).values(job_id=None))
    await session.execute(update(TelegramReportSchedule).where(
        TelegramReportSchedule.last_job_id == job_id,
    ).values(last_job_id=None))
    await session.execute(delete(RetrievalHit).where(or_(
        RetrievalHit.question_run_id.in_(select(QuestionRun.id).where(QuestionRun.job_id == job_id)),
        RetrievalHit.chunk_id.in_(select(MessageChunk.id).where(MessageChunk.job_id == job_id)),
    )))
    await session.execute(delete(MediaAnalysis).where(
        MediaAnalysis.media_id.in_(select(TelegramMedia.id).where(TelegramMedia.job_id == job_id)),
    ))
    for model in (
        MediaTranscriptTranslation, MediaTranscript, MessageTranslation, TelegramMedia,
        TelegramMessage, QuestionRun, Question, MessageChunk, Report, WorkerDeadLetter,
        WorkerTask, JobStep, JobEvent, WebSocketTicket,
    ):
        await session.execute(delete(model).where(model.job_id == job_id))
    await session.execute(delete(Job).where(Job.id == job_id))


async def _remove_job_objects(session, job):
    prefix = f"users/{job.owner_user_id}/jobs/{job.id}/"
    keys = set(await asyncio.to_thread(lambda: [o.object_name for o in list_objects(prefix)]))
    keys.update((await session.scalars(select(Report.object_key).where(Report.job_id == job.id))).all())
    keys.update((await session.scalars(select(TelegramMedia.minio_object_key).where(
        TelegramMedia.job_id == job.id, TelegramMedia.minio_object_key.is_not(None),
        TelegramMedia.source_media_id.is_(None),
    ))).all())
    # Only objects inside this owner's job namespace belong to this deletion.
    # Shared collection media stays owned by its collection even with no snapshots.
    keys = {key for key in keys if key.startswith(prefix)}
    protected = set()
    # Chunk queries to bound parameter counts for large extracted archives.
    key_list = sorted(keys)
    for start in range(0, len(key_list), 500):
        batch = key_list[start:start + 500]
        for column, extra in (
            (CollectedTelegramMedia.minio_object_key, True),
            (TelegramMedia.minio_object_key, TelegramMedia.job_id != job.id),
            (Report.object_key, Report.job_id != job.id),
            (Upload.object_key, True),
        ):
            protected.update((await session.scalars(select(column).where(column.in_(batch), extra))).all())
    for key in keys - protected:
        await asyncio.to_thread(remove_object, key)


async def cleanup_job(job_id: uuid.UUID) -> bool:
    async with claim_job_lifecycle(job_id) as connection:
        if connection is None:
            return False  # A worker is still stopping, or another cleaner owns it.
        async with SessionLocal(bind=connection) as session:
            job = await session.get(Job, job_id)
            if job is None:
                return True
            if job.deletion_requested_at is None:
                return False
            try:
                # No rows are removed until both external stores confirm deletion.
                await QdrantIndex().delete_job_points(job_id)
                await _remove_job_objects(session, job)
                upload_id = job.upload_id
                await _delete_job_rows(session, job_id)
                if upload_id:
                    # Original archives are shared when multiple jobs use them.
                    upload = await session.scalar(select(Upload).where(Upload.id == upload_id).with_for_update())
                    if upload and not await session.scalar(select(exists().where(Job.upload_id == upload_id))):
                        upload.deletion_requested_at = utc_now()
                await session.commit()
                return True
            except Exception as exc:
                await session.rollback()
                await session.execute(update(Job).where(Job.id == job_id).values(cleanup_error=str(exc)[:2000]))
                await session.commit()
                logger.warning("Job cleanup will retry: %s (%s)", job_id, type(exc).__name__)
                return False


async def cleanup_upload(upload_id: uuid.UUID) -> bool:
    async with SessionLocal() as session:
        # Writers and job creation take this row lock too. SKIP LOCKED leaves
        # in-progress transfers and another cleaner alone without delaying jobs.
        upload = await session.scalar(select(Upload).where(
            Upload.id == upload_id, Upload.deletion_requested_at.is_not(None),
        ).with_for_update(skip_locked=True))
        if upload is None:
            return False
        if await session.scalar(select(exists().where(Job.upload_id == upload_id))):
            return False
        try:
            await asyncio.to_thread(remove_object, upload.object_key)
            await session.delete(upload)
            await session.commit()
            return True
        except Exception as exc:
            await session.rollback()
            await session.execute(update(Upload).where(Upload.id == upload_id).values(cleanup_error=str(exc)[:2000]))
            await session.commit()
            logger.warning("Upload cleanup will retry: %s (%s)", upload_id, type(exc).__name__)
            return False


async def run_cleanup_cycle():
    async with SessionLocal() as session:
        await queue_expired_resources(session)
        job_ids = (await session.scalars(select(Job.id).where(Job.deletion_requested_at.is_not(None))
                   .order_by(Job.deletion_requested_at).limit(100))).all()
    for job_id in job_ids:
        await cleanup_job(job_id)
    async with SessionLocal() as session:
        upload_ids = (await session.scalars(select(Upload.id).where(Upload.deletion_requested_at.is_not(None))
                      .order_by(Upload.deletion_requested_at).limit(100))).all()
    for upload_id in upload_ids:
        await cleanup_upload(upload_id)


async def run_cleanup_loop():
    while True:
        try:
            await run_cleanup_cycle()
        except Exception:
            logger.exception("Retention cleanup cycle failed; retrying next interval")
        await asyncio.sleep(settings.cleanup_interval_seconds)

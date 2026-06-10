
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job, JobSourceType, JobStatus
from app.nats_client import publish_json
from app.services.events import record_event
from app.services.jobs import initial_task_payload
from app.workers import subjects

settings = get_settings()
logger = logging.getLogger(__name__)


async def recover_stale_queued_jobs(session: AsyncSession, js) -> list[dict[str, Any]]:
    """Republish the initial task for old queued jobs that never started.

    This covers the narrow failure window where the job was committed but the
    first JetStream publish was interrupted, or older builds created a queued job
    while NATS had no task message. Only jobs with started_at IS NULL are touched;
    once a worker starts validation it sets started_at and owns the lifecycle.
    """
    if not settings.recover_stale_queued_jobs:
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.stale_queued_job_after_seconds)
    rows = (
        await session.execute(
            select(Job)
            .where(
                Job.status == JobStatus.queued,
                Job.started_at.is_(None),
                Job.created_at <= cutoff,
            )
            .order_by(Job.created_at)
            .limit(settings.stale_queued_job_recovery_limit)
        )
    ).scalars().all()

    recovered: list[dict[str, Any]] = []
    for job in rows:
        payload = initial_task_payload(job)
        subject = (
            subjects.TELEGRAM_SNAPSHOT
            if job.source_type == JobSourceType.telegram_chat
            else subjects.VALIDATE
        )
        logger.warning("Republishing stale queued job %s to %s", job.id, subject)
        ack = await publish_json(js, subject, payload)
        await record_event(
            session,
            js=js,
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            event_type="job.requeued",
            level="warning",
            message="Analyse wurde erneut eingeplant, weil der Job in queued ohne Verarbeitung festhing.",
            payload={
                "subject": subject,
                "task_key": payload["task_key"],
                "cutoff_seconds": settings.stale_queued_job_after_seconds,
                "ack": str(ack),
            },
            raise_publish_errors=False,
        )
        recovered.append({"job_id": str(job.id), "subject": subject})

    if recovered:
        await session.commit()
    return recovered

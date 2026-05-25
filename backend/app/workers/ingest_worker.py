import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job, JobStatus, Upload
from app.services.minio_store import extracted_prefix, minio_client
from app.services.zip_ingest import ZipSecurityError, extract_zip_to_minio
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()


class ValidateWorker(Worker):
    subject = subjects.VALIDATE
    durable = "validate-worker"
    queue = "validate"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()

        job.status = JobStatus.running
        job.started_at = job.started_at or datetime.now(timezone.utc)
        await self.emit_event(session, job=job, event_type="zip.scan.started", message="ZIP-Sicherheitsprüfung gestartet")

        # The detailed central-directory validation runs in ExtractWorker after the
        # object has been spooled to a temporary file. This worker is still kept as
        # the explicit state transition where quota/capacity checks can be added.
        await self.emit_event(session, job=job, event_type="zip.scan.completed", message="ZIP-Sicherheitsprüfung vorgemerkt")

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="zip.scan.cancelled",
            message="ZIP-Sicherheitsprüfung wegen Job-Abbruch beendet",
        )

        await self.enqueue(
            subjects.EXTRACT,
            {
                "job_id": str(job.id),
                "owner_user_id": str(job.owner_user_id),
                "upload_id": str(job.upload_id),
                "task_key": f"extract:{job.id}",
            },
        )


class ExtractWorker(Worker):
    subject = subjects.EXTRACT
    durable = "extract-worker"
    queue = "extract"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        result = await session.execute(select(Job).where(Job.id == job_id))
        job = result.scalar_one()

        upload = (
            await session.execute(select(Upload).where(Upload.id == job.upload_id, Upload.owner_user_id == job.owner_user_id))
        ).scalar_one()

        await self.emit_event(session, job=job, event_type="zip.extract.started", message="ZIP-Extraktion gestartet")

        prefix = extracted_prefix(job.owner_user_id, job.id)
        try:
            extraction = await asyncio.to_thread(
                extract_zip_to_minio,
                client=minio_client(),
                bucket=settings.minio_bucket,
                upload_object_key=upload.object_key,
                extracted_prefix=prefix,
                settings=settings,
            )
        except ZipSecurityError as exc:
            job.status = JobStatus.failed
            job.error_message = f"ZIP rejected: {exc}"
            job.completed_at = datetime.now(timezone.utc)
            await self.emit_event(
                session,
                job=job,
                event_type="zip.scan.failed",
                message=f"ZIP wurde abgelehnt: {exc}",
                level="error",
            )
            return

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="zip.extract.cancelled",
            message="ZIP-Extraktion wegen Job-Abbruch beendet",
            payload={"extracted_prefix": extraction.extracted_prefix},
        )

        if not extraction.result_json_object_key:
            job.status = JobStatus.failed
            job.error_message = "Telegram export result.json not found"
            job.completed_at = datetime.now(timezone.utc)
            await self.emit_event(
                session,
                job=job,
                event_type="telegram.parse.failed",
                message="Keine result.json im Telegram-Export gefunden",
                level="error",
                payload={"extracted_prefix": extraction.extracted_prefix},
            )
            return

        await self.emit_event(
            session,
            job=job,
            event_type="zip.extract.completed",
            message="ZIP-Extraktion abgeschlossen",
            payload={
                "files_total": extraction.files_total,
                "bytes_total": extraction.bytes_total,
                "extracted_prefix": extraction.extracted_prefix,
                "result_json_object_key": extraction.result_json_object_key,
            },
        )

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="zip.extract.cancelled",
            message="ZIP-Extraktion wegen Job-Abbruch beendet",
            payload={"extracted_prefix": extraction.extracted_prefix},
        )

        await self.enqueue(
            subjects.PARSE,
            {
                "job_id": str(job.id),
                "owner_user_id": str(job.owner_user_id),
                "upload_id": str(job.upload_id),
                "extracted_prefix": extraction.extracted_prefix,
                "result_json_object_key": extraction.result_json_object_key,
                "task_key": f"parse:{job.id}",
            },
        )

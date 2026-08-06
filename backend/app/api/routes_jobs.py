import asyncio
import logging
import uuid
from io import BytesIO

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.dependencies import get_current_user
from app.models import (
    Job,
    JobEvent,
    JobSourceType,
    JobStatus,
    Report,
    Upload,
    User,
    WorkerDeadLetter,
)
from app.nats_client import nats_context
from app.schemas import (
    EventResponse,
    JobCreateRequest,
    JobResponse,
    ScheduledReportJobMetadata,
    TelegramReportCreateRequest,
)
from app.services.access_control import get_owned_job_or_404, get_owned_report_or_404
from app.services.capacity import capacity_snapshot, ensure_accepting_jobs
from app.services.jobs import (
    create_job_record,
    create_telegram_job_record,
    mark_job_retry_enqueue_failed_db_only,
    mark_job_start_failed_db_only,
    prepare_job_retry,
    publish_initial_job_task,
    publish_retry_job_task,
    request_cancel,
)
from app.services.events import record_event
from app.services.worker_control import mark_job_cancelled
from app.services.minio_store import get_bytes, minio_client
from app.services.report_bundle import (
    ReportBundleConflictError,
    ReportBundleError,
    build_report_bundle,
    remove_temp_file,
)
from app.services.report_naming import (
    attachment_content_disposition,
    build_download_all_filename,
    build_report_filename,
    report_date_for_job,
    resolve_report_source_name,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)
settings = get_settings()


def _scheduled_report_metadata(job: Job) -> ScheduledReportJobMetadata | None:
    raw = (job.options or {}).get("scheduled_report")
    if not isinstance(raw, dict):
        return None
    try:
        return ScheduledReportJobMetadata(**raw)
    except Exception:
        logger.warning("Ignoring invalid scheduled_report metadata on job %s", job.id)
        return None


def _job_response(job: Job) -> JobResponse:
    return JobResponse(
        id=job.id,
        status=job.status.value,
        source_type=job.source_type.value,
        telegram_chat_id=job.telegram_chat_id,
        report_start_at=job.report_start_at,
        report_end_at=job.report_end_at,
        source_name=job.source_name,
        created_at=job.created_at,
        completed_at=job.completed_at,
        error_message=job.error_message,
        scheduled_report=_scheduled_report_metadata(job),
    )


@router.get("/capacity")
async def get_jobs_capacity(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await capacity_snapshot(session)


@router.post("", response_model=JobResponse)
async def post_job(
    payload: JobCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    await ensure_accepting_jobs(session)

    # Persist and commit the job before publishing the first JetStream task.
    # Otherwise a fast worker can consume the task before the job row is visible
    # and ack it as job_not_found, leaving the job stuck in queued forever.
    job = await create_job_record(session, user.id, payload)
    await session.commit()
    logger.info("Created analysis job %s for user %s", job.id, user.id)

    try:
        async with nats_context() as (_, js):
            await publish_initial_job_task(session, js, job)
            await session.commit()
    except Exception as exc:
        logger.exception("Failed to publish first task for job %s", job.id)
        await session.rollback()
        await mark_job_start_failed_db_only(session, job.id, exc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis job was created but could not be enqueued. Please retry.",
        ) from exc

    return _job_response(job)


@router.post("/telegram", response_model=JobResponse)
async def post_telegram_job(
    payload: TelegramReportCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    await ensure_accepting_jobs(session)
    job = await create_telegram_job_record(session, user.id, payload)
    await session.commit()
    try:
        async with nats_context() as (_, js):
            await publish_initial_job_task(session, js, job)
            await session.commit()
    except Exception as exc:
        await session.rollback()
        await mark_job_start_failed_db_only(session, job.id, exc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis job was created but could not be enqueued. Please retry.",
        ) from exc
    return _job_response(job)


@router.get("", response_model=list[JobResponse])
async def list_jobs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[JobResponse]:
    result = await session.execute(
        select(Job).where(Job.owner_user_id == user.id).order_by(desc(Job.created_at)).limit(50)
    )
    return [_job_response(job) for job in result.scalars().all()]


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = await get_owned_job_or_404(session, job_id=job_id, user=user)
    return _job_response(job)


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    job = await get_owned_job_or_404(session, job_id=job_id, user=user)
    await request_cancel(session, job)
    async with nats_context() as (_, js):
        await record_event(
            session,
            js=js,
            job_id=job.id,
            owner_user_id=job.owner_user_id,
            event_type="job.cancel.requested",
            level="warning",
            message="Job-Abbruch wurde angefordert",
            payload={"job_id": str(job.id)},
        )
        # Guarantees that WebSocket clients receive job.cancelled even when no
        # worker is active for this job. Active workers still observe the DB
        # status and stop before publishing subsequent pipeline subjects.
        await mark_job_cancelled(session, job, js=js)
    await session.commit()
    return {"ok": True, "status": job.status.value}


@router.post("/{job_id}/retry", response_model=JobResponse)
async def retry_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> JobResponse:
    job = await get_owned_job_or_404(session, job_id=job_id, user=user)
    retry_target = await prepare_job_retry(session, job)
    await session.commit()

    try:
        async with nats_context() as (_, js):
            await publish_retry_job_task(session, js, job, retry_target)
            await session.commit()
    except Exception as exc:
        logger.exception("Failed to publish retry task for job %s", job.id)
        await session.rollback()
        await mark_job_retry_enqueue_failed_db_only(session, job.id, retry_target, exc)
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Analysis retry could not be enqueued. Please retry again.",
        ) from exc

    return _job_response(job)


@router.get("/{job_id}/events", response_model=list[EventResponse])
async def get_job_events(
    job_id: uuid.UUID,
    after_id: int = 0,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[EventResponse]:
    await get_owned_job_or_404(session, job_id=job_id, user=user)

    events_result = await session.execute(
        select(JobEvent)
        .where(JobEvent.job_id == job_id, JobEvent.id > after_id, JobEvent.owner_user_id == user.id)
        .order_by(JobEvent.id)
        .limit(1000)
    )
    events = events_result.scalars().all()
    return [
        EventResponse(
            id=e.id,
            event_type=e.event_type,
            level=e.level,
            message=e.message,
            payload=e.payload,
            created_at=e.created_at,
        )
        for e in events
    ]


@router.get("/{job_id}/dead-letters")
async def get_job_dead_letters(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await get_owned_job_or_404(session, job_id=job_id, user=user)

    rows = (
        await session.execute(
            select(WorkerDeadLetter)
            .where(WorkerDeadLetter.job_id == job_id)
            .order_by(desc(WorkerDeadLetter.created_at))
            .limit(500)
        )
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            "task_key": row.task_key,
            "subject": row.subject,
            "attempts": row.attempts,
            "reason": row.reason,
            "error_message": row.error_message,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/{job_id}/report/download")
async def download_report(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    job, report = await get_owned_report_or_404(session, job_id=job_id, user=user)
    if job.status != JobStatus.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is not completed")

    data = await asyncio.to_thread(get_bytes, report.object_key)
    source_name = await resolve_report_source_name(session, job)
    filename = build_report_filename(
        source_name,
        report_date_for_job(job, report.created_at),
    )
    headers = {"Content-Disposition": attachment_content_disposition(filename)}
    return StreamingResponse(BytesIO(data), media_type="application/zip", headers=headers)


@router.get("/{job_id}/report/download-all")
async def download_all(
    job_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    job = await get_owned_job_or_404(session, job_id=job_id, user=user)
    if job.status != JobStatus.completed:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Job is not completed")
    if job.source_type != JobSourceType.upload or job.upload_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Download all is only available for uploaded ZIP jobs",
        )

    report = (
        await session.execute(select(Report).where(Report.job_id == job.id))
    ).scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not available")

    upload = (
        await session.execute(
            select(Upload).where(
                Upload.id == job.upload_id,
                Upload.owner_user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if upload is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original upload not available")

    try:
        bundle_path = await asyncio.to_thread(
            build_report_bundle,
            client=minio_client(),
            bucket=settings.minio_bucket,
            upload_object_key=upload.object_key,
            report_object_key=report.object_key,
        )
    except ReportBundleConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ReportBundleError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    background_tasks.add_task(remove_temp_file, bundle_path)
    filename = build_download_all_filename(upload.filename)
    headers = {"Content-Disposition": attachment_content_disposition(filename)}
    return FileResponse(
        bundle_path,
        media_type="application/zip",
        headers=headers,
        background=background_tasks,
    )

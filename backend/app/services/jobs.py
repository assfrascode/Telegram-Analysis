import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import (
    Job,
    JobSourceType,
    JobStatus,
    Question,
    QuestionSet,
    TelegramChat,
    TelegramChatStatus,
    Upload,
    UploadStatus,
)
from app.nats_client import publish_json
from app.schemas import JobCreateRequest, QuestionInput, TelegramReportCreateRequest
from app.services.events import record_event, record_event_db_only
from app.services.question_sets import question_inputs_from_set, question_set_snapshot
from app.services.telegram_chat_access import ensure_chat_sync_source_available
from app.workers import subjects

settings = get_settings()
logger = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


async def _load_owned_upload(
    session: AsyncSession,
    *,
    upload_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> Upload:
    result = await session.execute(
        select(Upload).where(Upload.id == upload_id, Upload.owner_user_id == owner_user_id)
    )
    upload = result.scalar_one_or_none()
    if not upload:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload not found")
    if upload.status != UploadStatus.uploaded:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload not completed")
    return upload


async def _load_owned_question_set(
    session: AsyncSession,
    *,
    question_set_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> QuestionSet:
    result = await session.execute(
        select(QuestionSet)
        .options(selectinload(QuestionSet.items))
        .where(
            QuestionSet.id == question_set_id,
            QuestionSet.owner_user_id == owner_user_id,
            QuestionSet.archived_at.is_(None),
        )
    )
    question_set = result.scalar_one_or_none()
    if question_set is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question set not found")
    return question_set


async def _resolve_job_questions(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    payload: JobCreateRequest,
) -> tuple[list[QuestionInput], dict | None]:
    question_set_snapshot_data = None
    question_set = None
    if payload.question_set_id is not None:
        question_set = await _load_owned_question_set(
            session,
            question_set_id=payload.question_set_id,
            owner_user_id=owner_user_id,
        )
        question_set_snapshot_data = question_set_snapshot(question_set)

    if payload.questions:
        questions = payload.questions
    elif question_set is not None:
        questions = question_inputs_from_set(question_set)
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No questions provided")

    if not questions:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No questions provided")
    return questions, question_set_snapshot_data


async def _resolve_question_source(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    questions: list[QuestionInput] | None,
    question_set_id: uuid.UUID | None,
) -> tuple[list[QuestionInput], dict | None]:
    question_set_snapshot_data = None
    question_set = None
    if question_set_id is not None:
        question_set = await _load_owned_question_set(
            session,
            question_set_id=question_set_id,
            owner_user_id=owner_user_id,
        )
        question_set_snapshot_data = question_set_snapshot(question_set)
    resolved = questions or (question_inputs_from_set(question_set) if question_set else [])
    if not resolved:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No questions provided")
    return resolved, question_set_snapshot_data


def initial_task_payload(job: Job) -> dict[str, str]:
    payload = {
        "job_id": str(job.id),
        "owner_user_id": str(job.owner_user_id),
    }
    if job.source_type == JobSourceType.telegram_chat:
        payload["telegram_chat_id"] = str(job.telegram_chat_id)
        payload["task_key"] = f"telegram-snapshot:{job.id}"
    else:
        payload["upload_id"] = str(job.upload_id)
        payload["task_key"] = f"validate:{job.id}"
    return payload


async def create_job_record(session: AsyncSession, owner_user_id: uuid.UUID, payload: JobCreateRequest) -> Job:
    """Persist the job and its immutable question snapshot, but do not enqueue it.

    The caller must commit this transaction before publishing the first NATS task.
    Otherwise a worker can consume the task before the job row is visible and ack
    it as job_not_found, leaving the job stuck in queued with no pending NATS
    message.
    """
    upload = await _load_owned_upload(session, upload_id=payload.upload_id, owner_user_id=owner_user_id)
    questions, question_set_data = await _resolve_job_questions(
        session,
        owner_user_id=owner_user_id,
        payload=payload,
    )

    options = payload.options.model_dump()
    options.setdefault("retrieval_k", settings.default_retrieval_k)
    options.setdefault("rerank_k", settings.default_rerank_k)
    if question_set_data is not None:
        options["question_set"] = question_set_data

    job = Job(
        owner_user_id=owner_user_id,
        source_type=JobSourceType.upload,
        upload_id=upload.id,
        status=JobStatus.queued,
        options=options,
    )
    session.add(job)
    await session.flush()

    for idx, question_input in enumerate(questions, start=1):
        session.add(
            Question(
                job_id=job.id,
                question_index=idx,
                client_question_id=question_input.id,
                text=question_input.text,
            )
        )

    await session.flush()
    return job


async def create_telegram_job_record(
    session: AsyncSession,
    owner_user_id: uuid.UUID,
    payload: TelegramReportCreateRequest,
) -> Job:
    chat = (
        await session.execute(
            select(TelegramChat).where(
                TelegramChat.id == payload.telegram_chat_id,
                TelegramChat.owner_user_id == owner_user_id,
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Telegram chat not found")
    if chat.status == TelegramChatStatus.archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Telegram chat is archived")
    await ensure_chat_sync_source_available(session, chat)

    questions, question_set_data = await _resolve_question_source(
        session,
        owner_user_id=owner_user_id,
        questions=payload.questions,
        question_set_id=payload.question_set_id,
    )
    options = payload.options.model_dump()
    if question_set_data is not None:
        options["question_set"] = question_set_data
    job = Job(
        owner_user_id=owner_user_id,
        source_type=JobSourceType.telegram_chat,
        telegram_chat_id=chat.id,
        report_start_at=payload.start_at,
        report_end_at=payload.end_at,
        status=JobStatus.queued,
        options=options,
    )
    session.add(job)
    await session.flush()
    for idx, question_input in enumerate(questions, start=1):
        session.add(
            Question(
                job_id=job.id,
                question_index=idx,
                client_question_id=question_input.id,
                text=question_input.text,
            )
        )
    await session.flush()
    return job


async def publish_initial_job_task(session: AsyncSession, js, job: Job) -> None:
    """Publish the first task after the job has been committed to Postgres."""
    payload = initial_task_payload(job)
    subject = (
        subjects.TELEGRAM_SNAPSHOT
        if job.source_type == JobSourceType.telegram_chat
        else subjects.VALIDATE
    )
    logger.info("Publishing first task for job %s to %s", job.id, subject)
    ack = await publish_json(js, subject, payload)
    logger.info("Published first task for job %s to %s: %s", job.id, subject, ack)
    await record_event(
        session,
        js=js,
        job_id=job.id,
        owner_user_id=job.owner_user_id,
        event_type="job.queued",
        level="info",
        message="Analyse wurde eingeplant.",
        payload={"subject": subject, "task_key": payload["task_key"]},
        raise_publish_errors=False,
    )


async def mark_job_start_failed_db_only(session: AsyncSession, job_id: uuid.UUID, error: Exception) -> Job | None:
    job = await session.get(Job, job_id)
    if job is None:
        return None
    job.status = JobStatus.failed
    job.error_message = f"Analysis could not be started: {error}"
    job.completed_at = utc_now()
    await record_event_db_only(
        session,
        job_id=job.id,
        owner_user_id=job.owner_user_id,
        event_type="job.failed",
        level="error",
        message="Analyse konnte nicht gestartet werden. Die erste Queue-Nachricht wurde nicht veröffentlicht.",
        payload={"error": str(error), "stage": "initial_publish"},
    )
    await session.flush()
    return job


# Backward-compatible wrapper for older imports/tests. It deliberately commits
# the job before publishing so it cannot reproduce the queued-without-NATS race.
async def create_job(session: AsyncSession, js, owner_user_id: uuid.UUID, payload: JobCreateRequest) -> Job:
    job = await create_job_record(session, owner_user_id, payload)
    await session.commit()
    await publish_initial_job_task(session, js, job)
    await session.commit()
    return job


async def request_cancel(session: AsyncSession, job: Job) -> Job:
    if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
        return job
    job.status = JobStatus.cancelling
    await session.flush()
    return job

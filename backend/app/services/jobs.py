import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models import Job, JobStatus, Question, QuestionSet, Upload, UploadStatus
from app.nats_client import publish_json
from app.schemas import JobCreateRequest, QuestionInput
from app.services.question_sets import question_inputs_from_set, question_set_snapshot

settings = get_settings()


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


async def create_job(session: AsyncSession, js, owner_user_id: uuid.UUID, payload: JobCreateRequest) -> Job:
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

    job = Job(owner_user_id=owner_user_id, upload_id=upload.id, status=JobStatus.queued, options=options)
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

    await publish_json(
        js,
        "jobs.ingest.validate",
        {
            "job_id": str(job.id),
            "owner_user_id": str(owner_user_id),
            "upload_id": str(upload.id),
            "task_key": f"validate:{job.id}",
        },
    )
    return job


async def request_cancel(session: AsyncSession, job: Job) -> Job:
    if job.status in {JobStatus.completed, JobStatus.failed, JobStatus.cancelled}:
        return job
    job.status = JobStatus.cancelling
    await session.flush()
    return job


def utc_now():
    return datetime.now(timezone.utc)

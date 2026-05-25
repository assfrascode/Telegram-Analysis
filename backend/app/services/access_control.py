from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, QuestionSet, Report, Upload, User


_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")


async def get_owned_upload_or_404(
    session: AsyncSession,
    *,
    upload_id: uuid.UUID,
    user: User,
) -> Upload:
    """Return an upload only when it belongs to the authenticated user.

    Deliberately returns 404 rather than 403 for foreign resources so callers
    cannot use the API to enumerate object IDs across users.
    """
    result = await session.execute(
        select(Upload).where(Upload.id == upload_id, Upload.owner_user_id == user.id)
    )
    upload = result.scalar_one_or_none()
    if upload is None:
        raise _NOT_FOUND
    return upload


async def get_owned_job_or_404(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    user: User,
) -> Job:
    """Return a job only when it belongs to the authenticated user."""
    result = await session.execute(
        select(Job).where(Job.id == job_id, Job.owner_user_id == user.id)
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise _NOT_FOUND
    return job


async def get_owned_report_or_404(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    user: User,
) -> tuple[Job, Report]:
    """Return a report only through an owned job.

    Reports do not currently carry owner_user_id directly; the ownership chain is
    Report -> Job -> owner_user_id. Keep this helper as the single access path so
    future routes do not accidentally expose reports by object key or report ID.
    """
    job = await get_owned_job_or_404(session, job_id=job_id, user=user)
    result = await session.execute(select(Report).where(Report.job_id == job.id))
    report = result.scalar_one_or_none()
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not available")
    return job, report


async def get_owned_question_set_or_404(
    session: AsyncSession,
    *,
    question_set_id: uuid.UUID,
    user: User,
) -> QuestionSet:
    """Return a saved question set only when it belongs to the authenticated user."""
    result = await session.execute(
        select(QuestionSet).where(
            QuestionSet.id == question_set_id,
            QuestionSet.owner_user_id == user.id,
            QuestionSet.archived_at.is_(None),
        )
    )
    question_set = result.scalar_one_or_none()
    if question_set is None:
        raise _NOT_FOUND
    return question_set

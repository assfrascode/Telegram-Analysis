import uuid
from collections.abc import Collection
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Job, JobStatus, JobStep, StepStatus
from app.workers import subjects

PipelineTask = tuple[str, str]
MEDIA_DESCRIPTION_STEP = "media"
MEDIA_TRANSCRIPTION_STEP = "transcribe"
MEDIA_JOIN_STEP = "media-join"
MEDIA_BRANCH_STEPS = frozenset({MEDIA_DESCRIPTION_STEP, MEDIA_TRANSCRIPTION_STEP})
MEDIA_BARRIER_STEPS = (*MEDIA_BRANCH_STEPS, MEDIA_JOIN_STEP)


def next_tasks_after_messages(job: Job) -> tuple[PipelineTask, ...]:
    if (job.options or {}).get("translate", False):
        return ((subjects.MESSAGES_TRANSLATE, "translate"),)
    return next_tasks_after_translation(job)


def next_tasks_after_translation(job: Job) -> tuple[PipelineTask, ...]:
    if (job.options or {}).get("analyze_media", True):
        return (
            (subjects.MEDIA_DESCRIBE, MEDIA_DESCRIPTION_STEP),
            (subjects.MEDIA_TRANSCRIBE, MEDIA_TRANSCRIPTION_STEP),
        )
    return ((subjects.CHUNK_CREATE, "chunk"),)


def next_task_for_completed_media_steps(completed_steps: Collection[str]) -> PipelineTask | None:
    completed = set(completed_steps)
    if MEDIA_JOIN_STEP in completed or not MEDIA_BRANCH_STEPS.issubset(completed):
        return None
    return subjects.CHUNK_CREATE, "chunk"


def _mark_step_completed(
    session: AsyncSession,
    steps: dict[str, JobStep],
    *,
    job_id: uuid.UUID,
    step_name: str,
) -> None:
    now = datetime.now(timezone.utc)
    step = steps.get(step_name)
    if step is None:
        step = JobStep(
            job_id=job_id,
            step_name=step_name,
            status=StepStatus.completed,
            total=1,
            done=1,
            updated_at=now,
        )
        session.add(step)
        steps[step_name] = step
        return

    step.status = StepStatus.completed
    step.total = 1
    step.done = 1
    step.error_message = None
    step.updated_at = now


async def complete_media_branch(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    step_name: str,
) -> PipelineTask | None:
    if step_name not in MEDIA_BRANCH_STEPS:
        raise ValueError(f"Unknown media branch step: {step_name}")

    job = (
        await session.execute(
            select(Job)
            .where(Job.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if job.status in {
        JobStatus.cancelling,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.completed,
    }:
        return None

    rows = (
        await session.execute(
            select(JobStep).where(
                JobStep.job_id == job_id,
                JobStep.step_name.in_(MEDIA_BARRIER_STEPS),
            )
        )
    ).scalars().all()
    steps = {row.step_name: row for row in rows}
    _mark_step_completed(session, steps, job_id=job_id, step_name=step_name)

    completed_steps = {
        name for name, step in steps.items() if step.status == StepStatus.completed
    }
    next_task = next_task_for_completed_media_steps(completed_steps)
    if next_task is not None:
        _mark_step_completed(session, steps, job_id=job_id, step_name=MEDIA_JOIN_STEP)

    await session.flush()
    return next_task

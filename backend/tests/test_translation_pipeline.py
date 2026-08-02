import asyncio
import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import Job, JobStatus, JobStep, StepStatus
from app.workers import subjects
from app.workers.pipeline import (
    MEDIA_DESCRIPTION_STEP,
    MEDIA_JOIN_STEP,
    MEDIA_TRANSCRIPTION_STEP,
    _mark_step_completed,
    complete_media_branch,
    next_task_for_completed_media_steps,
    next_tasks_after_messages,
    next_tasks_after_translation,
)


def _job(options: dict) -> Job:
    return Job(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), options=options)


def test_messages_route_to_translation_when_enabled() -> None:
    assert next_tasks_after_messages(_job({"translate": True})) == (
        (subjects.MESSAGES_TRANSLATE, "translate"),
    )


def test_messages_fan_out_to_media_tasks_when_translation_disabled() -> None:
    assert next_tasks_after_messages(_job({"translate": False, "analyze_media": True})) == (
        (subjects.MEDIA_DESCRIBE, MEDIA_DESCRIPTION_STEP),
        (subjects.MEDIA_TRANSCRIBE, MEDIA_TRANSCRIPTION_STEP),
    )
    assert next_tasks_after_messages(_job({"translate": False, "analyze_media": False})) == (
        (subjects.CHUNK_CREATE, "chunk"),
    )


def test_translation_fans_out_to_media_tasks_or_routes_to_chunk() -> None:
    assert next_tasks_after_translation(_job({"analyze_media": True})) == (
        (subjects.MEDIA_DESCRIBE, MEDIA_DESCRIPTION_STEP),
        (subjects.MEDIA_TRANSCRIBE, MEDIA_TRANSCRIPTION_STEP),
    )
    assert next_tasks_after_translation(_job({"analyze_media": False})) == (
        (subjects.CHUNK_CREATE, "chunk"),
    )


def test_media_barrier_waits_for_both_branches() -> None:
    assert next_task_for_completed_media_steps({MEDIA_DESCRIPTION_STEP}) is None
    assert next_task_for_completed_media_steps({MEDIA_TRANSCRIPTION_STEP}) is None
    assert next_task_for_completed_media_steps(
        {MEDIA_DESCRIPTION_STEP, MEDIA_TRANSCRIPTION_STEP}
    ) == (subjects.CHUNK_CREATE, "chunk")


def test_media_barrier_join_marker_makes_transition_idempotent() -> None:
    assert next_task_for_completed_media_steps(
        {MEDIA_DESCRIPTION_STEP, MEDIA_TRANSCRIPTION_STEP, MEDIA_JOIN_STEP}
    ) is None


def test_mark_step_completed_creates_and_updates_durable_step() -> None:
    job_id = uuid.uuid4()
    added: list[JobStep] = []
    session = SimpleNamespace(add=added.append)
    steps: dict[str, JobStep] = {}

    _mark_step_completed(
        session,
        steps,
        job_id=job_id,
        step_name=MEDIA_DESCRIPTION_STEP,
    )

    assert added == [steps[MEDIA_DESCRIPTION_STEP]]
    assert steps[MEDIA_DESCRIPTION_STEP].status == StepStatus.completed
    assert steps[MEDIA_DESCRIPTION_STEP].done == 1

    steps[MEDIA_DESCRIPTION_STEP].status = StepStatus.failed_retryable
    steps[MEDIA_DESCRIPTION_STEP].error_message = "retry"
    _mark_step_completed(
        session,
        steps,
        job_id=job_id,
        step_name=MEDIA_DESCRIPTION_STEP,
    )
    assert len(added) == 1
    assert steps[MEDIA_DESCRIPTION_STEP].status == StepStatus.completed
    assert steps[MEDIA_DESCRIPTION_STEP].error_message is None


class _ScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one(self):
        return self.value


class _StepResult:
    def __init__(self, steps: list[JobStep]) -> None:
        self.steps = steps

    def scalars(self):
        return self

    def all(self) -> list[JobStep]:
        return self.steps


class _BarrierSession:
    def __init__(self, job, steps: list[JobStep] | None = None) -> None:
        self.job = job
        self.steps = steps or []
        self.added: list[JobStep] = []
        self.execute_count = 0
        self.flushed = False

    async def execute(self, _query):
        self.execute_count += 1
        if self.execute_count == 1:
            return _ScalarResult(self.job)
        return _StepResult(self.steps)

    def add(self, value: JobStep) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flushed = True


def _completed_step(job_id: uuid.UUID, step_name: str) -> JobStep:
    return JobStep(
        job_id=job_id,
        step_name=step_name,
        status=StepStatus.completed,
        total=1,
        done=1,
    )


def test_complete_media_branch_opens_barrier_for_second_branch() -> None:
    job_id = uuid.uuid4()
    session = _BarrierSession(
        SimpleNamespace(status=JobStatus.running),
        [_completed_step(job_id, MEDIA_DESCRIPTION_STEP)],
    )

    next_task = asyncio.run(
        complete_media_branch(
            session,
            job_id=job_id,
            step_name=MEDIA_TRANSCRIPTION_STEP,
        )
    )

    assert next_task == (subjects.CHUNK_CREATE, "chunk")
    assert [step.step_name for step in session.added] == [
        MEDIA_TRANSCRIPTION_STEP,
        MEDIA_JOIN_STEP,
    ]
    assert session.flushed is True


def test_complete_media_branch_does_not_reopen_completed_join() -> None:
    job_id = uuid.uuid4()
    session = _BarrierSession(
        SimpleNamespace(status=JobStatus.running),
        [
            _completed_step(job_id, MEDIA_DESCRIPTION_STEP),
            _completed_step(job_id, MEDIA_TRANSCRIPTION_STEP),
            _completed_step(job_id, MEDIA_JOIN_STEP),
        ],
    )

    next_task = asyncio.run(
        complete_media_branch(
            session,
            job_id=job_id,
            step_name=MEDIA_DESCRIPTION_STEP,
        )
    )

    assert next_task is None
    assert session.added == []
    assert session.flushed is True


def test_complete_media_branch_does_not_open_terminal_job() -> None:
    for status in (
        JobStatus.cancelling,
        JobStatus.cancelled,
        JobStatus.failed,
        JobStatus.completed,
    ):
        session = _BarrierSession(SimpleNamespace(status=status))

        next_task = asyncio.run(
            complete_media_branch(
                session,
                job_id=uuid.uuid4(),
                step_name=MEDIA_DESCRIPTION_STEP,
            )
        )

        assert next_task is None
        assert session.execute_count == 1
        assert session.added == []
        assert session.flushed is False

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import JobSourceType, JobStatus, StepStatus
from app.services.jobs import (
    reset_job_for_retry,
    reset_worker_task_for_retry,
    retry_target_for_job,
)
from app.workers import subjects


def _job(**overrides):
    values = {
        "id": uuid.uuid4(),
        "owner_user_id": uuid.uuid4(),
        "source_type": JobSourceType.upload,
        "upload_id": uuid.uuid4(),
        "telegram_chat_id": None,
        "status": JobStatus.failed,
        "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "completed_at": datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        "error_message": "boom",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_retry_target_from_dead_letter_normalizes_payload_and_strips_stale_delay() -> None:
    job = _job()
    dead_letter = SimpleNamespace(
        id=uuid.uuid4(),
        subject=subjects.CHUNK_CREATE,
        task_key=f"chunk:{job.id}",
        reason="max_attempts_exceeded",
        payload={
            "job_id": "wrong",
            "owner_user_id": "wrong",
            "task_key": "wrong",
            "retry_delay_seconds": 60,
            "custom": "kept",
        },
    )

    target = retry_target_for_job(job, dead_letter)

    assert target.subject == subjects.CHUNK_CREATE
    assert target.task_key == f"chunk:{job.id}"
    assert target.payload["job_id"] == str(job.id)
    assert target.payload["owner_user_id"] == str(job.owner_user_id)
    assert target.payload["task_key"] == f"chunk:{job.id}"
    assert target.payload["custom"] == "kept"
    assert "retry_delay_seconds" not in target.payload
    assert target.dead_letter_id == dead_letter.id
    assert target.dead_letter_reason == "max_attempts_exceeded"


def test_retry_never_started_failed_job_without_dead_letter_uses_initial_task() -> None:
    job = _job(started_at=None)

    target = retry_target_for_job(job, None)

    assert target.subject == subjects.VALIDATE
    assert target.task_key == f"validate:{job.id}"
    assert target.payload["job_id"] == str(job.id)
    assert target.payload["owner_user_id"] == str(job.owner_user_id)
    assert target.payload["upload_id"] == str(job.upload_id)


def test_started_failed_job_without_dead_letter_is_not_retryable() -> None:
    job = _job()

    with pytest.raises(HTTPException) as exc:
        retry_target_for_job(job, None)

    assert exc.value.status_code == 409


def test_non_failed_jobs_are_rejected_for_retry() -> None:
    job = _job(status=JobStatus.completed)

    with pytest.raises(HTTPException) as exc:
        reset_job_for_retry(job)

    assert exc.value.status_code == 409


def test_retry_reset_marks_job_runnable_and_clears_failed_task() -> None:
    job = _job()
    task = SimpleNamespace(
        status=StepStatus.failed_permanent,
        attempts=3,
        last_error="boom",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    reset_job_for_retry(job)
    reset_worker_task_for_retry(task)

    assert job.status == JobStatus.running
    assert job.error_message is None
    assert job.completed_at is None
    assert task.status == StepStatus.pending
    assert task.attempts == 0
    assert task.last_error is None
    assert task.updated_at > datetime(2026, 1, 1, tzinfo=timezone.utc)

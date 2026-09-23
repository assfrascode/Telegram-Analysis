import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models import (
    Job, JobEvent, JobSourceType, JobStatus, Question, StepStatus,
    TelegramChatStatus, TelegramIngestMode, Upload, UploadStatus,
)
from app.services.jobs import (
    _load_owned_upload,
    initial_task_payload,
    prepare_job_retry,
    restart_cancelled_job,
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


class RestartSession:
    def __init__(self, *, source=None, questions=()):
        self.results = [source, list(questions)]
        self.added = []
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        result = self.results.pop(0)
        return SimpleNamespace(
            scalar_one_or_none=lambda: result,
            scalars=lambda: SimpleNamespace(all=lambda: result),
        )

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if value.id is None:
                value.id = len(self.added) if isinstance(value, JobEvent) else uuid.uuid4()


def _cancelled_job(**overrides):
    values = {
        "id": uuid.uuid4(),
        "owner_user_id": uuid.uuid4(),
        "source_type": JobSourceType.upload,
        "upload_id": uuid.uuid4(),
        "status": JobStatus.cancelled,
        "options": {"question_set": {"name": "Original questions"}, "translate": True},
        "completed_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "started_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "source_name": "Original chat",
    }
    values.update(overrides)
    return Job(**values)


def test_cancelled_restart_copies_inputs_to_fresh_run_and_keeps_original_cancelled():
    original = _cancelled_job()
    question = Question(
        id=uuid.uuid4(), job_id=original.id, question_index=3,
        client_question_id="original-question", text="What happened?",
    )
    upload = Upload(id=original.upload_id, status=UploadStatus.uploaded)
    session = RestartSession(source=upload, questions=[question])

    restarted = asyncio.run(restart_cancelled_job(session, original))

    assert restarted.id != original.id
    assert restarted.owner_user_id == original.owner_user_id
    assert restarted.upload_id == original.upload_id
    assert restarted.source_name == original.source_name
    assert restarted.status == JobStatus.queued
    assert restarted.started_at is None
    assert restarted.completed_at is None
    assert restarted.options == original.options
    assert restarted.options is not original.options
    assert restarted.options["question_set"] is not original.options["question_set"]
    assert original.status == JobStatus.cancelled
    assert original.completed_at is not None
    copied = next(value for value in session.added if isinstance(value, Question))
    assert copied.id != question.id
    assert copied.job_id == restarted.id
    assert copied.question_index == question.question_index
    assert copied.client_question_id == question.client_question_id
    assert copied.text == question.text
    assert initial_task_payload(original)["task_key"] != initial_task_payload(restarted)["task_key"]
    events = [value for value in session.added if isinstance(value, JobEvent)]
    assert [(event.job_id, event.payload) for event in events] == [
        (restarted.id, {"previous_job_id": str(original.id)}),
        (original.id, {"restarted_job_id": str(restarted.id)}),
    ]
    # Source reuse and expired-upload cleanup serialize on the same row.
    assert "FOR UPDATE" in str(session.statements[0])


def test_cancelled_telegram_restart_keeps_window_and_question_snapshot():
    metadata = {"schedule_id": str(uuid.uuid4()), "scheduled_for": "2026-01-02T00:00:00+00:00"}
    original = _cancelled_job(
        source_type=JobSourceType.telegram_chat, upload_id=None,
        telegram_chat_id=uuid.uuid4(),
        report_start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        report_end_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        options={"scheduled_report": metadata, "question_set": {"name": "Archived since"}},
    )
    chat = SimpleNamespace(
        status=TelegramChatStatus.active, ingest_mode=TelegramIngestMode.external_push,
    )
    question = Question(question_index=1, text="Original question")
    session = RestartSession(source=chat, questions=[question])

    restarted = asyncio.run(restart_cancelled_job(session, original))

    assert restarted.telegram_chat_id == original.telegram_chat_id
    assert restarted.report_start_at == original.report_start_at
    assert restarted.report_end_at == original.report_end_at
    assert restarted.options == original.options
    assert initial_task_payload(restarted)["task_key"] == f"telegram-snapshot:{restarted.id}"
    assert len(session.statements) == 2  # Chat + saved questions, no mutable question-set lookup.


@pytest.mark.parametrize("status", [JobStatus.running, JobStatus.cancelling, JobStatus.completed])
def test_restart_rejects_jobs_that_have_not_been_cancelled(status):
    session = RestartSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(restart_cancelled_job(session, _cancelled_job(status=status)))
    assert exc.value.status_code == 409
    assert session.added == []
    assert session.statements == []


@pytest.mark.parametrize("operation", [restart_cancelled_job, prepare_job_retry])
def test_restart_and_retry_reject_jobs_pending_deletion(operation):
    job = _cancelled_job(deletion_requested_at=datetime.now(timezone.utc))
    session = RestartSession()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(operation(session, job))
    assert exc.value.status_code == 409
    assert session.added == []
    assert session.statements == []


def test_restart_rejects_source_upload_pending_cleanup():
    original = _cancelled_job()
    session = RestartSession(source=Upload(
        id=original.upload_id, status=UploadStatus.uploaded,
        deletion_requested_at=datetime.now(timezone.utc),
    ))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(restart_cancelled_job(session, original))
    assert exc.value.status_code == 409
    assert session.added == []


def test_restart_rejects_archived_chat():
    original = _cancelled_job(source_type=JobSourceType.telegram_chat, telegram_chat_id=uuid.uuid4())
    session = RestartSession(source=SimpleNamespace(status=TelegramChatStatus.archived))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(restart_cancelled_job(session, original))
    assert exc.value.status_code == 409
    assert session.added == []


def test_upload_lookup_preserves_owner_filter_and_locks_before_reuse():
    owner_id, upload_id = uuid.uuid4(), uuid.uuid4()
    session = RestartSession(source=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_load_owned_upload(session, upload_id=upload_id, owner_user_id=owner_id))
    assert exc.value.status_code == 404
    statement = session.statements[0]
    assert set(statement.compile().params.values()) == {upload_id, owner_id}
    assert "FOR UPDATE" in str(statement)

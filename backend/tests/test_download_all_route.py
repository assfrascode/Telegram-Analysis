import asyncio
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException, status

from app.api import routes_jobs
from app.models import JobSourceType, JobStatus
from app.services.report_bundle import ReportBundleConflictError


@pytest.fixture(autouse=True)
def _run_to_thread_inline(monkeypatch):
    async def run_inline(function, **kwargs):
        return function(**kwargs)

    monkeypatch.setattr(routes_jobs.asyncio, "to_thread", run_inline)


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Session:
    def __init__(self, *values) -> None:
        self.values = iter(values)

    async def execute(self, statement):
        return _Result(next(self.values))


def _job(*, status_value=JobStatus.completed, source_type=JobSourceType.upload):
    return SimpleNamespace(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        upload_id=uuid.uuid4() if source_type == JobSourceType.upload else None,
        telegram_chat_id=uuid.uuid4() if source_type == JobSourceType.telegram_chat else None,
        source_type=source_type,
        status=status_value,
        report_end_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )


def _install_owned_job(monkeypatch, job) -> None:
    async def get_owned_job(session, *, job_id, user):
        return job

    monkeypatch.setattr(routes_jobs, "get_owned_job_or_404", get_owned_job)


def test_download_all_returns_file_response_and_removes_temp_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    job = _job()
    user = SimpleNamespace(id=job.owner_user_id)
    upload = SimpleNamespace(
        id=job.upload_id,
        owner_user_id=user.id,
        filename="Telegram Export.zip",
        object_key="uploads/original.zip",
    )
    bundle_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle_path, mode="w") as archive:
        archive.writestr("result.json", "{}")

    report = SimpleNamespace(object_key="reports/report.zip")
    _install_owned_job(monkeypatch, job)
    monkeypatch.setattr(routes_jobs, "minio_client", lambda: object())
    monkeypatch.setattr(routes_jobs, "build_report_bundle", lambda **kwargs: str(bundle_path))
    background_tasks = BackgroundTasks()

    response = asyncio.run(
        routes_jobs.download_all(
            job.id,
            background_tasks,
            user=user,
            session=_Session(report, upload),
        )
    )

    assert response.path == str(bundle_path)
    assert response.media_type == "application/zip"
    assert "Telegram%20Export-with-report.zip" in response.headers["content-disposition"]
    assert bundle_path.exists()
    assert len(background_tasks.tasks) == 1
    cleanup = background_tasks.tasks[0]
    cleanup.func(*cleanup.args, **cleanup.kwargs)
    assert not bundle_path.exists()


def test_download_all_rejects_unfinished_job(monkeypatch) -> None:
    job = _job(status_value=JobStatus.running)
    _install_owned_job(monkeypatch, job)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes_jobs.download_all(
                job.id,
                BackgroundTasks(),
                user=SimpleNamespace(id=job.owner_user_id),
                session=_Session(),
            )
        )

    assert raised.value.status_code == status.HTTP_409_CONFLICT
    assert raised.value.detail == "Job is not completed"


def test_download_all_builds_collected_chat_export(monkeypatch, tmp_path: Path) -> None:
    job = _job(source_type=JobSourceType.telegram_chat)
    user = SimpleNamespace(id=job.owner_user_id)
    report = SimpleNamespace(
        object_key="reports/report.zip",
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    chat = SimpleNamespace(
        id=job.telegram_chat_id,
        owner_user_id=user.id,
        title="External chat",
        chat_type="channel",
        telegram_chat_id=123,
    )
    message_db_id = uuid.uuid4()
    message = SimpleNamespace(
        id=message_db_id,
        telegram_message_id=42,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        edited_timestamp=None,
        sender_id="user1",
        sender_name="Alice",
        message_type="message",
        reply_to_message_id=None,
        forwarded_from=None,
        reactions=[],
        text="hello",
    )
    media = SimpleNamespace(
        message_id=message_db_id,
        original_path="telegram/media/photo.jpg",
        minio_object_key="media/photo.jpg",
        media_type="image",
    )
    bundle_path = tmp_path / "collected.zip"
    with zipfile.ZipFile(bundle_path, mode="w") as archive:
        archive.writestr("result.json", "{}")

    captured = {}
    _install_owned_job(monkeypatch, job)
    monkeypatch.setattr(routes_jobs, "minio_client", lambda: object())

    def build_bundle(**kwargs):
        captured.update(kwargs)
        return str(bundle_path)

    monkeypatch.setattr(routes_jobs, "build_collected_chat_bundle", build_bundle)

    response = asyncio.run(
        routes_jobs.download_all(
            job.id,
            BackgroundTasks(),
            user=user,
            session=_Session(report, chat, [message], [media]),
        )
    )

    assert response.path == str(bundle_path)
    assert captured["chat_title"] == "External chat"
    assert captured["messages"][0]["telegram_message_id"] == 42
    assert captured["media"][0].path == "telegram/media/photo.jpg"
    assert "telegram-export-external-chat-2026-01-02-with-report.zip" in response.headers[
        "content-disposition"
    ]


def test_download_all_returns_not_found_when_original_upload_is_missing(monkeypatch) -> None:
    job = _job()
    report = SimpleNamespace(object_key="reports/report.zip")
    _install_owned_job(monkeypatch, job)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes_jobs.download_all(
                job.id,
                BackgroundTasks(),
                user=SimpleNamespace(id=job.owner_user_id),
                session=_Session(report, None),
            )
        )

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND
    assert raised.value.detail == "Original upload not available"


def test_download_all_returns_not_found_when_report_is_missing(monkeypatch) -> None:
    job = _job()
    _install_owned_job(monkeypatch, job)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes_jobs.download_all(
                job.id,
                BackgroundTasks(),
                user=SimpleNamespace(id=job.owner_user_id),
                session=_Session(None),
            )
        )

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND
    assert raised.value.detail == "Report not available"


def test_download_all_maps_report_path_collision_to_conflict(monkeypatch) -> None:
    job = _job()
    user = SimpleNamespace(id=job.owner_user_id)
    upload = SimpleNamespace(
        filename="export.zip",
        object_key="uploads/original.zip",
    )
    report = SimpleNamespace(object_key="reports/report.zip")
    _install_owned_job(monkeypatch, job)
    monkeypatch.setattr(routes_jobs, "minio_client", lambda: object())

    def fail_bundle(**kwargs):
        raise ReportBundleConflictError("Original upload already contains a report directory")

    monkeypatch.setattr(routes_jobs, "build_report_bundle", fail_bundle)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes_jobs.download_all(
                job.id,
                BackgroundTasks(),
                user=user,
                session=_Session(report, upload),
            )
        )

    assert raised.value.status_code == status.HTTP_409_CONFLICT
    assert "already contains a report directory" in raised.value.detail


def test_download_all_preserves_foreign_job_not_found(monkeypatch) -> None:
    async def reject_foreign_job(session, *, job_id, user):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    monkeypatch.setattr(routes_jobs, "get_owned_job_or_404", reject_foreign_job)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            routes_jobs.download_all(
                uuid.uuid4(),
                BackgroundTasks(),
                user=SimpleNamespace(id=uuid.uuid4()),
                session=_Session(),
            )
        )

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND
    assert raised.value.detail == "Resource not found"

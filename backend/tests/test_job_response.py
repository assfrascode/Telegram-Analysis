import uuid
from datetime import datetime, timezone

from app.api.routes_jobs import _job_response
from app.models import Job, JobSourceType, JobStatus


def test_job_response_includes_source_name() -> None:
    job = Job(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        source_type=JobSourceType.upload,
        upload_id=uuid.uuid4(),
        source_name="Example Group",
        status=JobStatus.completed,
        options={},
        created_at=datetime.now(timezone.utc),
    )

    response = _job_response(job)

    assert response.source_name == "Example Group"

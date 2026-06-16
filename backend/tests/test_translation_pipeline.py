import os
import uuid

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import Job
from app.workers import subjects
from app.workers.pipeline import next_subject_after_messages, next_subject_after_translation


def _job(options: dict) -> Job:
    return Job(id=uuid.uuid4(), owner_user_id=uuid.uuid4(), options=options)


def test_messages_route_to_translation_when_enabled() -> None:
    assert next_subject_after_messages(_job({"translate": True})) == (
        subjects.MESSAGES_TRANSLATE,
        "translate",
    )


def test_messages_route_to_media_or_chunk_when_translation_disabled() -> None:
    assert next_subject_after_messages(_job({"translate": False, "analyze_media": True})) == (
        subjects.MEDIA_DESCRIBE,
        "media",
    )
    assert next_subject_after_messages(_job({"translate": False, "analyze_media": False})) == (
        subjects.CHUNK_CREATE,
        "chunk",
    )


def test_translation_routes_to_media_or_chunk() -> None:
    assert next_subject_after_translation(_job({"analyze_media": True})) == (
        subjects.MEDIA_DESCRIBE,
        "media",
    )
    assert next_subject_after_translation(_job({"analyze_media": False})) == (
        subjects.CHUNK_CREATE,
        "chunk",
    )

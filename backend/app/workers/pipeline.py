from app.models import Job
from app.workers import subjects


def next_subject_after_messages(job: Job) -> tuple[str, str]:
    if (job.options or {}).get("translate", False):
        return subjects.MESSAGES_TRANSLATE, "translate"
    return next_subject_after_translation(job)


def next_subject_after_translation(job: Job) -> tuple[str, str]:
    if (job.options or {}).get("analyze_media", True):
        return subjects.MEDIA_DESCRIBE, "media"
    return subjects.CHUNK_CREATE, "chunk"


def next_subject_after_media_analysis(job: Job) -> tuple[str, str]:
    if (job.options or {}).get("analyze_media", True):
        return subjects.MEDIA_TRANSCRIBE, "transcribe"
    return subjects.CHUNK_CREATE, "chunk"

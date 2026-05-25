import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobEvent
from app.nats_client import publish_json


async def record_event_db_only(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    event_type: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
) -> JobEvent:
    payload = payload or {}
    event = JobEvent(
        job_id=job_id,
        owner_user_id=owner_user_id,
        event_type=event_type,
        level=level,
        message=message,
        payload=payload,
    )
    session.add(event)
    await session.flush()
    return event


async def publish_event(js, event: JobEvent) -> None:
    await publish_json(
        js,
        f"events.job.{event.job_id}",
        {
            "id": event.id,
            "job_id": str(event.job_id),
            "owner_user_id": str(event.owner_user_id),
            "event_type": event.event_type,
            "level": event.level,
            "message": event.message,
            "payload": event.payload,
            "created_at": event.created_at.isoformat(),
        },
    )


async def record_event(
    session: AsyncSession,
    *,
    js,
    job_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    event_type: str,
    message: str,
    level: str = "info",
    payload: dict[str, Any] | None = None,
    raise_publish_errors: bool = True,
) -> JobEvent:
    event = await record_event_db_only(
        session,
        job_id=job_id,
        owner_user_id=owner_user_id,
        event_type=event_type,
        message=message,
        level=level,
        payload=payload,
    )
    try:
        await publish_event(js, event)
    except Exception:
        if raise_publish_errors:
            raise
    return event

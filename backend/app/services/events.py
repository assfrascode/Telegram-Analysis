import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import JobEvent
from app.nats_client import publish_json


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

    await publish_json(
        js,
        f"events.job.{job_id}",
        {
            "id": event.id,
            "job_id": str(job_id),
            "owner_user_id": str(owner_user_id),
            "event_type": event_type,
            "level": level,
            "message": message,
            "payload": payload,
            "created_at": event.created_at.isoformat(),
        },
    )
    return event

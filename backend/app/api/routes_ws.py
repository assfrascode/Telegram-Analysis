import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from nats.errors import TimeoutError as NATSTimeoutError
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job, JobEvent
from app.nats_client import connect_nats
from app.security import get_user_from_token

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/jobs/{job_id}")
async def job_websocket(websocket: WebSocket, job_id: uuid.UUID, token: str):
    await websocket.accept()

    async with SessionLocal() as session:
        user = await get_user_from_token(session, token)
        if not user:
            await websocket.close(code=4401, reason="Unauthorized")
            return

        result = await session.execute(select(Job).where(Job.id == job_id, Job.owner_user_id == user.id))
        job = result.scalar_one_or_none()
        if not job:
            await websocket.close(code=4404, reason="Job not found")
            return

        backlog = await session.execute(
            select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.owner_user_id == user.id).order_by(JobEvent.id).limit(1000)
        )
        for event in backlog.scalars().all():
            await websocket.send_json(
                {
                    "id": event.id,
                    "job_id": str(job_id),
                    "event_type": event.event_type,
                    "level": event.level,
                    "message": event.message,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                    "replayed": True,
                }
            )

    nc = await connect_nats()
    sub = await nc.subscribe(f"events.job.{job_id}")

    try:
        while True:
            try:
                msg = await sub.next_msg(timeout=60)
            except NATSTimeoutError:
                await websocket.send_json({"event_type": "ws.keepalive", "message": "keepalive"})
                continue

            # Events are already JSON-encoded by record_event/publish_json.
            await websocket.send_text(msg.data.decode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        await nc.drain()

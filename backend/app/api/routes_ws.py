import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from nats.errors import TimeoutError as NATSTimeoutError
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job, JobEvent
from app.nats_client import connect_nats
from app.services.websocket_tickets import consume_websocket_ticket

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/jobs/{job_id}")
async def job_websocket(websocket: WebSocket, job_id: uuid.UUID, ticket: str):
    async with SessionLocal() as session:
        owner_user_id = await consume_websocket_ticket(session, raw_token=ticket, job_id=job_id)
        if owner_user_id is None:
            await websocket.close(code=4401, reason="Unauthorized")
            return

        result = await session.execute(select(Job).where(Job.id == job_id, Job.owner_user_id == owner_user_id))
        job = result.scalar_one_or_none()
        if not job:
            await websocket.close(code=4404, reason="Job not found")
            return

        await websocket.accept()

        backlog = await session.execute(
            select(JobEvent)
            .where(JobEvent.job_id == job_id, JobEvent.owner_user_id == owner_user_id)
            .order_by(JobEvent.id)
            .limit(1000)
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

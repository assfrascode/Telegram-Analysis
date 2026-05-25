import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext

from app.config import get_settings

settings = get_settings()


TASK_STREAM = "CHAT_ANALYSE_TASKS"
EVENT_STREAM = "CHAT_ANALYSE_EVENTS"
DLQ_STREAM = "CHAT_ANALYSE_DLQ"


async def connect_nats() -> NATS:
    return await nats.connect(settings.nats_url)


async def ensure_streams(js: JetStreamContext) -> None:
    from nats.js.api import RetentionPolicy, StreamConfig, StorageType

    streams = [
        StreamConfig(
            name=TASK_STREAM,
            subjects=["jobs.>"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=7 * 24 * 60 * 60,
        ),
        StreamConfig(
            name=EVENT_STREAM,
            subjects=["events.>"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=30 * 24 * 60 * 60,
        ),
        StreamConfig(
            name=DLQ_STREAM,
            subjects=["dlq.>"],
            retention=RetentionPolicy.LIMITS,
            storage=StorageType.FILE,
            max_age=30 * 24 * 60 * 60,
        ),
    ]

    for cfg in streams:
        try:
            await js.add_stream(cfg)
        except Exception:
            # Stream likely exists. For production, inspect and update config explicitly.
            pass


async def publish_json(js: JetStreamContext, subject: str, payload: dict[str, Any]) -> None:
    await js.publish(subject, json.dumps(payload, default=str).encode("utf-8"))


@asynccontextmanager
async def nats_context() -> AsyncIterator[tuple[NATS, JetStreamContext]]:
    nc = await connect_nats()
    try:
        js = nc.jetstream()
        await ensure_streams(js)
        yield nc, js
    finally:
        await nc.drain()

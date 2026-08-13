import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import nats
from nats.aio.client import Client as NATS
from nats.js import JetStreamContext

from app.config import get_settings
from app.observability.metrics import QUEUE_PUBLISHED, QUEUE_PUBLISH_DURATION

settings = get_settings()


TASK_STREAM = "CHAT_ANALYSE_TASKS"
EVENT_STREAM = "CHAT_ANALYSE_EVENTS"
DLQ_STREAM = "CHAT_ANALYSE_DLQ"


async def connect_nats() -> NATS:
    options: dict[str, str] = {}
    if settings.nats_token:
        options["token"] = settings.nats_token
    elif settings.nats_user:
        options["user"] = settings.nats_user
        options["password"] = settings.nats_password
    # Credentials embedded in nats://user:password@host remain supported when
    # the explicit fields are empty.
    return await nats.connect(settings.nats_url, **options)


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


async def publish_json(js: JetStreamContext, subject: str, payload: dict[str, Any]) -> Any:
    if subject.startswith("jobs."):
        stream = TASK_STREAM
        metric_subject = subject
    elif subject.startswith("dlq."):
        stream = DLQ_STREAM
        metric_subject = subject
    else:
        stream = EVENT_STREAM
        # Event subjects contain user/job UUIDs; do not turn those IDs into
        # unbounded Prometheus label values.
        metric_subject = "events"
    started = time.perf_counter()
    status = "success"
    try:
        return await js.publish(subject, json.dumps(payload, default=str).encode("utf-8"))
    except Exception:
        status = "error"
        raise
    finally:
        QUEUE_PUBLISHED.labels(stream, metric_subject, status).inc()
        QUEUE_PUBLISH_DURATION.labels(stream, metric_subject).observe(
            time.perf_counter() - started
        )


async def task_queue_backlog(js: JetStreamContext) -> int:
    """Return messages pending delivery or acknowledgement across task consumers."""
    consumers = await js.consumers_info(TASK_STREAM)
    return sum(
        int(getattr(consumer, "num_pending", 0) or 0)
        + int(getattr(consumer, "num_ack_pending", 0) or 0)
        for consumer in consumers
    )


@asynccontextmanager
async def nats_context() -> AsyncIterator[tuple[NATS, JetStreamContext]]:
    nc = await connect_nats()
    try:
        js = nc.jetstream()
        await ensure_streams(js)
        yield nc, js
    finally:
        await nc.drain()

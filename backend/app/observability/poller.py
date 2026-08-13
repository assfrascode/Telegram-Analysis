import asyncio
import logging

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import StepStatus, TelegramReportSchedule, WorkerDeadLetter, WorkerTask
from app.nats_client import TASK_STREAM, connect_nats, ensure_streams, task_queue_backlog
from app.observability.metrics import (
    DEPENDENCY_UP,
    FAILED_SCHEDULES,
    OBSERVABILITY_REFRESH,
    QUEUE_BACKLOG,
    WORKER_DEAD_LETTERS,
    WORKER_RETRY_BACKLOG,
    WORKER_TASK_BACKLOG,
)
from app.services.capacity import capacity_snapshot


logger = logging.getLogger(__name__)
settings = get_settings()


async def refresh_operational_metrics() -> None:
    async with SessionLocal() as session:
        snapshot = await capacity_snapshot(session)
        for dependency, result in snapshot.get("resources", {}).items():
            if dependency == "vllm":
                for endpoint, endpoint_result in result.items():
                    DEPENDENCY_UP.labels(f"vllm_{endpoint}").set(
                        1 if endpoint_result.get("ok") else 0
                    )
                continue
            DEPENDENCY_UP.labels(dependency).set(1 if result.get("ok") else 0)

        counts = snapshot.get("counts", {})
        WORKER_TASK_BACKLOG.set(int(counts.get("pending_worker_tasks", 0) or 0))
        WORKER_DEAD_LETTERS.set(int(counts.get("dead_letters_total", 0) or 0))

        repeated_retries = int(
            (
                await session.execute(
                    select(func.count(WorkerTask.id)).where(
                        WorkerTask.status == StepStatus.failed_retryable,
                        WorkerTask.attempts >= 2,
                    )
                )
            ).scalar()
            or 0
        )
        WORKER_RETRY_BACKLOG.set(repeated_retries)
        failed_schedules = int(
            (
                await session.execute(
                    select(func.count(TelegramReportSchedule.id)).where(
                        TelegramReportSchedule.enabled.is_(True),
                        TelegramReportSchedule.last_error.is_not(None),
                    )
                )
            ).scalar()
            or 0
        )
        FAILED_SCHEDULES.set(failed_schedules)

        # Keep the direct count query here so the gauge remains available if the
        # capacity response later stops exposing dead-letter internals.
        dead_letters = int(
            (await session.execute(select(func.count(WorkerDeadLetter.id)))).scalar() or 0
        )
        WORKER_DEAD_LETTERS.set(dead_letters)

    nc = await connect_nats()
    try:
        js = nc.jetstream()
        await ensure_streams(js)
        QUEUE_BACKLOG.labels(TASK_STREAM).set(await task_queue_backlog(js))
    finally:
        await nc.drain()


async def run_operational_metrics_poller(stop: asyncio.Event) -> None:
    interval = max(5, int(settings.observability_poll_seconds))
    while not stop.is_set():
        try:
            await refresh_operational_metrics()
            OBSERVABILITY_REFRESH.labels("success").inc()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            OBSERVABILITY_REFRESH.labels("error").inc()
            logger.warning(
                "Operational metrics refresh failed",
                extra={"event": "metrics.refresh_failed", "error_type": type(exc).__name__},
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            pass

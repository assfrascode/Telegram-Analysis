
import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Job, JobStatus, StepStatus, TelegramMedia, WorkerDeadLetter, WorkerTask
from app.nats_client import TASK_STREAM, connect_nats, ensure_streams
from app.services.minio_store import minio_client

settings = get_settings()
JOB_ADMISSION_LOCK_KEY = 0x434841544A4F42  # "CHATJOB", PostgreSQL advisory xact lock


@dataclass(slots=True)
class HealthCheck:
    name: str
    ok: bool
    status: str
    latency_ms: float | None = None
    detail: str | None = None
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "ok": self.ok,
            "status": self.status,
        }
        if self.latency_ms is not None:
            data["latency_ms"] = round(self.latency_ms, 2)
        if self.detail:
            data["detail"] = self.detail
        if self.metadata:
            data.update(self.metadata)
        return data


async def _timed(name: str, coro, *, timeout_seconds: float | None = None) -> HealthCheck:
    start = time.perf_counter()
    timeout = timeout_seconds or settings.capacity_health_timeout_seconds
    try:
        result = await asyncio.wait_for(coro, timeout=timeout)
        latency_ms = (time.perf_counter() - start) * 1000
        if isinstance(result, HealthCheck):
            result.latency_ms = latency_ms
            return result
        return HealthCheck(name=name, ok=True, status="ok", latency_ms=latency_ms)
    except asyncio.TimeoutError:
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthCheck(
            name=name,
            ok=False,
            status="timeout",
            latency_ms=latency_ms,
            detail=f"Healthcheck timed out after {timeout:.1f}s",
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000
        return HealthCheck(
            name=name,
            ok=False,
            status="error",
            latency_ms=latency_ms,
            detail=str(exc)[:1000],
        )


async def _check_postgres(session: AsyncSession) -> HealthCheck:
    await session.execute(text("SELECT 1"))
    return HealthCheck(name="postgres", ok=True, status="ok")


async def _check_minio() -> HealthCheck:
    def _bucket_exists() -> bool:
        return minio_client().bucket_exists(settings.minio_bucket)

    exists = await asyncio.to_thread(_bucket_exists)
    return HealthCheck(
        name="minio",
        ok=exists,
        status="ok" if exists else "bucket_missing",
        detail=None if exists else f"Bucket does not exist: {settings.minio_bucket}",
        metadata={"bucket": settings.minio_bucket},
    )


async def _check_qdrant() -> HealthCheck:
    base = settings.qdrant_url.rstrip("/")
    headers = {"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {}
    async with httpx.AsyncClient(timeout=settings.capacity_health_timeout_seconds, headers=headers) as client:
        response = await client.get(f"{base}/readyz")
        if response.status_code == 404:
            # Older Qdrant builds may not expose /readyz. /collections is enough
            # to prove the REST API is reachable.
            response = await client.get(f"{base}/collections")
        response.raise_for_status()
    return HealthCheck(name="qdrant", ok=True, status="ok")


async def _check_nats() -> HealthCheck:
    nc = await connect_nats()
    try:
        js = nc.jetstream()
        await ensure_streams(js)
        stream_messages: int | None = None
        try:
            info = await js.stream_info(TASK_STREAM)
            stream_messages = int(getattr(info.state, "messages", 0) or 0)
        except Exception:
            # If stream_info fails but connect/ensure_streams worked, the server
            # is still reachable. The missing count is represented as null.
            stream_messages = None
        return HealthCheck(
            name="nats",
            ok=True,
            status="ok",
            metadata={"task_stream_messages": stream_messages},
        )
    finally:
        await nc.drain()


def _model_endpoint_label(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.netloc or base_url
    return host.replace(":", "_").replace("/", "_")


async def _check_openai_models_endpoint(name: str, base_url: str) -> HealthCheck:
    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}
    async with httpx.AsyncClient(timeout=settings.capacity_health_timeout_seconds) as client:
        response = await client.get(f"{base}/models", headers=headers)
        response.raise_for_status()
        body = response.json()
    count = len(body.get("data", [])) if isinstance(body, dict) else None
    return HealthCheck(name=name, ok=True, status="ok", metadata={"models": count})


async def _vllm_healthchecks() -> dict[str, dict[str, Any]]:
    if settings.llm_mock_enabled:
        return {
            "mock": {
                "ok": True,
                "status": "mock_enabled",
                "detail": "LLM_MOCK_ENABLED=true; model endpoint checks skipped.",
            }
        }
    if not settings.capacity_check_vllm:
        return {
            "skipped": {
                "ok": True,
                "status": "disabled",
                "detail": "CAPACITY_CHECK_VLLM=false; model endpoint checks skipped.",
            }
        }

    endpoints = {
        "text": settings.vllm_text_base_url,
        "vision": settings.vllm_vision_base_url,
        "embedding": settings.vllm_embedding_base_url,
        "reranker": settings.vllm_reranker_base_url,
    }
    checks: dict[str, dict[str, Any]] = {}
    for name, url in endpoints.items():
        key = f"{name}_{_model_endpoint_label(url)}"
        check = await _timed(f"vllm_{name}", _check_openai_models_endpoint(name, url))
        checks[key] = check.as_dict()
    return checks


async def _db_counts(session: AsyncSession) -> dict[str, int]:
    active_statuses = [JobStatus.queued, JobStatus.running, JobStatus.cancelling]
    active_jobs = int(
        (await session.execute(select(func.count(Job.id)).where(Job.status.in_(active_statuses)))).scalar() or 0
    )
    pending_media_tasks = int(
        (
            await session.execute(
                select(func.count(TelegramMedia.id)).where(
                    TelegramMedia.status.in_([StepStatus.pending, StepStatus.running, StepStatus.failed_retryable])
                )
            )
        ).scalar()
        or 0
    )
    pending_worker_tasks = int(
        (
            await session.execute(
                select(func.count(WorkerTask.id)).where(
                    WorkerTask.status.in_([StepStatus.pending, StepStatus.running, StepStatus.failed_retryable])
                )
            )
        ).scalar()
        or 0
    )
    failed_retryable_tasks = int(
        (
            await session.execute(
                select(func.count(WorkerTask.id)).where(WorkerTask.status == StepStatus.failed_retryable)
            )
        ).scalar()
        or 0
    )
    dead_letters_total = int((await session.execute(select(func.count(WorkerDeadLetter.id)))).scalar() or 0)
    return {
        "active_jobs": active_jobs,
        "pending_media_tasks": pending_media_tasks,
        "pending_worker_tasks": pending_worker_tasks,
        "failed_retryable_tasks": failed_retryable_tasks,
        "dead_letters_total": dead_letters_total,
    }


def _resource_blockers(resources: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    required = {
        "postgres": settings.capacity_require_postgres,
        "minio": settings.capacity_require_minio,
        "nats": settings.capacity_require_nats,
        "qdrant": settings.capacity_require_qdrant,
    }
    for name, is_required in required.items():
        if is_required and not resources.get(name, {}).get("ok", False):
            blockers.append(f"{name}_unhealthy")

    if settings.capacity_require_vllm:
        for name, check in resources.get("vllm", {}).items():
            if not check.get("ok", False):
                blockers.append(f"vllm_{name}_unhealthy")
    return blockers


def _limit_blockers(counts: dict[str, int], resources: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if counts.get("active_jobs", 0) >= settings.max_active_jobs:
        blockers.append("max_active_jobs_reached")
    if counts.get("pending_media_tasks", 0) >= settings.max_pending_media_tasks:
        blockers.append("max_pending_media_tasks_reached")
    if counts.get("pending_worker_tasks", 0) >= settings.max_pending_worker_tasks:
        blockers.append("max_pending_worker_tasks_reached")
    if counts.get("failed_retryable_tasks", 0) >= settings.max_failed_retryable_tasks:
        blockers.append("too_many_retryable_failures")

    task_stream_messages = resources.get("nats", {}).get("task_stream_messages")
    if (
        isinstance(task_stream_messages, int)
        and task_stream_messages >= settings.max_nats_task_stream_messages
    ):
        blockers.append("max_nats_task_stream_messages_reached")
    return blockers


async def capacity_snapshot(session: AsyncSession) -> dict[str, Any]:
    postgres = await _timed("postgres", _check_postgres(session))
    resources: dict[str, Any] = {"postgres": postgres.as_dict()}

    counts = {
        "active_jobs": 0,
        "pending_media_tasks": 0,
        "pending_worker_tasks": 0,
        "failed_retryable_tasks": 0,
        "dead_letters_total": 0,
    }
    if postgres.ok:
        try:
            counts = await _db_counts(session)
        except Exception as exc:
            resources["postgres"] = HealthCheck(
                name="postgres",
                ok=False,
                status="count_error",
                detail=str(exc)[:1000],
            ).as_dict()

    # Run external dependency checks concurrently after the DB check. They have
    # independent short timeouts so one unhealthy service cannot hang /capacity.
    minio_check, nats_check, qdrant_check, vllm_checks = await asyncio.gather(
        _timed("minio", _check_minio()),
        _timed("nats", _check_nats()),
        _timed("qdrant", _check_qdrant()),
        _vllm_healthchecks(),
    )
    resources.update(
        {
            "minio": minio_check.as_dict(),
            "nats": nats_check.as_dict(),
            "qdrant": qdrant_check.as_dict(),
            "vllm": vllm_checks,
        }
    )

    blockers = _resource_blockers(resources) + _limit_blockers(counts, resources)

    return {
        "accepting_jobs": not blockers,
        "blockers": blockers,
        "counts": counts,
        "limits": {
            "max_active_jobs": settings.max_active_jobs,
            "max_pending_media_tasks": settings.max_pending_media_tasks,
            "max_pending_worker_tasks": settings.max_pending_worker_tasks,
            "max_failed_retryable_tasks": settings.max_failed_retryable_tasks,
            "max_nats_task_stream_messages": settings.max_nats_task_stream_messages,
        },
        # Backwards-compatible flattened fields used by older frontend/debug calls.
        **counts,
        "max_active_jobs": settings.max_active_jobs,
        "max_pending_media_tasks": settings.max_pending_media_tasks,
        "max_pending_worker_tasks": settings.max_pending_worker_tasks,
        "max_failed_retryable_tasks": settings.max_failed_retryable_tasks,
        "max_nats_task_stream_messages": settings.max_nats_task_stream_messages,
        "resources": resources,
    }


async def ensure_accepting_jobs(session: AsyncSession) -> None:
    from fastapi import HTTPException, status

    try:
        # The caller inserts and commits its Job on this same session. Holding a
        # transaction-scoped advisory lock therefore serializes count -> insert
        # reservations across API processes and the report scheduler.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": JOB_ADMISSION_LOCK_KEY},
        )
        snapshot = await capacity_snapshot(session)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "message": "Systemzustand konnte nicht geprüft werden. Neuer Auftrag wird abgelehnt.",
                "error": str(exc)[:1000],
            },
        ) from exc

    if snapshot["accepting_jobs"]:
        return

    resource_failure = any(str(blocker).endswith("_unhealthy") for blocker in snapshot["blockers"])
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE if resource_failure else status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "message": "System ausgelastet oder nicht bereit. Neuer Auftrag kann derzeit nicht gestartet werden.",
            "capacity": snapshot,
        },
    )

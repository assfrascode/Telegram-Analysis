import logging
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from prometheus_client import Counter, Gauge, Histogram, start_http_server


logger = logging.getLogger(__name__)

HTTP_REQUESTS = Counter(
    "chat_analyse_http_requests_total",
    "HTTP requests completed by the API.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "chat_analyse_http_request_duration_seconds",
    "HTTP request duration.",
    ("method", "route"),
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "chat_analyse_http_requests_in_progress",
    "HTTP requests currently in progress.",
    ("method",),
)

QUEUE_PUBLISHED = Counter(
    "chat_analyse_queue_messages_published_total",
    "Messages published to JetStream.",
    ("stream", "subject", "status"),
)
QUEUE_PUBLISH_DURATION = Histogram(
    "chat_analyse_queue_publish_duration_seconds",
    "JetStream publish latency.",
    ("stream", "subject"),
)
QUEUE_BACKLOG = Gauge(
    "chat_analyse_queue_backlog_messages",
    "Messages pending delivery or acknowledgement across queue consumers.",
    ("stream",),
)

WORKER_TASKS = Counter(
    "chat_analyse_worker_tasks_total",
    "Worker task outcomes.",
    ("subject", "status"),
)
WORKER_TASK_DURATION = Histogram(
    "chat_analyse_worker_task_duration_seconds",
    "End-to-end worker handler duration.",
    ("subject", "status"),
)
WORKER_RETRIES = Counter(
    "chat_analyse_worker_retries_total",
    "Worker tasks scheduled for another attempt.",
    ("subject", "reason"),
)
WORKER_DEAD_LETTERS_CREATED = Counter(
    "chat_analyse_worker_dead_letters_created_total",
    "Worker dead letters created.",
    ("subject", "reason"),
)
WORKER_TASK_BACKLOG = Gauge(
    "chat_analyse_worker_task_backlog",
    "Worker tasks not in a terminal state.",
)
WORKER_RETRY_BACKLOG = Gauge(
    "chat_analyse_worker_retry_backlog",
    "Retryable worker tasks with at least two attempts.",
)
WORKER_DEAD_LETTERS = Gauge(
    "chat_analyse_worker_dead_letters",
    "Persisted worker dead letters.",
)

MODEL_CALLS = Counter(
    "chat_analyse_model_calls_total",
    "Logical calls to model and translation providers.",
    ("provider", "operation", "model", "status"),
)
MODEL_CALL_DURATION = Histogram(
    "chat_analyse_model_call_duration_seconds",
    "Logical model-call duration.",
    ("provider", "operation", "model"),
)

SYNC_RUNS = Counter(
    "chat_analyse_sync_runs_total",
    "Telegram synchronization outcomes.",
    ("source", "status"),
)
SYNC_DURATION = Histogram(
    "chat_analyse_sync_duration_seconds",
    "Telegram synchronization duration.",
    ("source", "status"),
)

JOBS_TERMINAL = Counter(
    "chat_analyse_jobs_terminal_total",
    "Analysis jobs entering a terminal state.",
    ("status", "source_type"),
)
JOB_DURATION = Histogram(
    "chat_analyse_job_duration_seconds",
    "Analysis job duration from creation to terminal state.",
    ("status", "source_type"),
)

DEPENDENCY_UP = Gauge(
    "chat_analyse_dependency_up",
    "Whether a required application dependency is healthy.",
    ("dependency",),
)
FAILED_SCHEDULES = Gauge(
    "chat_analyse_failed_schedules",
    "Enabled schedules with a recorded failure.",
)
SCHEDULE_RUNS = Counter(
    "chat_analyse_schedule_runs_total",
    "Report scheduler outcomes.",
    ("status",),
)
OBSERVABILITY_REFRESH = Counter(
    "chat_analyse_observability_refresh_total",
    "Operational metric refresh outcomes.",
    ("status",),
)

_server_lock = threading.Lock()
_server_started = False


def start_metrics_server(port: int, *, enabled: bool = True) -> None:
    global _server_started
    if not enabled:
        return
    with _server_lock:
        if _server_started:
            return
        start_http_server(port, addr="0.0.0.0")
        _server_started = True
    logger.info("Metrics exporter started", extra={"event": "metrics.started"})


@asynccontextmanager
async def observe_model_call(
    provider: str,
    operation: str,
    model: str,
) -> AsyncIterator[None]:
    started = time.perf_counter()
    status = "success"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        MODEL_CALLS.labels(provider, operation, model, status).inc()
        MODEL_CALL_DURATION.labels(provider, operation, model).observe(time.perf_counter() - started)


@contextmanager
def observe_worker_task(subject: str) -> Iterator[dict[str, str]]:
    started = time.perf_counter()
    result = {"status": "error"}
    try:
        yield result
    finally:
        status = result["status"]
        WORKER_TASKS.labels(subject, status).inc()
        WORKER_TASK_DURATION.labels(subject, status).observe(time.perf_counter() - started)


def instrument_worker_task(
    function: Callable[..., Awaitable[str]],
) -> Callable[..., Awaitable[str]]:
    async def wrapped(worker, payload: dict, *args, **kwargs) -> str:
        with observe_worker_task(worker.subject) as observation:
            try:
                action = await function(worker, payload, *args, **kwargs)
            except Exception:
                observation["status"] = "error"
                raise
            observation["status"] = "retrying" if action == "nak" else "acked"
            return action

    return wrapped


def record_job_terminal(job) -> None:
    status = getattr(getattr(job, "status", None), "value", str(getattr(job, "status", "unknown")))
    source_type = getattr(
        getattr(job, "source_type", None),
        "value",
        str(getattr(job, "source_type", "unknown")),
    )
    JOBS_TERMINAL.labels(status, source_type).inc()
    created_at = getattr(job, "created_at", None)
    completed_at = getattr(job, "completed_at", None)
    if created_at is not None and completed_at is not None:
        duration = max(0.0, (completed_at - created_at).total_seconds())
        JOB_DURATION.labels(status, source_type).observe(duration)


def record_sync_terminal(run, source: str) -> None:
    status = getattr(getattr(run, "status", None), "value", str(getattr(run, "status", "unknown")))
    SYNC_RUNS.labels(source, status).inc()
    started_at = getattr(run, "started_at", None)
    completed_at = getattr(run, "completed_at", None)
    if started_at is not None and completed_at is not None:
        duration = max(0.0, (completed_at - started_at).total_seconds())
        SYNC_DURATION.labels(source, status).observe(duration)

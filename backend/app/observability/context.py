from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)


@dataclass(frozen=True, slots=True)
class CorrelationIds:
    request_id: str | None
    job_id: str | None
    task_id: str | None


def correlation_ids() -> CorrelationIds:
    return CorrelationIds(
        request_id=_request_id.get(),
        job_id=_job_id.get(),
        task_id=_task_id.get(),
    )


def current_request_id() -> str | None:
    return _request_id.get()


@contextmanager
def correlation_context(
    *,
    request_id: str | None = None,
    job_id: str | None = None,
    task_id: str | None = None,
) -> Iterator[None]:
    """Bind correlation IDs for logs emitted by the current async context."""
    tokens = []
    if request_id is not None:
        tokens.append((_request_id, _request_id.set(str(request_id))))
    if job_id is not None:
        tokens.append((_job_id, _job_id.set(str(job_id))))
    if task_id is not None:
        tokens.append((_task_id, _task_id.set(str(task_id))))
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)


def correlated_worker_task(function: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
    """Bind IDs from a worker payload without logging the payload itself."""

    async def wrapped(worker, payload: dict, *args, **kwargs) -> str:
        job_id = str(payload.get("job_id") or "") or None
        task_id = str(payload.get("task_key") or f"{worker.subject}:{job_id}")
        request_id = str(payload.get("request_id") or "") or None
        with correlation_context(request_id=request_id, job_id=job_id, task_id=task_id):
            return await function(worker, payload, *args, **kwargs)

    return wrapped

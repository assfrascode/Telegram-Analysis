import json
import logging
from types import SimpleNamespace
from io import StringIO

import pytest

from app.observability.context import correlation_context
from app.observability.logging import JsonFormatter
from app.observability.metrics import MODEL_CALLS, observe_model_call


def test_json_logs_include_correlations_and_redact_credentials():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.structured")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    with correlation_context(request_id="req-1", job_id="job-2", task_id="task-3"):
        logger.info(
            "dependency rejected authorization=Bearer abc123 password=hunter2",
            extra={"event": "dependency.rejected", "subject": "jobs.question.answer"},
        )

    document = json.loads(stream.getvalue())
    assert document["request_id"] == "req-1"
    assert document["job_id"] == "job-2"
    assert document["task_id"] == "task-3"
    assert document["event"] == "dependency.rejected"
    assert document["subject"] == "jobs.question.answer"
    assert "abc123" not in document["message"]
    assert "hunter2" not in document["message"]


def test_json_logs_omit_sensitive_exception_messages():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.exception")
    logger.handlers = [handler]
    logger.propagate = False

    try:
        raise RuntimeError("telegram message body and token=do-not-log")
    except RuntimeError:
        logger.exception("Dependency call failed")

    document = json.loads(stream.getvalue())
    assert document["message"] == "Dependency call failed"
    assert document["exception_type"] == "RuntimeError"
    assert "telegram message body" not in stream.getvalue()
    assert "do-not-log" not in stream.getvalue()


def test_model_call_metrics_record_success_and_failure():
    import asyncio

    success_before = MODEL_CALLS.labels("test", "operation", "model", "success")._value.get()
    error_before = MODEL_CALLS.labels("test", "operation", "model", "error")._value.get()

    async def exercise_metrics() -> None:
        async with observe_model_call("test", "operation", "model"):
            pass
        with pytest.raises(RuntimeError):
            async with observe_model_call("test", "operation", "model"):
                raise RuntimeError("expected")

    asyncio.run(exercise_metrics())

    assert MODEL_CALLS.labels("test", "operation", "model", "success")._value.get() == success_before + 1
    assert MODEL_CALLS.labels("test", "operation", "model", "error")._value.get() == error_before + 1


def test_application_has_no_unstructured_print_logging():
    from pathlib import Path

    app_root = Path(__file__).parents[1] / "app"
    offenders = [
        str(path.relative_to(app_root))
        for path in app_root.rglob("*.py")
        if "print(" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_task_queue_backlog_counts_pending_and_in_flight_messages():
    import asyncio

    from app.nats_client import task_queue_backlog

    class JetStream:
        async def consumers_info(self, stream):
            assert stream == "CHAT_ANALYSE_TASKS"
            return [
                SimpleNamespace(num_pending=7, num_ack_pending=2),
                SimpleNamespace(num_pending=3, num_ack_pending=None),
            ]

    assert asyncio.run(task_queue_backlog(JetStream())) == 12

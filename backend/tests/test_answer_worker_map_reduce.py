import asyncio
import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.services.answer_generation import EvidenceChunk
from app.workers import rag_worker
from app.workers.rag_worker import AnswerWorker


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class FakeGateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    async def answer_prompt(self, prompt: str, *, max_tokens: int = 4096) -> str:
        self.calls.append((prompt, max_tokens))
        if "Evidence batch" in prompt:
            return f"mapped summary {len(self.calls)}"
        if "Intermediate summaries:" in prompt:
            return "final answer from mapped summaries"
        return "direct answer from raw evidence"


def _chunk(index: int, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=uuid.UUID(f"00000000-0000-0000-0000-{index:012d}"),
        chunk_index=index,
        text=text,
        message_ids=[str(index)],
    )


def _question() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000101"),
        question_index=1,
        text="Welche Narrative dominieren?",
    )


def _question_run() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000201"))


def _job() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.UUID("00000000-0000-0000-0000-000000000301"))


def _worker_with_recorded_events(events: list[dict]) -> AnswerWorker:
    worker = AnswerWorker()

    async def emit_event(session, *, job, event_type, message, payload=None, level="info"):
        events.append(
            {
                "event_type": event_type,
                "message": message,
                "payload": payload or {},
                "level": level,
            }
        )

    async def checkpoint_cancelled(session, job, **kwargs):
        return None

    worker.emit_event = emit_event
    worker.checkpoint_cancelled = checkpoint_cancelled
    return worker


def test_answer_concurrency_is_capped_by_http_connections(monkeypatch) -> None:
    monkeypatch.setattr(rag_worker.settings, "vllm_text_concurrency", 8)
    monkeypatch.setattr(rag_worker.settings, "vllm_text_http_max_connections", 3)

    assert rag_worker.answer_text_concurrency() == 3

    monkeypatch.setattr(rag_worker.settings, "vllm_text_concurrency", 2)
    monkeypatch.setattr(rag_worker.settings, "vllm_text_http_max_connections", 9)

    assert rag_worker.answer_text_concurrency() == 2


def test_answer_worker_runs_questions_in_bounded_parallel(monkeypatch) -> None:
    job = SimpleNamespace(
        id=uuid.UUID("00000000-0000-0000-0000-000000000301"),
        owner_user_id=uuid.UUID("00000000-0000-0000-0000-000000000401"),
    )
    question_run_ids = [uuid.uuid4() for _ in range(5)]

    class HandleResult:
        def __init__(self, value):
            self.value = value

        def scalar_one(self):
            return self.value

        def all(self):
            return self.value

    class HandleSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.execute_calls = 0

        async def execute(self, statement):
            self.execute_calls += 1
            if self.execute_calls == 1:
                return HandleResult(job)
            return HandleResult([(question_run_id, index) for index, question_run_id in enumerate(question_run_ids)])

    class FakeCloseGateway:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(rag_worker.settings, "vllm_text_concurrency", 4)
    monkeypatch.setattr(rag_worker.settings, "vllm_text_http_max_connections", 2)
    gateway = FakeCloseGateway()
    monkeypatch.setattr(rag_worker, "VLLMGateway", lambda: gateway)

    worker = AnswerWorker()
    active = 0
    max_active = 0

    async def answer_one_question(**kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        async with kwargs["progress_lock"]:
            kwargs["progress"]["done"] += 1
        return {"answered_with_evidence": 1, "evidence_chunks": 1}

    async def no_op(*args, **kwargs):
        return None

    worker._answer_one_question = answer_one_question
    worker.emit_event = no_op
    worker.checkpoint_cancelled = no_op
    worker.enqueue = no_op

    asyncio.run(worker.handle(HandleSession(), {"job_id": str(job.id)}))

    assert max_active == 2
    assert gateway.closed is True


def test_answer_worker_uses_direct_strategy_when_evidence_fits(monkeypatch) -> None:
    monkeypatch.setattr(rag_worker.settings, "answer_context_max_chars", 10_000)
    events: list[dict] = []
    gateway = FakeGateway()

    answer, raw_response = asyncio.run(
        _worker_with_recorded_events(events)._answer_question_with_evidence(
            FakeSession(),
            _job(),
            gateway,
            question_run=_question_run(),
            question=_question(),
            evidence_chunks=[_chunk(1, "RAW-DIRECT")],
            questions_done=0,
            questions_total=1,
        )
    )

    assert answer == "direct answer from raw evidence"
    assert raw_response["strategy"] == "direct"
    assert raw_response["evidence_batch_count"] == 1
    assert len(gateway.calls) == 1
    assert "RAW-DIRECT" in gateway.calls[0][0]
    assert events == []


def test_answer_worker_uses_map_reduce_when_evidence_overflows(monkeypatch) -> None:
    monkeypatch.setattr(rag_worker.settings, "answer_context_max_chars", 350)
    events: list[dict] = []
    gateway = FakeGateway()

    answer, raw_response = asyncio.run(
        _worker_with_recorded_events(events)._answer_question_with_evidence(
            FakeSession(),
            _job(),
            gateway,
            question_run=_question_run(),
            question=_question(),
            evidence_chunks=[
                _chunk(1, "RAW-A " + "x" * 80),
                _chunk(2, "RAW-B " + "x" * 80),
                _chunk(3, "RAW-C " + "x" * 80),
            ],
            questions_done=0,
            questions_total=1,
        )
    )

    assert answer == "final answer from mapped summaries"
    assert raw_response["strategy"] == "map_reduce"
    assert raw_response["evidence_batch_count"] > 1
    assert raw_response["summary_chars"] > 0
    assert raw_response["prompt_chars"] == len(gateway.calls[-1][0])
    assert len(gateway.calls) == raw_response["evidence_batch_count"] + 1
    assert "Intermediate summaries:" in gateway.calls[-1][0]
    assert "RAW-A" not in gateway.calls[-1][0]
    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "answer.map_reduce.started"
    assert event_types[-1] == "answer.map_reduce.completed"
    assert event_types.count("answer.map_reduce.progress") == raw_response["evidence_batch_count"]

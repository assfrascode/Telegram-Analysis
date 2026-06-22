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
        if "Evidenz-Batch" in prompt:
            return f"mapped summary {len(self.calls)}"
        if "Zwischenzusammenfassungen:" in prompt:
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
    assert "Zwischenzusammenfassungen:" in gateway.calls[-1][0]
    assert "RAW-A" not in gateway.calls[-1][0]
    event_types = [event["event_type"] for event in events]
    assert event_types[0] == "answer.map_reduce.started"
    assert event_types[-1] == "answer.map_reduce.completed"
    assert event_types.count("answer.map_reduce.progress") == raw_response["evidence_batch_count"]

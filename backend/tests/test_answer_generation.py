
import uuid
from datetime import datetime, timezone

from app.services.answer_generation import (
    EvidenceChunk,
    build_answer_prompt,
    build_evidence_context,
    evidence_chunk_payload,
    make_short_answer,
    no_evidence_answer,
)


def test_build_evidence_context_preserves_chunk_metadata_and_text():
    chunk = EvidenceChunk(
        chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        chunk_index=7,
        text="[2024-01-01] [msg_id=1] Beispielnachricht\nIMAGE_DESCRIPTION:\nEin Bild.",
        message_ids=["1", "2"],
        rerank_rank=1,
        rerank_score=0.9,
        retrieval_rank=3,
        retrieval_score=0.7,
        start_timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
    )

    context = build_evidence_context([chunk])

    assert "[EVIDENCE_CHUNK rank=1 chunk_index=7" in context
    assert "message_ids=1,2" in context
    assert "IMAGE_DESCRIPTION" in context
    assert "[/EVIDENCE_CHUNK]" in context


def test_build_evidence_context_respects_max_chars():
    chunk = EvidenceChunk(
        chunk_id=uuid.uuid4(),
        chunk_index=1,
        text="x" * 1000,
        message_ids=["1"],
    )

    context = build_evidence_context([chunk], max_chars=300)

    assert len(context) <= 330
    assert "CONTEXT_TRUNCATED" in context


def test_build_answer_prompt_contains_question_and_context():
    prompt = build_answer_prompt("Welche Narrative?", "Evidence")

    assert "Welche Narrative?" in prompt
    assert "Evidence" in prompt
    assert "ausschließlich anhand der Evidenz" in prompt


def test_make_short_answer_truncates_deterministically():
    short = make_short_answer("A" * 400, max_chars=20)

    assert short == "A" * 19 + "…"


def test_no_evidence_answer_is_explicit():
    answer = no_evidence_answer("Frage?")

    assert "keine Evidenz-Chunks" in answer
    assert "Frage?" in answer


def test_evidence_chunk_payload_is_serializable():
    chunk = EvidenceChunk(
        chunk_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
        chunk_index=4,
        text="abc",
        message_ids=["10"],
        rerank_rank=2,
    )

    payload = evidence_chunk_payload(chunk)

    assert payload["chunk_id"] == "00000000-0000-0000-0000-000000000002"
    assert payload["chunk_index"] == 4
    assert payload["message_ids"] == ["10"]
    assert payload["text_chars"] == 3

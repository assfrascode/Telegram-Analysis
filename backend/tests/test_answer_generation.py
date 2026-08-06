
import uuid
from datetime import datetime, timezone

from app.services.answer_generation import (
    EvidenceChunk,
    build_evidence_batches,
    build_evidence_map_prompt,
    build_answer_prompt,
    build_evidence_context,
    build_reduce_answer_prompt,
    build_summary_batches,
    build_summary_reduce_prompt,
    evidence_chunk_payload,
    make_short_answer,
    no_evidence_answer,
)


def _chunk(index: int, text: str) -> EvidenceChunk:
    return EvidenceChunk(
        chunk_id=uuid.UUID(f"00000000-0000-0000-0000-{index:012d}"),
        chunk_index=index,
        text=text,
        message_ids=[str(index)],
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


def test_build_evidence_batches_single_batch_matches_context():
    chunks = [
        _chunk(1, "Erste Nachricht."),
        _chunk(2, "Zweite Nachricht."),
    ]

    context = build_evidence_context(chunks, max_chars=10_000)
    batches = build_evidence_batches(chunks, max_chars=10_000)

    assert len(batches) == 1
    assert batches[0].context == context
    assert batches[0].chunks == chunks
    assert not batches[0].truncated


def test_build_evidence_batches_splits_context_under_cap_in_order():
    chunks = [
        _chunk(1, "RAW-1 " + "x" * 60),
        _chunk(2, "RAW-2 " + "x" * 60),
        _chunk(3, "RAW-3 " + "x" * 60),
    ]

    batches = build_evidence_batches(chunks, max_chars=260)

    assert len(batches) > 1
    assert all(len(batch.context) <= 260 for batch in batches)
    assert [chunk.chunk_index for batch in batches for chunk in batch.chunks] == [1, 2, 3]
    assert "RAW-1" in batches[0].context
    assert "RAW-3" in batches[-1].context


def test_build_evidence_batches_truncates_oversized_single_chunk():
    batches = build_evidence_batches([_chunk(1, "x" * 1000)], max_chars=260)

    assert len(batches) == 1
    assert batches[0].truncated
    assert len(batches[0].context) <= 260
    assert "CONTEXT_TRUNCATED" in batches[0].context


def test_build_evidence_batches_token_budget_splits_without_truncating():
    batches = build_evidence_batches(
        [_chunk(1, "RAW-LONG " + "x" * 500)],
        max_tokens=240,
        token_counter=len,
    )

    assert len(batches) > 1
    assert all(len(batch.context) <= 240 for batch in batches)
    assert all(not batch.truncated for batch in batches)
    assert "CONTEXT_TRUNCATED" not in "\n".join(batch.context for batch in batches)
    assert "part=1/" in batches[0].context


def test_build_answer_prompt_contains_question_and_context():
    prompt = build_answer_prompt("Welche Narrative?", "Evidence")

    assert "Welche Narrative?" in prompt
    assert "Evidence" in prompt
    assert "using only the evidence chunks" in prompt
    assert "Answer in English" in prompt


def test_map_and_reduce_prompts_contain_question_and_summary_context():
    batch = build_evidence_batches([_chunk(1, "Belegtext.")], max_chars=10_000)[0]
    map_prompt = build_evidence_map_prompt("Welche Narrative?", batch, batch_count=1)

    assert "Welche Narrative?" in map_prompt
    assert "Evidence batch 1/1" in map_prompt
    assert "chunk_id=00000000-0000-0000-0000-000000000001" in map_prompt
    assert "Intermediate summary" in map_prompt

    summary_batch = build_summary_batches(["Zusammenfassung A", "Zusammenfassung B"], max_chars=10_000)[0]
    reduce_summary_prompt = build_summary_reduce_prompt(
        "Welche Narrative?",
        summary_batch,
        round_index=1,
        batch_count=1,
    )
    final_prompt = build_reduce_answer_prompt("Welche Narrative?", summary_batch.context)

    assert "Reduction round 1" in reduce_summary_prompt
    assert "Zusammenfassung A" in reduce_summary_prompt
    assert "Intermediate summaries" in final_prompt
    assert "Welche Narrative?" in final_prompt


def test_make_short_answer_truncates_deterministically():
    short = make_short_answer("A" * 400, max_chars=20)

    assert short == "A" * 19 + "…"


def test_no_evidence_answer_is_explicit():
    answer = no_evidence_answer("Frage?")

    assert "no evidence chunks" in answer
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

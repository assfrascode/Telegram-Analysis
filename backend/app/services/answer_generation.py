
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from app.config import get_settings
from app.llm.prompt_limits import count_text_tokens, split_text_by_tokens

settings = get_settings()


@dataclass(slots=True)
class EvidenceChunk:
    chunk_id: uuid.UUID
    chunk_index: int
    text: str
    message_ids: list[str]
    rerank_rank: int | None = None
    rerank_score: float | None = None
    retrieval_rank: int | None = None
    retrieval_score: float | None = None
    start_timestamp: datetime | None = None
    end_timestamp: datetime | None = None


@dataclass(slots=True)
class EvidenceBatch:
    batch_index: int
    chunks: list[EvidenceChunk]
    context: str
    truncated: bool = False


@dataclass(slots=True)
class SummaryBatch:
    batch_index: int
    context: str
    summary_indexes: list[int]
    truncated: bool = False


CONTEXT_TRUNCATED_MARKER = "[CONTEXT_TRUNCATED]"
SUMMARY_TRUNCATED_MARKER = "[SUMMARY_TRUNCATED]"


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.isoformat()


def evidence_chunk_payload(chunk: EvidenceChunk) -> dict[str, Any]:
    """Return compact serializable metadata for audit/debug storage."""
    return {
        "chunk_id": str(chunk.chunk_id),
        "chunk_index": chunk.chunk_index,
        "message_ids": list(chunk.message_ids or []),
        "rerank_rank": chunk.rerank_rank,
        "rerank_score": chunk.rerank_score,
        "retrieval_rank": chunk.retrieval_rank,
        "retrieval_score": chunk.retrieval_score,
        "start_timestamp": _format_timestamp(chunk.start_timestamp),
        "end_timestamp": _format_timestamp(chunk.end_timestamp),
        "text_chars": len(chunk.text or ""),
    }


def _truncate_with_marker(text: str, *, max_chars: int, marker: str) -> str:
    if max_chars <= 0:
        return ""
    marker_text = f"\n{marker}"
    if max_chars <= len(marker_text):
        return text[:max_chars].rstrip()
    return text[: max_chars - len(marker_text)].rstrip() + marker_text


def format_evidence_chunk(chunk: EvidenceChunk, *, fallback_rank: int) -> str:
    rank = chunk.rerank_rank or fallback_rank
    header = (
        f"[EVIDENCE_CHUNK rank={rank} chunk_index={chunk.chunk_index} "
        f"chunk_id={chunk.chunk_id} "
        f"time_range={_format_timestamp(chunk.start_timestamp)}..{_format_timestamp(chunk.end_timestamp)} "
        f"message_ids={','.join(chunk.message_ids or [])}]"
    )
    return f"{header}\n{(chunk.text or '').strip()}\n[/EVIDENCE_CHUNK]"


def _format_evidence_chunk_part(
    chunk: EvidenceChunk,
    *,
    fallback_rank: int,
    part_index: int,
    part_count: int,
    text: str,
) -> str:
    rank = chunk.rerank_rank or fallback_rank
    header = (
        f"[EVIDENCE_CHUNK rank={rank} chunk_index={chunk.chunk_index} "
        f"chunk_id={chunk.chunk_id} "
        f"time_range={_format_timestamp(chunk.start_timestamp)}..{_format_timestamp(chunk.end_timestamp)} "
        f"message_ids={','.join(chunk.message_ids or [])} part={part_index}/{part_count}]"
    )
    return f"{header}\n{(text or '').strip()}\n[/EVIDENCE_CHUNK]"


def _token_count(text: str, token_counter: Callable[[str], int] | None = None) -> int:
    if token_counter is not None:
        return token_counter(text)
    return count_text_tokens(text, model=settings.text_model)


def _evidence_blocks_for_token_budget(
    chunk: EvidenceChunk,
    *,
    fallback_rank: int,
    max_tokens: int,
    token_counter: Callable[[str], int] | None = None,
) -> list[tuple[str, bool]]:
    block = format_evidence_chunk(chunk, fallback_rank=fallback_rank)
    if _token_count(block, token_counter) <= max_tokens:
        return [(block, False)]

    marker_overhead = _token_count(
        _format_evidence_chunk_part(
            chunk,
            fallback_rank=fallback_rank,
            part_index=1,
            part_count=999,
            text="",
        ),
        token_counter,
    )
    text_budget = max_tokens - marker_overhead - 8
    if text_budget <= 0:
        raise ValueError("Evidence token budget is too small for chunk metadata")

    while text_budget > 0:
        parts = split_text_by_tokens(chunk.text or "", text_budget, model=settings.text_model)
        part_count = len(parts)
        blocks = [
            _format_evidence_chunk_part(
                chunk,
                fallback_rank=fallback_rank,
                part_index=index,
                part_count=part_count,
                text=part,
            )
            for index, part in enumerate(parts, start=1)
        ]
        if all(_token_count(item, token_counter) <= max_tokens for item in blocks):
            return [(item, True) for item in blocks]
        text_budget -= 8
    raise ValueError("Evidence token budget is too small for split chunk text")


def build_evidence_batches(
    chunks: list[EvidenceChunk],
    *,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> list[EvidenceBatch]:
    """Split evidence into ordered context batches below ``max_chars``.

    Oversized single chunks are truncated inside their own batch so the caller can
    still map-reduce from the best available evidence without sending an
    unbounded prompt.
    """
    if not chunks:
        return []

    token_cap = max_tokens if max_tokens and max_tokens > 0 else None
    if token_cap is not None:
        batches: list[EvidenceBatch] = []
        current_blocks: list[str] = []
        current_chunks: list[EvidenceChunk] = []
        current_tokens = 0
        separator = "\n\n"
        separator_tokens = _token_count(separator, token_counter)

        def flush_current() -> None:
            nonlocal current_blocks, current_chunks, current_tokens
            if not current_blocks:
                return
            batches.append(
                EvidenceBatch(
                    batch_index=len(batches) + 1,
                    chunks=list(current_chunks),
                    context=separator.join(current_blocks).strip(),
                )
            )
            current_blocks = []
            current_chunks = []
            current_tokens = 0

        for fallback_rank, chunk in enumerate(chunks, start=1):
            for block, was_split in _evidence_blocks_for_token_budget(
                chunk,
                fallback_rank=fallback_rank,
                max_tokens=token_cap,
                token_counter=token_counter,
            ):
                block_tokens = _token_count(block, token_counter)
                if block_tokens > token_cap:
                    raise ValueError("Evidence block exceeds token budget after splitting")
                extra = separator_tokens if current_blocks else 0
                if current_blocks and current_tokens + extra + block_tokens > token_cap:
                    flush_current()
                    extra = 0
                current_blocks.append(block)
                current_chunks.append(chunk)
                current_tokens += extra + block_tokens
                if was_split:
                    # Split blocks are complete continuations, not truncations.
                    continue

        flush_current()
        return batches

    cap = max_chars if max_chars and max_chars > 0 else None
    if cap is None:
        context = "\n\n".join(
            format_evidence_chunk(chunk, fallback_rank=fallback_rank)
            for fallback_rank, chunk in enumerate(chunks, start=1)
        ).strip()
        return [EvidenceBatch(batch_index=1, chunks=list(chunks), context=context)]

    batches: list[EvidenceBatch] = []
    current_blocks: list[str] = []
    current_chunks: list[EvidenceChunk] = []
    current_chars = 0

    def flush_current() -> None:
        nonlocal current_blocks, current_chunks, current_chars
        if not current_blocks:
            return
        batches.append(
            EvidenceBatch(
                batch_index=len(batches) + 1,
                chunks=current_chunks,
                context="\n\n".join(current_blocks).strip(),
            )
        )
        current_blocks = []
        current_chunks = []
        current_chars = 0

    for fallback_rank, chunk in enumerate(chunks, start=1):
        block = format_evidence_chunk(chunk, fallback_rank=fallback_rank)
        if len(block) > cap:
            flush_current()
            batches.append(
                EvidenceBatch(
                    batch_index=len(batches) + 1,
                    chunks=[chunk],
                    context=_truncate_with_marker(
                        block,
                        max_chars=cap,
                        marker=CONTEXT_TRUNCATED_MARKER,
                    ),
                    truncated=True,
                )
            )
            continue

        separator_chars = 2 if current_blocks else 0
        if current_blocks and current_chars + separator_chars + len(block) > cap:
            flush_current()
            separator_chars = 0

        current_blocks.append(block)
        current_chunks.append(chunk)
        current_chars += separator_chars + len(block)

    flush_current()
    return batches


def build_evidence_context(
    chunks: list[EvidenceChunk],
    *,
    max_chars: int | None = None,
) -> str:
    """Render reranked chunks into the context block used by answer generation.

    The full chunk text is preserved unless ``max_chars`` is reached. A hard cap
    avoids accidentally sending very large prompts when users configure high
    retrieval/rerank values.
    """
    parts: list[str] = []
    used_chars = 0
    cap = max_chars if max_chars and max_chars > 0 else None

    for fallback_rank, chunk in enumerate(chunks, start=1):
        block = format_evidence_chunk(chunk, fallback_rank=fallback_rank)
        if cap is not None and used_chars + len(block) > cap:
            remaining = cap - used_chars
            if remaining <= 200:
                break
            block = _truncate_with_marker(
                block,
                max_chars=remaining,
                marker=CONTEXT_TRUNCATED_MARKER,
            )
            parts.append(block)
            break
        parts.append(block)
        used_chars += len(block) + 2

    return "\n\n".join(parts).strip()


def build_answer_prompt(question: str, context: str) -> str:
    """Build the user prompt body passed to the text model.

    ``VLLMGateway.answer_question`` wraps this with a system message. Keeping the
    prompt body in a helper makes the worker deterministic and testable.
    """
    return (
        "Answer the following question using only the evidence chunks.\n"
        "Do not use external information. State clearly when the evidence is insufficient.\n"
        "Briefly cite the key evidence and note contradictions or weak evidence when relevant.\n"
        "Answer in English even when the question or evidence is in another language.\n\n"
        f"Question:\n{question.strip()}\n\n"
        f"Evidence chunks:\n{context.strip()}\n\n"
        "Answer:"
    )


def build_evidence_map_prompt(
    question: str,
    batch: EvidenceBatch,
    *,
    batch_count: int,
) -> str:
    return (
        "Summarize the following evidence chunks for a later answer.\n"
        "Use only this evidence. Preserve key facts, chunk IDs, message IDs, time ranges, "
        "contradictions, weak evidence, and uncertainty.\n"
        "Be concise but complete enough to answer the original question from this summary alone.\n"
        "Write the summary in English.\n\n"
        f"Original question:\n{question.strip()}\n\n"
        f"Evidence batch {batch.batch_index}/{batch_count}:\n{batch.context.strip()}\n\n"
        "Intermediate summary:"
    )


def _format_summary_block(summary: str, *, summary_index: int) -> str:
    return (
        f"[INTERMEDIATE_SUMMARY index={summary_index}]\n"
        f"{(summary or '').strip()}\n"
        "[/INTERMEDIATE_SUMMARY]"
    )


def build_summary_batches(
    summaries: list[str],
    *,
    max_chars: int | None = None,
    max_tokens: int | None = None,
    token_counter: Callable[[str], int] | None = None,
) -> list[SummaryBatch]:
    if not summaries:
        return []

    token_cap = max_tokens if max_tokens and max_tokens > 0 else None
    if token_cap is not None:
        batches: list[SummaryBatch] = []
        current_blocks: list[str] = []
        current_indexes: list[int] = []
        current_tokens = 0
        separator = "\n\n"
        separator_tokens = _token_count(separator, token_counter)

        def flush_current() -> None:
            nonlocal current_blocks, current_indexes, current_tokens
            if not current_blocks:
                return
            batches.append(
                SummaryBatch(
                    batch_index=len(batches) + 1,
                    context=separator.join(current_blocks).strip(),
                    summary_indexes=list(current_indexes),
                )
            )
            current_blocks = []
            current_indexes = []
            current_tokens = 0

        for index, summary in enumerate(summaries, start=1):
            block = _format_summary_block(summary, summary_index=index)
            block_tokens = _token_count(block, token_counter)
            if block_tokens > token_cap:
                overhead = _token_count(_format_summary_block("", summary_index=index), token_counter)
                text_budget = token_cap - overhead - 8
                if text_budget <= 0:
                    raise ValueError("Summary token budget is too small for summary metadata")
                while text_budget > 0:
                    parts = split_text_by_tokens(summary, text_budget, model=settings.text_model)
                    blocks = [_format_summary_block(part, summary_index=index) for part in parts]
                    if all(_token_count(item, token_counter) <= token_cap for item in blocks):
                        break
                    text_budget -= 8
                else:
                    raise ValueError("Summary token budget is too small for split summary text")
            else:
                blocks = [block]

            for item in blocks:
                item_tokens = _token_count(item, token_counter)
                if item_tokens > token_cap:
                    raise ValueError("Summary block exceeds token budget after splitting")
                extra = separator_tokens if current_blocks else 0
                if current_blocks and current_tokens + extra + item_tokens > token_cap:
                    flush_current()
                    extra = 0
                current_blocks.append(item)
                current_indexes.append(index)
                current_tokens += extra + item_tokens

        flush_current()
        return batches

    cap = max_chars if max_chars and max_chars > 0 else None
    if cap is None:
        context = "\n\n".join(
            _format_summary_block(summary, summary_index=index)
            for index, summary in enumerate(summaries, start=1)
        ).strip()
        return [
            SummaryBatch(
                batch_index=1,
                context=context,
                summary_indexes=list(range(1, len(summaries) + 1)),
            )
        ]

    batches: list[SummaryBatch] = []
    current_blocks: list[str] = []
    current_indexes: list[int] = []
    current_chars = 0

    def flush_current() -> None:
        nonlocal current_blocks, current_indexes, current_chars
        if not current_blocks:
            return
        batches.append(
            SummaryBatch(
                batch_index=len(batches) + 1,
                context="\n\n".join(current_blocks).strip(),
                summary_indexes=current_indexes,
            )
        )
        current_blocks = []
        current_indexes = []
        current_chars = 0

    for index, summary in enumerate(summaries, start=1):
        block = _format_summary_block(summary, summary_index=index)
        if len(block) > cap:
            flush_current()
            batches.append(
                SummaryBatch(
                    batch_index=len(batches) + 1,
                    context=_truncate_with_marker(
                        block,
                        max_chars=cap,
                        marker=SUMMARY_TRUNCATED_MARKER,
                    ),
                    summary_indexes=[index],
                    truncated=True,
                )
            )
            continue

        separator_chars = 2 if current_blocks else 0
        if current_blocks and current_chars + separator_chars + len(block) > cap:
            flush_current()
            separator_chars = 0

        current_blocks.append(block)
        current_indexes.append(index)
        current_chars += separator_chars + len(block)

    flush_current()
    return batches


def build_summary_reduce_prompt(
    question: str,
    summary_batch: SummaryBatch,
    *,
    round_index: int,
    batch_count: int,
) -> str:
    return (
        "Condense the following intermediate summaries for another reduction round.\n"
        "Use only the provided summaries. Preserve facts, chunk IDs, message IDs, time ranges, "
        "contradictions, and uncertainty.\n"
        "Do not introduce new evidence or conclusions. Write in English.\n\n"
        f"Original question:\n{question.strip()}\n\n"
        f"Reduction round {round_index}, batch {summary_batch.batch_index}/{batch_count}:\n"
        f"{summary_batch.context.strip()}\n\n"
        "Condensed intermediate summary:"
    )


def build_reduce_answer_prompt(question: str, summary_context: str) -> str:
    return (
        "Answer the following question using only the intermediate summaries.\n"
        "Do not use external information. State clearly when the summaries are insufficient.\n"
        "Briefly cite the key evidence and note contradictions or weak evidence when relevant.\n"
        "Answer in English even when the question or source evidence is in another language.\n\n"
        f"Question:\n{question.strip()}\n\n"
        f"Intermediate summaries:\n{summary_context.strip()}\n\n"
        "Answer:"
    )


def make_short_answer(answer: str, *, max_chars: int = 320) -> str:
    """Create a deterministic short answer for report cards.

    This avoids a second LLM call in the MVP while keeping the summary stable.
    """
    normalized = " ".join((answer or "").split())
    if not normalized:
        return "No answer was generated."
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)].rstrip() + "…"


def no_evidence_answer(question: str) -> str:
    return (
        "The question cannot be answered reliably from the retrieval and reranking results "
        "because no evidence chunks were selected as answer context.\n\n"
        f"Question: {question.strip()}"
    )


import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import get_settings

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


def build_evidence_batches(
    chunks: list[EvidenceChunk],
    *,
    max_chars: int | None = None,
) -> list[EvidenceBatch]:
    """Split evidence into ordered context batches below ``max_chars``.

    Oversized single chunks are truncated inside their own batch so the caller can
    still map-reduce from the best available evidence without sending an
    unbounded prompt.
    """
    if not chunks:
        return []

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
        "Beantworte die folgende Frage ausschließlich anhand der Evidenz-Chunks.\n"
        "Nutze keine externen Informationen. Wenn die Evidenz nicht ausreicht, sage das explizit.\n"
        "Erwähne zentrale Belege knapp und widersprüchliche oder schwache Evidenz, falls vorhanden.\n\n"
        f"Frage:\n{question.strip()}\n\n"
        f"Evidenz-Chunks:\n{context.strip()}\n\n"
        "Antwort:"
    )


def build_evidence_map_prompt(
    question: str,
    batch: EvidenceBatch,
    *,
    batch_count: int,
) -> str:
    return (
        "Fasse die folgenden Evidenz-Chunks als Zwischenzusammenfassung für die spätere "
        "Fragenbeantwortung zusammen.\n"
        "Nutze ausschließlich diese Evidenz. Erhalte zentrale Fakten, Chunk-IDs, Message-IDs, "
        "Zeitbereiche, Widersprüche, schwache Evidenz und Unsicherheiten.\n"
        "Schreibe knapp, aber vollständig genug, damit die Ausgangsfrage später nur anhand "
        "dieser Zwischenzusammenfassung beantwortet werden kann.\n\n"
        f"Ausgangsfrage:\n{question.strip()}\n\n"
        f"Evidenz-Batch {batch.batch_index}/{batch_count}:\n{batch.context.strip()}\n\n"
        "Zwischenzusammenfassung:"
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
) -> list[SummaryBatch]:
    if not summaries:
        return []

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
        "Verdichte die folgenden Zwischenzusammenfassungen für eine weitere Reduktionsrunde.\n"
        "Nutze ausschließlich die bereitgestellten Zwischenzusammenfassungen. Erhalte Fakten, "
        "Chunk-IDs, Message-IDs, Zeitbereiche, Widersprüche und Unsicherheiten.\n"
        "Erfinde keine neuen Belege oder Schlussfolgerungen.\n\n"
        f"Ausgangsfrage:\n{question.strip()}\n\n"
        f"Reduktionsrunde {round_index}, Batch {summary_batch.batch_index}/{batch_count}:\n"
        f"{summary_batch.context.strip()}\n\n"
        "Verdichtete Zwischenzusammenfassung:"
    )


def build_reduce_answer_prompt(question: str, summary_context: str) -> str:
    return (
        "Beantworte die folgende Frage ausschließlich anhand der Zwischenzusammenfassungen.\n"
        "Nutze keine externen Informationen. Wenn die Zusammenfassungen nicht ausreichen, sage "
        "das explizit.\n"
        "Erwähne zentrale Belege knapp und widersprüchliche oder schwache Evidenz, falls vorhanden.\n\n"
        f"Frage:\n{question.strip()}\n\n"
        f"Zwischenzusammenfassungen:\n{summary_context.strip()}\n\n"
        "Antwort:"
    )


def make_short_answer(answer: str, *, max_chars: int = 320) -> str:
    """Create a deterministic short answer for report cards.

    This avoids a second LLM call in the MVP while keeping the summary stable.
    """
    normalized = " ".join((answer or "").split())
    if not normalized:
        return "Keine Antwort erzeugt."
    if len(normalized) <= max_chars:
        return normalized
    return normalized[: max(0, max_chars - 1)].rstrip() + "…"


def no_evidence_answer(question: str) -> str:
    return (
        "Die Frage kann auf Basis der Retrieval-/Reranking-Ergebnisse nicht belastbar beantwortet werden, "
        "weil keine Evidenz-Chunks als Antwortkontext markiert wurden.\n\n"
        f"Frage: {question.strip()}"
    )


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
        rank = chunk.rerank_rank or fallback_rank
        header = (
            f"[EVIDENCE_CHUNK rank={rank} chunk_index={chunk.chunk_index} "
            f"chunk_id={chunk.chunk_id} "
            f"time_range={_format_timestamp(chunk.start_timestamp)}..{_format_timestamp(chunk.end_timestamp)} "
            f"message_ids={','.join(chunk.message_ids or [])}]"
        )
        block = f"{header}\n{(chunk.text or '').strip()}\n[/EVIDENCE_CHUNK]"
        if cap is not None and used_chars + len(block) > cap:
            remaining = cap - used_chars
            if remaining <= 200:
                break
            block = block[:remaining].rstrip() + "\n[CONTEXT_TRUNCATED]"
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

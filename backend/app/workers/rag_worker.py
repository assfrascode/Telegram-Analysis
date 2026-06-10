
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.embedding_client import EmbeddingClient
from app.llm.reranker_client import RerankerClient
from app.llm.vllm_gateway import VLLMGateway
from app.models import Job, MessageChunk, Question, QuestionRun, RetrievalHit, StepStatus
from app.services.answer_generation import (
    EvidenceChunk,
    build_answer_prompt,
    build_evidence_context,
    evidence_chunk_payload,
    make_short_answer,
    no_evidence_answer,
)
from app.services.qdrant_index import QdrantIndex
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(parsed, maximum))


def retrieval_k_for_job(job: Job) -> int:
    """Return the user-selected retrieval_k for this job.

    The frontend submits this value in ``job.options``. ``settings.default_retrieval_k``
    is only a fallback for old jobs or malformed local test payloads.
    """
    return _bounded_int(
        (job.options or {}).get("retrieval_k"),
        default=settings.default_retrieval_k,
        minimum=1,
        maximum=1000,
    )


def rerank_k_for_job(job: Job) -> int:
    return _bounded_int(
        (job.options or {}).get("rerank_k"),
        default=settings.default_rerank_k,
        minimum=1,
        maximum=1000,
    )


def _chunk_id_from_qdrant_hit(hit: dict[str, Any]) -> uuid.UUID | None:
    payload = hit.get("payload") if isinstance(hit.get("payload"), dict) else {}
    raw_chunk_id = payload.get("chunk_id") or hit.get("id")
    if raw_chunk_id is None:
        return None
    try:
        return uuid.UUID(str(raw_chunk_id))
    except (TypeError, ValueError):
        return None


def _sanitize_rerank_results(results: list[dict[str, Any]], document_count: int) -> list[dict[str, Any]]:
    """Return valid, de-duplicated reranker results sorted by score descending.

    Reranker backends are not perfectly consistent. This helper accepts the
    normalized response from ``RerankerClient``, removes invalid indexes and
    duplicate indexes, and keeps a deterministic ordering by score with the
    original response order as tie-breaker.
    """
    sanitized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for position, item in enumerate(results or []):
        try:
            index = int(item.get("index"))
        except (AttributeError, TypeError, ValueError):
            continue
        if index < 0 or index >= document_count or index in seen:
            continue
        seen.add(index)
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        sanitized.append({"index": index, "score": score, "position": position})

    return sorted(sanitized, key=lambda item: (-item["score"], item["position"]))


def _rerank_summary_payload(
    *,
    question_run: QuestionRun,
    retrieved_count: int,
    ranked_count: int,
    used_count: int,
) -> dict[str, Any]:
    return {
        "question_run_id": str(question_run.id),
        "retrieval_k": question_run.retrieval_k,
        "rerank_k": question_run.rerank_k,
        "retrieved_hits": retrieved_count,
        "ranked_hits": ranked_count,
        "used_in_answer": used_count,
        "reranker_model": settings.reranker_model,
    }


async def _delete_existing_question_runs(session: AsyncSession, job_id: uuid.UUID) -> None:
    """Remove previous retrieval/rerank/answer state for an idempotent rebuild."""
    run_ids = list(
        (
            await session.execute(select(QuestionRun.id).where(QuestionRun.job_id == job_id))
        )
        .scalars()
        .all()
    )
    if run_ids:
        await session.execute(delete(RetrievalHit).where(RetrievalHit.question_run_id.in_(run_ids)))
    await session.execute(delete(QuestionRun).where(QuestionRun.job_id == job_id))


class RetrieveWorker(Worker):
    subject = subjects.QUESTION_RETRIEVE
    durable = "retrieve-worker"
    queue = "retrieve"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        retrieval_k = retrieval_k_for_job(job)
        rerank_k = rerank_k_for_job(job)

        questions = list(
            (
                await session.execute(
                    select(Question).where(Question.job_id == job.id).order_by(Question.question_index)
                )
            )
            .scalars()
            .all()
        )

        await self.emit_event(
            session,
            job=job,
            event_type="retrieval.started",
            message="Retrieval für Fragen gestartet",
            payload={
                "questions_total": len(questions),
                "retrieval_k": retrieval_k,
                "rerank_k": rerank_k,
                "embedding_model": settings.embedding_model,
                "qdrant_collection": settings.qdrant_collection,
            },
        )
        await session.commit()

        await _delete_existing_question_runs(session, job.id)
        await session.flush()

        if not questions:
            await self.emit_event(
                session,
                job=job,
                event_type="retrieval.completed",
                message="Keine Fragen vorhanden; Retrieval übersprungen",
                payload={"questions_total": 0, "questions_done": 0, "retrieval_k": retrieval_k},
            )
            await session.commit()
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="retrieval.cancelled",
                message="Retrieval wegen Job-Abbruch nicht weitergeführt",
                payload={"questions_total": 0},
            )
            await self.enqueue(
                subjects.QUESTION_RERANK,
                {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"rerank:{job.id}"},
            )
            return

        embedder = EmbeddingClient()
        qdrant = QdrantIndex()
        question_vectors = await embedder.embed([question.text for question in questions])
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="retrieval.cancelled",
            message="Retrieval nach Frage-Embedding wegen Job-Abbruch beendet",
            payload={"questions_total": len(questions)},
        )
        if len(question_vectors) != len(questions):
            raise RuntimeError(
                f"Embedding endpoint returned {len(question_vectors)} vectors for {len(questions)} questions"
            )

        done = 0
        total_hits = 0
        for question, vector in zip(questions, question_vectors, strict=True):
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="retrieval.cancelled",
                    message="Retrieval wegen Job-Abbruch beendet",
                    payload={"questions_done": done, "questions_total": len(questions)},
                    level="warning",
                )
                await session.commit()
                return

            question_run = QuestionRun(
                question_id=question.id,
                job_id=job.id,
                retrieval_k=retrieval_k,
                rerank_k=rerank_k,
                status=StepStatus.running,
                raw_response={
                    "stage": "retrieval",
                    "retrieval_k": retrieval_k,
                    "rerank_k": rerank_k,
                    "embedding_model": settings.embedding_model,
                    "qdrant_collection": settings.qdrant_collection,
                },
            )
            session.add(question_run)
            await session.flush()

            hits = await qdrant.search(vector=vector, job_id=job.id, limit=retrieval_k, with_payload=True)
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="retrieval.cancelled",
                message="Retrieval nach Qdrant-Suche wegen Job-Abbruch beendet",
                payload={"questions_done": done, "questions_total": len(questions)},
            )
            seen_chunk_ids: set[uuid.UUID] = set()
            inserted_hits = 0

            for hit in hits:
                chunk_id = _chunk_id_from_qdrant_hit(hit)
                if chunk_id is None or chunk_id in seen_chunk_ids:
                    continue

                chunk_exists = (
                    await session.execute(
                        select(MessageChunk.id).where(
                            MessageChunk.id == chunk_id,
                            MessageChunk.job_id == job.id,
                        )
                    )
                ).scalar_one_or_none()
                if chunk_exists is None:
                    continue

                seen_chunk_ids.add(chunk_id)
                inserted_hits += 1
                session.add(
                    RetrievalHit(
                        question_run_id=question_run.id,
                        chunk_id=chunk_id,
                        retrieval_rank=inserted_hits,
                        retrieval_score=hit.get("score"),
                        used_in_answer=False,
                    )
                )

            done += 1
            total_hits += inserted_hits
            question_run.raw_response = {
                **(question_run.raw_response or {}),
                "retrieved_hits": inserted_hits,
                "qdrant_returned_hits": len(hits),
            }
            await self.emit_event(
                session,
                job=job,
                event_type="retrieval.progress",
                message=(
                    f"Frage {question.question_index}/{len(questions)}: "
                    f"{inserted_hits} Chunks mit retrieval_k={retrieval_k} gefunden"
                ),
                payload={
                    "questions_done": done,
                    "questions_total": len(questions),
                    "question_id": str(question.id),
                    "question_index": question.question_index,
                    "hits": inserted_hits,
                    "retrieval_k": retrieval_k,
                },
            )
            await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="retrieval.cancelled",
            message="Retrieval wegen Job-Abbruch beendet",
            payload={"questions_done": done, "questions_total": len(questions)},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="retrieval.completed",
            message="Retrieval abgeschlossen",
            payload={
                "questions_done": done,
                "questions_total": len(questions),
                "retrieval_k": retrieval_k,
                "total_hits": total_hits,
            },
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="retrieval.cancelled",
            message="Retrieval nach Abschluss wegen Job-Abbruch nicht weitergeführt",
            payload={"questions_done": done, "questions_total": len(questions)},
        )

        await self.enqueue(
            subjects.QUESTION_RERANK,
            {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"rerank:{job.id}"},
        )


class RerankWorker(Worker):
    subject = subjects.QUESTION_RERANK
    durable = "rerank-worker"
    queue = "rerank"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        run_rows = (
            (
                await session.execute(
                    select(QuestionRun, Question)
                    .join(Question, Question.id == QuestionRun.question_id)
                    .where(QuestionRun.job_id == job.id)
                    .order_by(Question.question_index)
                )
            )
            .all()
        )

        await self.emit_event(
            session,
            job=job,
            event_type="reranking.started",
            message="Reranking gestartet",
            payload={
                "questions_total": len(run_rows),
                "reranker_model": settings.reranker_model,
            },
        )
        await session.commit()

        reranker = RerankerClient()
        done = 0
        total_used = 0
        total_ranked = 0

        for question_run, question in run_rows:
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="reranking.cancelled",
                    message="Reranking wegen Job-Abbruch beendet",
                    payload={"questions_done": done, "questions_total": len(run_rows)},
                    level="warning",
                )
                await session.commit()
                return

            question_run.status = StepStatus.running

            hit_rows = (
                (
                    await session.execute(
                        select(RetrievalHit, MessageChunk)
                        .join(MessageChunk, MessageChunk.id == RetrievalHit.chunk_id)
                        .where(RetrievalHit.question_run_id == question_run.id)
                        .order_by(RetrievalHit.retrieval_rank)
                    )
                )
                .all()
            )

            hits: list[RetrievalHit] = [row[0] for row in hit_rows]
            chunks: list[MessageChunk] = [row[1] for row in hit_rows]

            for hit in hits:
                hit.used_in_answer = False
                hit.rerank_rank = None
                hit.rerank_score = None

            if not hits:
                question_run.status = StepStatus.completed
                question_run.raw_response = {
                    **(question_run.raw_response or {}),
                    "stage": "reranking",
                    "reranking": _rerank_summary_payload(
                        question_run=question_run,
                        retrieved_count=0,
                        ranked_count=0,
                        used_count=0,
                    ),
                }
                done += 1
                await self.emit_event(
                    session,
                    job=job,
                    event_type="reranking.progress",
                    message=f"Frage {question.question_index}/{len(run_rows)}: keine Retrieval-Treffer vorhanden",
                    payload={
                        "questions_done": done,
                        "questions_total": len(run_rows),
                        "question_id": str(question.id),
                        "question_index": question.question_index,
                        "retrieved_hits": 0,
                        "ranked_hits": 0,
                        "used_in_answer": 0,
                    },
                )
                await session.commit()
                continue

            documents = [chunk.text for chunk in chunks]
            raw_ranked = await reranker.rerank(question.text, documents)
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="reranking.cancelled",
                message="Reranking nach Modellaufruf wegen Job-Abbruch beendet",
                payload={"questions_done": done, "questions_total": len(run_rows)},
            )
            ranked = _sanitize_rerank_results(raw_ranked, len(documents))
            if not ranked:
                raise RuntimeError(f"Reranker returned no valid scores for question_run={question_run.id}")

            used_count = 0
            for rank, item in enumerate(ranked, start=1):
                hit = hits[item["index"]]
                hit.rerank_rank = rank
                hit.rerank_score = item["score"]
                if rank <= question_run.rerank_k:
                    hit.used_in_answer = True
                    used_count += 1

            question_run.status = StepStatus.completed
            question_run.raw_response = {
                **(question_run.raw_response or {}),
                "stage": "reranking",
                "reranking": _rerank_summary_payload(
                    question_run=question_run,
                    retrieved_count=len(hits),
                    ranked_count=len(ranked),
                    used_count=used_count,
                ),
            }

            done += 1
            total_used += used_count
            total_ranked += len(ranked)
            await self.emit_event(
                session,
                job=job,
                event_type="reranking.progress",
                message=(
                    f"Frage {question.question_index}/{len(run_rows)}: "
                    f"{used_count} von {len(hits)} Retrieval-Treffern für Antwortkontext ausgewählt"
                ),
                payload={
                    "questions_done": done,
                    "questions_total": len(run_rows),
                    "question_id": str(question.id),
                    "question_index": question.question_index,
                    "question_run_id": str(question_run.id),
                    "retrieved_hits": len(hits),
                    "ranked_hits": len(ranked),
                    "used_in_answer": used_count,
                    "rerank_k": question_run.rerank_k,
                },
            )
            await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="reranking.cancelled",
            message="Reranking wegen Job-Abbruch beendet",
            payload={"questions_done": done, "questions_total": len(run_rows)},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="reranking.completed",
            message="Reranking abgeschlossen",
            payload={
                "questions_done": done,
                "questions_total": len(run_rows),
                "ranked_hits": total_ranked,
                "used_in_answer": total_used,
            },
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="reranking.cancelled",
            message="Reranking nach Abschluss wegen Job-Abbruch nicht weitergeführt",
            payload={"questions_done": done, "questions_total": len(run_rows)},
        )

        await self.enqueue(
            subjects.QUESTION_ANSWER,
            {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"answer:{job.id}"},
        )


class AnswerWorker(Worker):
    subject = subjects.QUESTION_ANSWER
    durable = "answer-worker"
    queue = "answer"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        run_rows = (
            (
                await session.execute(
                    select(QuestionRun, Question)
                    .join(Question, Question.id == QuestionRun.question_id)
                    .where(QuestionRun.job_id == job.id)
                    .order_by(Question.question_index)
                )
            )
            .all()
        )

        await self.emit_event(
            session,
            job=job,
            event_type="answer.started",
            message="Fragenbeantwortung gestartet",
            payload={
                "questions_total": len(run_rows),
                "text_model": settings.text_model,
                "mock_enabled": settings.llm_mock_enabled,
            },
        )
        await session.commit()

        gateway = VLLMGateway()
        done = 0
        answered = 0
        total_evidence_chunks = 0

        for question_run, question in run_rows:
            if await self.should_skip_cancelled(session, job.id):
                await self.emit_event(
                    session,
                    job=job,
                    event_type="answer.cancelled",
                    message="Fragenbeantwortung wegen Job-Abbruch beendet",
                    payload={"questions_done": done, "questions_total": len(run_rows)},
                    level="warning",
                )
                await session.commit()
                return

            question_run.status = StepStatus.running
            await session.flush()

            hit_rows = (
                (
                    await session.execute(
                        select(RetrievalHit, MessageChunk)
                        .join(MessageChunk, MessageChunk.id == RetrievalHit.chunk_id)
                        .where(
                            RetrievalHit.question_run_id == question_run.id,
                            RetrievalHit.used_in_answer.is_(True),
                        )
                        .order_by(RetrievalHit.rerank_rank, RetrievalHit.retrieval_rank)
                    )
                )
                .all()
            )

            evidence_chunks = [
                EvidenceChunk(
                    chunk_id=chunk.id,
                    chunk_index=chunk.chunk_index,
                    text=chunk.text,
                    message_ids=list(chunk.message_ids or []),
                    rerank_rank=hit.rerank_rank,
                    rerank_score=hit.rerank_score,
                    retrieval_rank=hit.retrieval_rank,
                    retrieval_score=hit.retrieval_score,
                    start_timestamp=chunk.start_timestamp,
                    end_timestamp=chunk.end_timestamp,
                )
                for hit, chunk in hit_rows
            ]

            context = build_evidence_context(
                evidence_chunks,
                max_chars=settings.answer_context_max_chars,
            )
            prompt_body = build_answer_prompt(question.text, context) if context else ""

            if not context:
                answer = no_evidence_answer(question.text)
                raw_response = {
                    "stage": "answer",
                    "mock": settings.llm_mock_enabled,
                    "text_model": settings.text_model,
                    "evidence_chunks": [],
                    "context_chars": 0,
                    "prompt_chars": 0,
                    "skipped_llm_reason": "no_used_evidence_chunks",
                }
            else:
                answer = await gateway.answer_prompt(prompt_body)
                await self.checkpoint_cancelled(
                    session,
                    job,
                    event_type="answer.cancelled",
                    message="Fragenbeantwortung nach Modellaufruf wegen Job-Abbruch beendet",
                    payload={"questions_done": done, "questions_total": len(run_rows)},
                )
                raw_response = {
                    "stage": "answer",
                    "mock": settings.llm_mock_enabled,
                    "text_model": settings.text_model,
                    "evidence_chunks": [evidence_chunk_payload(chunk) for chunk in evidence_chunks],
                    "context_chars": len(context),
                    "prompt_chars": len(prompt_body),
                    "answer_chars": len(answer),
                }
                answered += 1

            question_run.answer = answer
            question_run.short_answer = make_short_answer(answer)
            question_run.status = StepStatus.completed
            question_run.raw_response = {
                **(question_run.raw_response or {}),
                "answer": raw_response,
            }

            done += 1
            total_evidence_chunks += len(evidence_chunks)
            await self.emit_event(
                session,
                job=job,
                event_type="answer.progress",
                message=(
                    f"Frage {question.question_index}/{len(run_rows)} beantwortet "
                    f"mit {len(evidence_chunks)} Evidenz-Chunks"
                ),
                payload={
                    "questions_done": done,
                    "questions_total": len(run_rows),
                    "question_id": str(question.id),
                    "question_index": question.question_index,
                    "question_run_id": str(question_run.id),
                    "evidence_chunks": len(evidence_chunks),
                    "answer_chars": len(answer),
                },
            )
            await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="answer.cancelled",
            message="Fragenbeantwortung wegen Job-Abbruch beendet",
            payload={"questions_done": done, "questions_total": len(run_rows)},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="answer.completed",
            message="Fragenbeantwortung abgeschlossen",
            payload={
                "questions_done": done,
                "questions_total": len(run_rows),
                "answered_with_evidence": answered,
                "evidence_chunks_total": total_evidence_chunks,
            },
        )
        await session.commit()

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="answer.cancelled",
            message="Fragenbeantwortung nach Abschluss wegen Job-Abbruch nicht weitergeführt",
            payload={"questions_done": done, "questions_total": len(run_rows)},
        )

        await self.enqueue(
            subjects.REPORT_RENDER,
            {"job_id": str(job.id), "owner_user_id": str(job.owner_user_id), "task_key": f"report:{job.id}"},
        )

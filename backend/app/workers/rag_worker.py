
import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.embedding_client import EmbeddingClient
from app.llm.prompt_limits import count_text_tokens
from app.llm.reranker_client import RerankerClient
from app.llm.vllm_gateway import VLLMGateway
from app.models import Job, MessageChunk, Question, QuestionRun, RetrievalHit, StepStatus
from app.services.answer_generation import (
    EvidenceChunk,
    EvidenceBatch,
    SummaryBatch,
    build_answer_prompt,
    build_evidence_batches,
    build_evidence_map_prompt,
    build_reduce_answer_prompt,
    build_summary_batches,
    build_summary_reduce_prompt,
    evidence_chunk_payload,
    make_short_answer,
    no_evidence_answer,
)
from app.services.qdrant_index import QdrantIndex
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()
ANSWER_MAP_MAX_TOKENS = 1536
ANSWER_REDUCE_MAX_TOKENS = 4096
MAX_SUMMARY_REDUCE_ROUNDS = 6


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
    raw_chunk_id = payload.get("parent_chunk_id") or payload.get("chunk_id") or hit.get("id")
    if raw_chunk_id is None:
        return None
    try:
        return uuid.UUID(str(raw_chunk_id))
    except (TypeError, ValueError):
        return None


def _merge_qdrant_hits_by_parent(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[uuid.UUID, dict[str, Any]] = {}
    for position, hit in enumerate(hits):
        chunk_id = _chunk_id_from_qdrant_hit(hit)
        if chunk_id is None:
            continue
        try:
            score = float(hit.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        current = merged.get(chunk_id)
        if current is None or score > current["score"]:
            payload = dict(hit.get("payload") or {})
            payload["chunk_id"] = str(chunk_id)
            payload["parent_chunk_id"] = str(chunk_id)
            merged[chunk_id] = {
                **hit,
                "payload": payload,
                "score": score,
                "_merge_position": position,
            }

    return sorted(
        merged.values(),
        key=lambda item: (-float(item.get("score", 0.0)), int(item.get("_merge_position", 0))),
    )


async def _search_unique_parent_hits(
    qdrant: QdrantIndex,
    *,
    vectors: list[list[float]],
    job_id: uuid.UUID,
    limit: int,
) -> list[dict[str, Any]]:
    if not vectors or limit <= 0:
        return []

    search_limit = max(limit * 4, limit + 20)
    max_search_limit = max(search_limit, min(10_000, limit * 64))
    while True:
        all_hits: list[dict[str, Any]] = []
        exhausted = True
        for vector in vectors:
            hits = await qdrant.search(
                vector=vector,
                job_id=job_id,
                limit=search_limit,
                with_payload=True,
            )
            all_hits.extend(hits)
            if len(hits) >= search_limit:
                exhausted = False

        merged = _merge_qdrant_hits_by_parent(all_hits)
        if len(merged) >= limit or exhausted or search_limit >= max_search_limit:
            return merged[:limit]
        search_limit = min(max_search_limit, search_limit * 2)


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


def _answer_prompt_context_budget(question: str, prompt_body_budget: int) -> int:
    overhead = count_text_tokens(build_answer_prompt(question, ""), model=settings.text_model)
    return max(1, prompt_body_budget - overhead)


def _evidence_map_context_budget(question: str, prompt_body_budget: int) -> int:
    empty_batch = EvidenceBatch(batch_index=1, chunks=[], context="")
    overhead = count_text_tokens(
        build_evidence_map_prompt(question, empty_batch, batch_count=999),
        model=settings.text_model,
    )
    return max(1, prompt_body_budget - overhead)


def _summary_reduce_context_budget(question: str, prompt_body_budget: int) -> int:
    empty_batch = SummaryBatch(batch_index=1, context="", summary_indexes=[])
    overhead = count_text_tokens(
        build_summary_reduce_prompt(question, empty_batch, round_index=999, batch_count=999),
        model=settings.text_model,
    )
    return max(1, prompt_body_budget - overhead)


def _final_answer_summary_context_budget(question: str, prompt_body_budget: int) -> int:
    overhead = count_text_tokens(build_reduce_answer_prompt(question, ""), model=settings.text_model)
    return max(1, prompt_body_budget - overhead)


async def _gateway_answer_prompt_body_budget(gateway: Any, *, max_tokens: int) -> int:
    method = getattr(gateway, "answer_prompt_body_budget", None)
    if callable(method):
        return int(await method(max_tokens=max_tokens))
    # Unit-test fakes historically only implemented answer_prompt. Keep those
    # tests meaningful by deriving a body budget from the legacy character cap.
    return max(
        1,
        settings.answer_context_max_chars
        + count_text_tokens(build_answer_prompt("", ""), model=settings.text_model),
    )


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
        question_part_vectors = await embedder.embed_parts([question.text for question in questions])
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="retrieval.cancelled",
            message="Retrieval nach Frage-Embedding wegen Job-Abbruch beendet",
            payload={"questions_total": len(questions)},
        )
        question_vectors: list[list[list[float]]] = [[] for _ in questions]
        for item in question_part_vectors:
            question_vectors[item.segment.parent_index].append(item.vector)
        if any(not vectors for vectors in question_vectors):
            raise RuntimeError("Embedding endpoint returned no vector for at least one question")

        done = 0
        total_hits = 0
        for question, vectors in zip(questions, question_vectors, strict=True):
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

            hits = await _search_unique_parent_hits(
                qdrant,
                vectors=vectors,
                job_id=job.id,
                limit=retrieval_k,
            )
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
                "question_embedding_segments": len(vectors),
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
                    "question_embedding_segments": len(vectors),
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

    async def _call_answer_model(
        self,
        session: AsyncSession,
        job: Job,
        gateway: VLLMGateway,
        prompt: str,
        *,
        max_tokens: int,
        questions_done: int,
        questions_total: int,
        message: str,
    ) -> str:
        answer = await gateway.answer_prompt(prompt, max_tokens=max_tokens)
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="answer.cancelled",
            message=message,
            payload={"questions_done": questions_done, "questions_total": questions_total},
        )
        return answer

    async def _answer_question_with_evidence(
        self,
        session: AsyncSession,
        job: Job,
        gateway: VLLMGateway,
        *,
        question_run: QuestionRun,
        question: Question,
        evidence_chunks: list[EvidenceChunk],
        questions_done: int,
        questions_total: int,
    ) -> tuple[str, dict[str, Any]]:
        evidence_payload = [evidence_chunk_payload(chunk) for chunk in evidence_chunks]
        if callable(getattr(gateway, "answer_prompt_body_budget", None)):
            direct_body_budget = await _gateway_answer_prompt_body_budget(
                gateway,
                max_tokens=ANSWER_REDUCE_MAX_TOKENS,
            )
            map_body_budget = await _gateway_answer_prompt_body_budget(
                gateway,
                max_tokens=ANSWER_MAP_MAX_TOKENS,
            )
            direct_context_budget = _answer_prompt_context_budget(question.text, direct_body_budget)
            map_context_budget = _evidence_map_context_budget(question.text, map_body_budget)
        else:
            direct_body_budget = settings.answer_context_max_chars + 10_000
            map_body_budget = settings.answer_context_max_chars + 10_000
            direct_context_budget = settings.answer_context_max_chars
            map_context_budget = settings.answer_context_max_chars

        direct_batches = build_evidence_batches(
            evidence_chunks,
            max_tokens=direct_context_budget,
        )

        if not direct_batches:
            answer = no_evidence_answer(question.text)
            return answer, {
                "stage": "answer",
                "strategy": "no_evidence",
                "mock": settings.llm_mock_enabled,
                "text_model": settings.text_model,
                "evidence_chunks": [],
                "evidence_batch_count": 0,
                "truncated_evidence_batches": 0,
                "context_chars": 0,
                "summary_chars": 0,
                "reduce_rounds": 0,
                "prompt_chars": 0,
                "skipped_llm_reason": "no_used_evidence_chunks",
            }

        if len(direct_batches) == 1 and not direct_batches[0].truncated:
            context = direct_batches[0].context
            prompt_body = build_answer_prompt(question.text, context)
            answer = await self._call_answer_model(
                session,
                job,
                gateway,
                prompt_body,
                max_tokens=ANSWER_REDUCE_MAX_TOKENS,
                questions_done=questions_done,
                questions_total=questions_total,
                message="Fragenbeantwortung nach Modellaufruf wegen Job-Abbruch beendet",
            )
            return answer, {
                "stage": "answer",
                "strategy": "direct",
                "mock": settings.llm_mock_enabled,
                "text_model": settings.text_model,
                "evidence_chunks": evidence_payload,
                "evidence_batch_count": 1,
                "truncated_evidence_batches": 0,
                "context_chars": len(context),
                "context_tokens": count_text_tokens(context, model=settings.text_model),
                "summary_chars": 0,
                "reduce_rounds": 0,
                "prompt_chars": len(prompt_body),
                "prompt_tokens": count_text_tokens(prompt_body, model=settings.text_model),
                "answer_chars": len(answer),
            }

        evidence_batches = build_evidence_batches(
            evidence_chunks,
            max_tokens=map_context_budget,
        )
        truncated_batches = sum(1 for batch in evidence_batches if batch.truncated)
        return await self._answer_question_map_reduce(
            session,
            job,
            gateway,
            question_run=question_run,
            question=question,
            evidence_chunks=evidence_chunks,
            evidence_payload=evidence_payload,
            evidence_batches=evidence_batches,
            truncated_batches=truncated_batches,
            map_body_budget=map_body_budget,
            direct_body_budget=direct_body_budget,
            questions_done=questions_done,
            questions_total=questions_total,
        )

    async def _answer_question_map_reduce(
        self,
        session: AsyncSession,
        job: Job,
        gateway: VLLMGateway,
        *,
        question_run: QuestionRun,
        question: Question,
        evidence_chunks: list[EvidenceChunk],
        evidence_payload: list[dict[str, Any]],
        evidence_batches: list[EvidenceBatch],
        truncated_batches: int,
        map_body_budget: int,
        direct_body_budget: int,
        questions_done: int,
        questions_total: int,
    ) -> tuple[str, dict[str, Any]]:
        await self.emit_event(
            session,
            job=job,
            event_type="answer.map_reduce.started",
            message="Map-Reduce-Fragenbeantwortung gestartet",
            payload={
                "questions_done": questions_done,
                "questions_total": questions_total,
                "question_id": str(question.id),
                "question_index": question.question_index,
                "question_run_id": str(question_run.id),
                "evidence_chunks": len(evidence_chunks),
                "evidence_batches": len(evidence_batches),
                "truncated_evidence_batches": truncated_batches,
                "context_chars": sum(len(batch.context) for batch in evidence_batches),
                "text_model": settings.text_model,
                "mock_enabled": settings.llm_mock_enabled,
            },
        )
        await session.commit()

        map_summaries: list[str] = []
        map_prompt_chars: list[int] = []
        map_prompt_tokens: list[int] = []
        for batch in evidence_batches:
            map_prompt = build_evidence_map_prompt(
                question.text,
                batch,
                batch_count=len(evidence_batches),
            )
            summary = await self._call_answer_model(
                session,
                job,
                gateway,
                map_prompt,
                max_tokens=ANSWER_MAP_MAX_TOKENS,
                questions_done=questions_done,
                questions_total=questions_total,
                message="Map-Reduce-Zwischenzusammenfassung wegen Job-Abbruch beendet",
            )
            map_summaries.append(summary)
            map_prompt_chars.append(len(map_prompt))
            map_prompt_tokens.append(count_text_tokens(map_prompt, model=settings.text_model))

            await self.emit_event(
                session,
                job=job,
                event_type="answer.map_reduce.progress",
                message=(
                    f"Zwischenzusammenfassung {batch.batch_index}/{len(evidence_batches)} "
                    "erstellt"
                ),
                payload={
                    "phase": "map",
                    "questions_done": questions_done,
                    "questions_total": questions_total,
                    "question_id": str(question.id),
                    "question_index": question.question_index,
                    "question_run_id": str(question_run.id),
                    "batch_index": batch.batch_index,
                    "batches_total": len(evidence_batches),
                    "prompt_chars": len(map_prompt),
                    "prompt_tokens": map_prompt_tokens[-1],
                    "summary_chars": len(summary),
                },
            )
            await session.commit()

        summaries = map_summaries
        reduce_rounds = 0
        reduce_prompt_chars: list[int] = []
        reduce_prompt_tokens: list[int] = []
        summary_context_truncated = False

        while True:
            summary_context_budget = min(
                _summary_reduce_context_budget(question.text, map_body_budget),
                _final_answer_summary_context_budget(question.text, direct_body_budget),
            )
            summary_batches = build_summary_batches(
                summaries,
                max_tokens=summary_context_budget,
            )
            if len(summary_batches) <= 1 or reduce_rounds >= MAX_SUMMARY_REDUCE_ROUNDS:
                if len(summary_batches) > 1:
                    summary_context_truncated = True
                break

            reduce_rounds += 1
            next_summaries: list[str] = []
            for summary_batch in summary_batches:
                reduce_prompt = build_summary_reduce_prompt(
                    question.text,
                    summary_batch,
                    round_index=reduce_rounds,
                    batch_count=len(summary_batches),
                )
                reduced_summary = await self._call_answer_model(
                    session,
                    job,
                    gateway,
                    reduce_prompt,
                    max_tokens=ANSWER_MAP_MAX_TOKENS,
                    questions_done=questions_done,
                    questions_total=questions_total,
                    message="Map-Reduce-Reduktionsrunde wegen Job-Abbruch beendet",
                )
                reduce_prompt_chars.append(len(reduce_prompt))
                reduce_prompt_tokens.append(count_text_tokens(reduce_prompt, model=settings.text_model))
                next_summaries.append(reduced_summary)

                await self.emit_event(
                    session,
                    job=job,
                    event_type="answer.map_reduce.progress",
                    message=(
                        f"Reduktionsrunde {reduce_rounds}, Batch "
                        f"{summary_batch.batch_index}/{len(summary_batches)} erstellt"
                    ),
                    payload={
                        "phase": "reduce",
                        "questions_done": questions_done,
                        "questions_total": questions_total,
                        "question_id": str(question.id),
                        "question_index": question.question_index,
                        "question_run_id": str(question_run.id),
                        "round_index": reduce_rounds,
                        "batch_index": summary_batch.batch_index,
                        "batches_total": len(summary_batches),
                        "prompt_chars": len(reduce_prompt),
                        "prompt_tokens": reduce_prompt_tokens[-1],
                        "summary_chars": len(reduced_summary),
                    },
                )
                await session.commit()
            summaries = next_summaries

        final_summary_batches = build_summary_batches(
            summaries,
            max_tokens=_final_answer_summary_context_budget(question.text, direct_body_budget),
        )
        if len(final_summary_batches) > 1:
            raise RuntimeError(
                "Summary reduction did not converge into a final token-bounded answer prompt"
            )
        final_summary_context = final_summary_batches[0].context if final_summary_batches else ""
        final_prompt = build_reduce_answer_prompt(question.text, final_summary_context)
        answer = await self._call_answer_model(
            session,
            job,
            gateway,
            final_prompt,
            max_tokens=ANSWER_REDUCE_MAX_TOKENS,
            questions_done=questions_done,
            questions_total=questions_total,
            message="Map-Reduce-Antwortsynthese wegen Job-Abbruch beendet",
        )

        await self.emit_event(
            session,
            job=job,
            event_type="answer.map_reduce.completed",
            message="Map-Reduce-Fragenbeantwortung abgeschlossen",
            payload={
                "questions_done": questions_done,
                "questions_total": questions_total,
                "question_id": str(question.id),
                "question_index": question.question_index,
                "question_run_id": str(question_run.id),
                "evidence_batches": len(evidence_batches),
                "truncated_evidence_batches": truncated_batches,
                "reduce_rounds": reduce_rounds,
                "summary_context_chars": len(final_summary_context),
                "answer_chars": len(answer),
            },
        )
        await session.commit()

        return answer, {
            "stage": "answer",
            "strategy": "map_reduce",
            "mock": settings.llm_mock_enabled,
            "text_model": settings.text_model,
            "evidence_chunks": evidence_payload,
            "evidence_batch_count": len(evidence_batches),
            "truncated_evidence_batches": truncated_batches,
            "context_chars": sum(len(batch.context) for batch in evidence_batches),
            "summary_chars": sum(len(summary) for summary in map_summaries),
            "final_summary_context_chars": len(final_summary_context),
            "reduce_rounds": reduce_rounds,
            "summary_context_truncated": summary_context_truncated,
            "prompt_chars": len(final_prompt),
            "prompt_tokens": count_text_tokens(final_prompt, model=settings.text_model),
            "map_prompt_chars": map_prompt_chars,
            "map_prompt_tokens": map_prompt_tokens,
            "reduce_prompt_chars": reduce_prompt_chars,
            "reduce_prompt_tokens": reduce_prompt_tokens,
            "answer_chars": len(answer),
        }

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

            answer, raw_response = await self._answer_question_with_evidence(
                session,
                job,
                gateway,
                question_run=question_run,
                question=question,
                evidence_chunks=evidence_chunks,
                questions_done=done,
                questions_total=len(run_rows),
            )
            if raw_response.get("strategy") != "no_evidence":
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
                    "strategy": raw_response.get("strategy"),
                    "evidence_batches": raw_response.get("evidence_batch_count", 0),
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


import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import func, nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm.prompt_limits import count_text_tokens, split_text_by_tokens
from app.llm.vllm_gateway import VLLMGateway
from app.models import (
    Job,
    JobStatus,
    MediaAnalysis,
    MediaTranscript,
    MediaTranscriptTranslation,
    MessageChunk,
    MessageTranslation,
    Question,
    QuestionRun,
    Report,
    RetrievalHit,
    StepStatus,
    TelegramMedia,
    TelegramMessage,
)
from app.services.minio_store import put_bytes
from app.services.report_builder import (
    ReportGalleryItem,
    ReportQuestion,
    bluf_question_blocks,
    bluf_source_questions,
    build_bluf_reduce_prompt,
    build_bluf_synthesis_prompt,
    build_bluf_synthesis_prompt_from_blocks,
    build_report_evidence_chunk,
    build_report_gallery_item,
    build_report_message,
    make_bluf,
    parse_uuid_list,
    render_report_markdown,
    sort_report_gallery_items,
)
from app.services.report_naming import (
    build_report_filename,
    report_date_for_job,
    resolve_report_source_name,
)
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()
BLUF_MAX_TOKENS = 1024


def _score(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:.4f}"


def _batch_bluf_blocks(blocks: list[str], *, max_tokens: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []

    def prompt_tokens(items: list[str]) -> int:
        return count_text_tokens(
            build_bluf_synthesis_prompt_from_blocks(items),
            model=settings.text_model,
        )

    for block in blocks:
        candidates = [block]
        if prompt_tokens([block]) > max_tokens:
            overhead = count_text_tokens(
                build_bluf_synthesis_prompt_from_blocks([""]),
                model=settings.text_model,
            )
            part_budget = max(1, max_tokens - overhead - 8)
            candidates = split_text_by_tokens(block, part_budget, model=settings.text_model)

        for candidate in candidates:
            if current and prompt_tokens([*current, candidate]) > max_tokens:
                batches.append(current)
                current = []
            current.append(candidate)

    if current:
        batches.append(current)
    return batches


def _batch_bluf_summaries(summaries: list[str], *, max_tokens: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []

    def prompt_tokens(items: list[str]) -> int:
        return count_text_tokens(build_bluf_reduce_prompt(items), model=settings.text_model)

    for summary in summaries:
        candidates = [summary]
        if prompt_tokens([summary]) > max_tokens:
            overhead = count_text_tokens(build_bluf_reduce_prompt([""]), model=settings.text_model)
            part_budget = max(1, max_tokens - overhead - 8)
            candidates = split_text_by_tokens(summary, part_budget, model=settings.text_model)

        for candidate in candidates:
            if current and prompt_tokens([*current, candidate]) > max_tokens:
                batches.append(current)
                current = []
            current.append(candidate)

    if current:
        batches.append(current)
    return batches


class ReportWorker(Worker):
    subject = subjects.REPORT_RENDER
    durable = "report-worker"
    queue = "report"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job_id = uuid.UUID(payload["job_id"])
        job = (await session.execute(select(Job).where(Job.id == job_id))).scalar_one()

        await self.emit_event(
            session,
            job=job,
            event_type="report.render.started",
            message="Report generation started",
        )
        await session.commit()

        if await self.should_skip_cancelled(session, job.id):
            return

        env = Environment(
            loader=FileSystemLoader("app/templates/report"),
            autoescape=select_autoescape(["html", "xml", "html.j2"]),
        )
        env.filters["score"] = _score

        questions = await self._load_questions(session, job)
        media_gallery = await self._load_media_gallery(session, job)
        stats = await self._load_stats(session, job)
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="report.render.cancelled",
            message="Report generation stopped because the job was cancelled",
            payload={"questions_total": len(questions)},
        )

        await self.emit_event(
            session,
            job=job,
            event_type="report.render.progress",
            message="Answers and evidence loaded for the report",
            payload={
                "questions_total": len(questions),
                "evidence_chunks_total": sum(len(question.evidence) for question in questions),
                "messages_total": stats["messages_total"],
                "media_total": stats["media_total"],
            },
        )
        await session.commit()

        bluf = await self._synthesize_bluf(session, job, questions)

        generated_at = datetime.now(timezone.utc)
        source_name = await resolve_report_source_name(session, job)
        if not job.source_name:
            job.source_name = source_name
        filename = build_report_filename(
            source_name,
            report_date_for_job(job, generated_at),
        )
        report_bytes = self._render_report_zip(
            env,
            job=job,
            generated_at=generated_at,
            questions=questions,
            media_gallery=media_gallery,
            stats=stats,
            bluf=bluf,
        )

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="report.render.cancelled",
            message="Report generation stopped before saving because the job was cancelled",
            payload={"report_bytes": len(report_bytes)},
        )

        object_key = f"users/{job.owner_user_id}/jobs/{job.id}/reports/report.zip"
        put_bytes(object_key, report_bytes, content_type="application/zip")

        existing = (await session.execute(select(Report).where(Report.job_id == job.id))).scalar_one_or_none()
        if existing:
            existing.object_key = object_key
            existing.filename = filename
            existing.created_at = datetime.now(timezone.utc)
        else:
            session.add(Report(job_id=job.id, object_key=object_key, filename=filename))

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="report.render.cancelled",
            message="Saved report left incomplete because the job was cancelled",
            payload={"report_object_key": object_key},
        )

        job.status = JobStatus.completed
        job.completed_at = datetime.now(timezone.utc)
        await self.emit_event(
            session,
            job=job,
            event_type="job.completed",
            message="Report generated",
            payload={
                "report_object_key": object_key,
                "report_filename": filename,
                "report_bytes": len(report_bytes),
                "questions_total": len(questions),
                "evidence_chunks_total": sum(len(question.evidence) for question in questions),
            },
        )

    async def _synthesize_bluf(
        self,
        session: AsyncSession,
        job: Job,
        questions: list[ReportQuestion],
    ) -> str:
        source_questions = bluf_source_questions(questions)
        prompt = build_bluf_synthesis_prompt(questions)
        await self.emit_event(
            session,
            job=job,
            event_type="report.bluf.started",
            message="Report summary generation started",
            payload={
                "questions_total": len(questions),
                "source_questions": len(source_questions),
                "prompt_chars": len(prompt),
                "text_model": settings.text_model,
                "mock_enabled": settings.llm_mock_enabled,
            },
        )
        await session.commit()

        if not prompt:
            bluf = make_bluf(questions)
            await self.emit_event(
                session,
                job=job,
                event_type="report.bluf.completed",
                message="Report summary created without completed question summaries",
                payload={
                    "questions_total": len(questions),
                    "source_questions": 0,
                    "bluf_chars": len(bluf),
                    "skipped_llm_reason": "no_completed_question_summaries",
                },
            )
            await session.commit()
            return bluf

        gateway = VLLMGateway()
        bluf_prompt_budget = await gateway.synthesize_bluf_prompt_body_budget(
            max_tokens=BLUF_MAX_TOKENS
        )
        prompt_tokens = count_text_tokens(prompt, model=settings.text_model)
        if prompt_tokens <= bluf_prompt_budget:
            bluf = await gateway.synthesize_bluf(prompt)
            bluf_strategy = "direct"
            bluf_batches = 1
            bluf_reduce_rounds = 0
        else:
            bluf, bluf_batches, bluf_reduce_rounds = await self._synthesize_bluf_map_reduce(
                gateway,
                source_questions,
                prompt_budget=bluf_prompt_budget,
            )
            bluf_strategy = "map_reduce"
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="report.render.cancelled",
            message="Report summary generation stopped because the job was cancelled",
            payload={
                "questions_total": len(questions),
                "source_questions": len(source_questions),
                "strategy": bluf_strategy,
                "batches": bluf_batches,
                "reduce_rounds": bluf_reduce_rounds,
            },
        )
        bluf = bluf.strip()
        if not bluf:
            raise ValueError("BLUF synthesis returned empty text")

        await self.emit_event(
            session,
            job=job,
            event_type="report.bluf.completed",
            message="Report summary generation completed",
            payload={
                "questions_total": len(questions),
                "source_questions": len(source_questions),
                "prompt_chars": len(prompt),
                "prompt_tokens": prompt_tokens,
                "bluf_chars": len(bluf),
                "strategy": bluf_strategy,
                "batches": bluf_batches,
                "reduce_rounds": bluf_reduce_rounds,
                "text_model": settings.text_model,
                "mock_enabled": settings.llm_mock_enabled,
            },
        )
        await session.commit()
        return bluf

    async def _synthesize_bluf_map_reduce(
        self,
        gateway: VLLMGateway,
        source_questions: list[ReportQuestion],
        *,
        prompt_budget: int,
    ) -> tuple[str, int, int]:
        blocks = bluf_question_blocks(source_questions)
        batches = _batch_bluf_blocks(blocks, max_tokens=prompt_budget)
        summaries = [
            await gateway.synthesize_bluf(build_bluf_synthesis_prompt_from_blocks(batch))
            for batch in batches
        ]

        reduce_rounds = 0
        while True:
            reduce_prompt = build_bluf_reduce_prompt(summaries)
            if count_text_tokens(reduce_prompt, model=settings.text_model) <= prompt_budget:
                return await gateway.synthesize_bluf(reduce_prompt), len(batches), reduce_rounds

            if reduce_rounds >= 6:
                raise RuntimeError("BLUF reduction did not converge into a token-bounded prompt")

            reduce_rounds += 1
            summary_batches = _batch_bluf_summaries(summaries, max_tokens=prompt_budget)
            summaries = [
                await gateway.synthesize_bluf(build_bluf_reduce_prompt(batch))
                for batch in summary_batches
            ]

    def _render_report_zip(
        self,
        env: Environment,
        *,
        job: Job,
        generated_at: datetime,
        questions: list[ReportQuestion],
        stats: dict[str, Any],
        bluf: str,
        media_gallery: list[ReportGalleryItem] | None = None,
    ) -> bytes:
        env.filters["report_markdown"] = render_report_markdown
        gallery_items = media_gallery or []
        index_html = env.get_template("index.html.j2").render(
            job=job,
            generated_at=generated_at,
            questions=questions,
            media_gallery=gallery_items,
            stats=stats,
            bluf=bluf,
        )

        media_gallery_html = env.get_template("media_gallery.html.j2").render(
            job=job,
            generated_at=generated_at,
            media_gallery=gallery_items,
        )

        report_css = env.get_template("report.css.j2").render()
        report_js = env.get_template("report.js.j2").render()

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report/index.html", index_html)
            zf.writestr("report/media_gallery.html", media_gallery_html)
            zf.writestr("report/assets/report.css", report_css)
            zf.writestr("report/assets/report.js", report_js)

            for question in questions:
                html = env.get_template("subreport.html.j2").render(
                    job=job,
                    question=question,
                    generated_at=generated_at,
                    stats=stats,
                )
                zf.writestr(f"report/{question.filename}", html)

        return zip_buffer.getvalue()

    async def _load_media_gallery(
        self,
        session: AsyncSession,
        job: Job,
    ) -> list[ReportGalleryItem]:
        rows = list(
            (
                await session.execute(
                    select(TelegramMedia, TelegramMessage)
                    .outerjoin(TelegramMessage, TelegramMessage.id == TelegramMedia.message_id)
                    .where(TelegramMedia.job_id == job.id)
                )
            ).all()
        )
        items = [build_report_gallery_item(media, message) for media, message in rows]
        return sort_report_gallery_items(items)

    async def _load_questions(self, session: AsyncSession, job: Job) -> list[ReportQuestion]:
        question_rows = list(
            (
                await session.execute(
                    select(Question).where(Question.job_id == job.id).order_by(Question.question_index)
                )
            )
            .scalars()
            .all()
        )

        rendered: list[ReportQuestion] = []
        for question in question_rows:
            run = (
                await session.execute(
                    select(QuestionRun)
                    .where(QuestionRun.question_id == question.id)
                    .order_by(QuestionRun.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            evidence = (
                await self._hydrate_evidence(
                    session,
                    run,
                    english_only=bool((job.options or {}).get("translate", False)),
                )
                if run
                else []
            )
            rendered.append(
                ReportQuestion(
                    index=question.question_index,
                    filename=f"questions/q_{question.question_index:03d}.html",
                    question=question.text,
                    answer=run.answer if run and run.answer else "No answer has been saved yet.",
                    short_answer=(run.short_answer if run and run.short_answer else "No summary has been saved yet."),
                    status=(run.status.value if run else StepStatus.pending.value),
                    retrieval_k=(run.retrieval_k if run else None),
                    rerank_k=(run.rerank_k if run else None),
                    evidence=evidence,
                )
            )
        return rendered

    async def _hydrate_evidence(
        self,
        session: AsyncSession,
        question_run: QuestionRun,
        *,
        english_only: bool,
    ) -> list[Any]:
        hit_rows = list(
            (
                await session.execute(
                    select(RetrievalHit, MessageChunk)
                    .join(MessageChunk, MessageChunk.id == RetrievalHit.chunk_id)
                    .where(
                        RetrievalHit.question_run_id == question_run.id,
                        RetrievalHit.used_in_answer.is_(True),
                    )
                    .order_by(nullslast(RetrievalHit.rerank_rank), RetrievalHit.retrieval_rank)
                )
            ).all()
        )

        evidence = []
        for hit, chunk in hit_rows:
            message_ids = parse_uuid_list(chunk.message_ids)
            if not message_ids:
                evidence.append(build_report_evidence_chunk(hit=hit, chunk=chunk, messages=[]))
                continue

            message_rows = list(
                (
                    await session.execute(
                        select(TelegramMessage).where(
                            TelegramMessage.job_id == question_run.job_id,
                            TelegramMessage.id.in_(message_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            messages_by_id = {message.id: message for message in message_rows}
            media_by_message = await self._load_media_for_messages(
                session,
                question_run.job_id,
                message_ids,
            )
            translations_by_message = await self._load_translations_for_messages(
                session,
                question_run.job_id,
                message_ids,
            )

            ordered_messages = []
            for message_id in message_ids:
                message = messages_by_id.get(message_id)
                if message is None:
                    continue
                ordered_messages.append(
                    build_report_message(
                        message,
                        media_by_message.get(message.id, []),
                        translation=translations_by_message.get(message.id),
                        english_only=english_only,
                    )
                )

            evidence.append(build_report_evidence_chunk(hit=hit, chunk=chunk, messages=ordered_messages))
        return evidence

    async def _load_media_for_messages(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        message_ids: list[uuid.UUID],
    ) -> dict[
        uuid.UUID,
        list[
            tuple[
                TelegramMedia,
                MediaAnalysis | None,
                MediaTranscript | None,
                MediaTranscriptTranslation | None,
            ]
        ],
    ]:
        if not message_ids:
            return {}

        rows = list(
            (
                await session.execute(
                    select(
                        TelegramMedia,
                        MediaAnalysis,
                        MediaTranscript,
                        MediaTranscriptTranslation,
                    )
                    .outerjoin(
                        MediaAnalysis,
                        (MediaAnalysis.media_id == TelegramMedia.id)
                        & (MediaAnalysis.model_name == settings.vision_model)
                        & (MediaAnalysis.prompt_version == settings.media_analysis_prompt_version),
                    )
                    .outerjoin(
                        MediaTranscript,
                        (MediaTranscript.media_id == TelegramMedia.id)
                        & (MediaTranscript.provider == "openai")
                        & (MediaTranscript.model_name == settings.openai_transcription_model)
                        & (MediaTranscript.response_format == "text"),
                    )
                    .outerjoin(
                        MediaTranscriptTranslation,
                        (MediaTranscriptTranslation.transcript_id == MediaTranscript.id)
                        & (MediaTranscriptTranslation.provider == "libretranslate")
                        & (MediaTranscriptTranslation.target_language == "en"),
                    )
                    .where(
                        TelegramMedia.job_id == job_id,
                        TelegramMedia.message_id.in_(message_ids),
                    )
                    .order_by(TelegramMedia.original_path)
                )
            ).all()
        )

        grouped: dict[
            uuid.UUID,
            list[
                tuple[
                    TelegramMedia,
                    MediaAnalysis | None,
                    MediaTranscript | None,
                    MediaTranscriptTranslation | None,
                ]
            ],
        ] = defaultdict(list)
        for media, analysis, transcript, transcript_translation in rows:
            if media.message_id is not None:
                grouped[media.message_id].append(
                    (media, analysis, transcript, transcript_translation)
                )
        return grouped

    async def _load_translations_for_messages(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        message_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, MessageTranslation]:
        if not message_ids:
            return {}

        rows = list(
            (
                await session.execute(
                    select(MessageTranslation).where(
                        MessageTranslation.job_id == job_id,
                        MessageTranslation.message_id.in_(message_ids),
                        MessageTranslation.provider == "libretranslate",
                        MessageTranslation.target_language == "en",
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.message_id: row for row in rows if row.translated_text.strip()}

    async def _load_stats(self, session: AsyncSession, job: Job) -> dict[str, Any]:
        messages_total = await self._count(session, select(func.count()).select_from(TelegramMessage).where(TelegramMessage.job_id == job.id))
        chunks_total = await self._count(session, select(func.count()).select_from(MessageChunk).where(MessageChunk.job_id == job.id))
        questions_total = await self._count(session, select(func.count()).select_from(Question).where(Question.job_id == job.id))
        media_total = await self._count(session, select(func.count()).select_from(TelegramMedia).where(TelegramMedia.job_id == job.id))
        media_completed = await self._count(
            session,
            select(func.count()).select_from(TelegramMedia).where(
                TelegramMedia.job_id == job.id,
                TelegramMedia.status == StepStatus.completed,
            ),
        )
        media_failed = await self._count(
            session,
            select(func.count()).select_from(TelegramMedia).where(
                TelegramMedia.job_id == job.id,
                TelegramMedia.status.in_([StepStatus.failed_retryable, StepStatus.failed_permanent]),
            ),
        )
        media_missing = await self._count(
            session,
            select(func.count()).select_from(TelegramMedia).where(
                TelegramMedia.job_id == job.id,
                TelegramMedia.missing_reason.is_not(None),
            ),
        )
        evidence_chunks_total = await self._count(
            session,
            select(func.count()).select_from(RetrievalHit).join(QuestionRun, QuestionRun.id == RetrievalHit.question_run_id).where(
                QuestionRun.job_id == job.id,
                RetrievalHit.used_in_answer.is_(True),
            ),
        )
        return {
            "messages_total": messages_total,
            "chunks_total": chunks_total,
            "questions_total": questions_total,
            "media_total": media_total,
            "media_completed": media_completed,
            "media_failed": media_failed,
            "media_missing": media_missing,
            "evidence_chunks_total": evidence_chunks_total,
            "retrieval_k": (job.options or {}).get("retrieval_k"),
            "rerank_k": (job.options or {}).get("rerank_k"),
            "translate": bool((job.options or {}).get("translate", False)),
            "analyze_media": bool((job.options or {}).get("analyze_media", True)),
            "mock_enabled": settings.llm_mock_enabled,
            "text_model": settings.text_model,
            "vision_model": settings.vision_model,
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.reranker_model,
        }

    async def _count(self, session: AsyncSession, statement) -> int:
        value = (await session.execute(statement)).scalar_one()
        return int(value or 0)

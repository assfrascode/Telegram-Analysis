
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
from app.models import (
    Job,
    JobStatus,
    MediaAnalysis,
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
    ReportQuestion,
    build_report_evidence_chunk,
    build_report_message,
    make_bluf,
    parse_uuid_list,
)
from app.workers import subjects
from app.workers.base import Worker

settings = get_settings()


def _report_zip_name(job_id: uuid.UUID) -> str:
    return f"chat-analyse-report-{job_id}.zip"


def _score(value: float | None) -> str:
    if value is None:
        return "–"
    return f"{value:.4f}"


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
            message="Report-Erstellung gestartet",
        )
        await session.commit()

        if await self.should_skip_cancelled(session, job.id):
            return

        env = Environment(
            loader=FileSystemLoader("app/templates/report"),
            autoescape=select_autoescape(["html", "xml"]),
        )
        env.filters["score"] = _score

        questions = await self._load_questions(session, job)
        stats = await self._load_stats(session, job)
        await self.checkpoint_cancelled(
            session,
            job,
            event_type="report.render.cancelled",
            message="Report-Erstellung wegen Job-Abbruch beendet",
            payload={"questions_total": len(questions)},
        )
        bluf = make_bluf(questions)

        await self.emit_event(
            session,
            job=job,
            event_type="report.render.progress",
            message="Antworten und Evidenzdaten für Report geladen",
            payload={
                "questions_total": len(questions),
                "evidence_chunks_total": sum(len(question.evidence) for question in questions),
                "messages_total": stats["messages_total"],
                "media_total": stats["media_total"],
            },
        )
        await session.commit()

        generated_at = datetime.now(timezone.utc)
        index_html = env.get_template("index.html.j2").render(
            job=job,
            generated_at=generated_at,
            questions=questions,
            stats=stats,
            bluf=bluf,
        )

        report_css = env.get_template("report.css.j2").render()
        report_js = env.get_template("report.js.j2").render()

        zip_buffer = BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("report/index.html", index_html)
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

        await self.checkpoint_cancelled(
            session,
            job,
            event_type="report.render.cancelled",
            message="Report-Erstellung vor Speichern wegen Job-Abbruch beendet",
            payload={"report_bytes": len(zip_buffer.getvalue())},
        )

        object_key = f"users/{job.owner_user_id}/jobs/{job.id}/reports/report.zip"
        put_bytes(object_key, zip_buffer.getvalue(), content_type="application/zip")

        existing = (await session.execute(select(Report).where(Report.job_id == job.id))).scalar_one_or_none()
        filename = _report_zip_name(job.id)
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
            message="Report-Erstellung nach Speichern wegen Job-Abbruch nicht als abgeschlossen markiert",
            payload={"report_object_key": object_key},
        )

        job.status = JobStatus.completed
        job.completed_at = datetime.now(timezone.utc)
        await self.emit_event(
            session,
            job=job,
            event_type="job.completed",
            message="Report wurde erstellt",
            payload={
                "report_object_key": object_key,
                "report_filename": filename,
                "report_bytes": len(zip_buffer.getvalue()),
                "questions_total": len(questions),
                "evidence_chunks_total": sum(len(question.evidence) for question in questions),
            },
        )

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

            evidence = await self._hydrate_evidence(session, run) if run else []
            rendered.append(
                ReportQuestion(
                    index=question.question_index,
                    filename=f"questions/q_{question.question_index:03d}.html",
                    question=question.text,
                    answer=run.answer if run and run.answer else "Noch keine Antwort gespeichert.",
                    short_answer=(run.short_answer if run and run.short_answer else "Noch keine Kurzantwort gespeichert."),
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
                    )
                )

            evidence.append(build_report_evidence_chunk(hit=hit, chunk=chunk, messages=ordered_messages))
        return evidence

    async def _load_media_for_messages(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        message_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[tuple[TelegramMedia, MediaAnalysis | None]]]:
        if not message_ids:
            return {}

        rows = list(
            (
                await session.execute(
                    select(TelegramMedia, MediaAnalysis)
                    .outerjoin(
                        MediaAnalysis,
                        (MediaAnalysis.media_id == TelegramMedia.id)
                        & (MediaAnalysis.model_name == settings.vision_model)
                        & (MediaAnalysis.prompt_version == settings.media_analysis_prompt_version),
                    )
                    .where(
                        TelegramMedia.job_id == job_id,
                        TelegramMedia.message_id.in_(message_ids),
                    )
                    .order_by(TelegramMedia.original_path)
                )
            ).all()
        )

        grouped: dict[uuid.UUID, list[tuple[TelegramMedia, MediaAnalysis | None]]] = defaultdict(list)
        for media, analysis in rows:
            if media.message_id is not None:
                grouped[media.message_id].append((media, analysis))
        return grouped

    async def _load_translations_for_messages(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        message_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, MessageTranslation]:
        if not message_ids:
            return {}

        target_language = (settings.libretranslate_target_language or "en").strip() or "en"
        rows = list(
            (
                await session.execute(
                    select(MessageTranslation).where(
                        MessageTranslation.job_id == job_id,
                        MessageTranslation.message_id.in_(message_ids),
                        MessageTranslation.provider == "libretranslate",
                        MessageTranslation.target_language == target_language,
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

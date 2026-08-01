import os
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.services.report_builder import (
    MISSING_SHORT_ANSWER,
    NO_ANSWER_BLUF,
    ReportQuestion,
    bluf_source_questions,
    build_bluf_synthesis_prompt,
    make_bluf,
)
from app.workers.report_worker import ReportWorker


def _question(
    *,
    index: int,
    question: str = "Was ist passiert?",
    short_answer: str = "Kurzbefund",
    status: str = "completed",
) -> ReportQuestion:
    return ReportQuestion(
        index=index,
        filename=f"questions/q_{index:03d}.html",
        question=question,
        answer=short_answer,
        short_answer=short_answer,
        status=status,
        retrieval_k=50,
        rerank_k=15,
        evidence=[],
    )


def test_bluf_prompt_uses_only_completed_real_question_summaries() -> None:
    questions = [
        _question(
            index=1,
            question="Welche Narrative dominieren?",
            short_answer="Narrativ A dominiert, Evidenz ist stark.",
        ),
        _question(index=2, short_answer=MISSING_SHORT_ANSWER, status="pending"),
        _question(
            index=3,
            question="Welche Unsicherheiten gibt es?",
            short_answer="Eine Quelle widerspricht.",
        ),
    ]

    prompt = build_bluf_synthesis_prompt(questions)

    assert len(bluf_source_questions(questions)) == 2
    assert "Nutze ausschließlich" in prompt
    assert "Ausgangsfrage: Welche Narrative dominieren?" in prompt
    assert "Kurzantwort: Narrativ A dominiert, Evidenz ist stark." in prompt
    assert "Ausgangsfrage: Welche Unsicherheiten gibt es?" in prompt
    assert MISSING_SHORT_ANSWER not in prompt


def test_empty_bluf_sources_keep_existing_no_answer_message() -> None:
    questions = [
        _question(index=1, short_answer=MISSING_SHORT_ANSWER, status="pending"),
        _question(index=2, short_answer="", status="completed"),
    ]

    assert build_bluf_synthesis_prompt(questions) == ""
    assert make_bluf(questions) == NO_ANSWER_BLUF


def test_report_index_renders_synthesized_bluf_instead_of_question_list() -> None:
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml", "html.j2"]),
    )
    env.filters["score"] = lambda value: "–" if value is None else f"{value:.4f}"

    questions = [
        _question(index=1, question="Was ist die Lage?", short_answer="Alter Kurzbefund"),
    ]
    stats = {
        "messages_total": 10,
        "chunks_total": 2,
        "questions_total": 1,
        "evidence_chunks_total": 0,
        "media_total": 0,
        "media_completed": 0,
        "media_failed": 0,
        "media_missing": 0,
        "retrieval_k": 50,
        "rerank_k": 15,
        "translate": False,
        "analyze_media": True,
        "mock_enabled": True,
        "text_model": "text-model",
        "vision_model": "vision-model",
        "embedding_model": "embedding-model",
        "reranker_model": "reranker-model",
    }

    report_bytes = ReportWorker()._render_report_zip(
        env,
        job=SimpleNamespace(id=uuid.uuid4()),
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        questions=questions,
        media_gallery=[],
        stats=stats,
        bluf="Synthetisierte Gesamt-BLUF.",
    )

    with zipfile.ZipFile(BytesIO(report_bytes)) as archive:
        index_html = archive.read("report/index.html").decode("utf-8")

    assert "Synthetisierte Gesamt-BLUF." in index_html
    assert "Frage 1: Alter Kurzbefund" not in index_html

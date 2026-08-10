import os
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.services.report_builder import ReportQuestion, render_report_markdown
from app.workers.report_worker import ReportWorker


def _stats() -> dict:
    return {
        "messages_total": 1,
        "chunks_total": 1,
        "questions_total": 1,
        "evidence_chunks_total": 0,
        "media_total": 0,
        "media_completed": 0,
        "media_failed": 0,
        "media_missing": 0,
        "retrieval_k": 50,
        "rerank_k": 15,
        "translate": False,
        "analyze_media": False,
        "mock_enabled": True,
        "text_model": "text-model",
        "vision_model": "vision-model",
        "embedding_model": "embedding-model",
        "reranker_model": "reranker-model",
    }


def _render_report_files(
    *,
    short_answer: str,
    bluf: str,
    answer: str = "Full answer",
) -> dict[str, str]:
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml", "html.j2"]),
    )
    question = ReportQuestion(
        index=1,
        filename="questions/q_001.html",
        question="What happened?",
        answer=answer,
        short_answer=short_answer,
        status="completed",
        retrieval_k=50,
        rerank_k=15,
        evidence=[],
    )
    report_bytes = ReportWorker()._render_report_zip(
        env,
        job=SimpleNamespace(id=uuid.uuid4(), source_name="Example Group"),
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        questions=[question],
        media_gallery=[],
        stats=_stats(),
        bluf=bluf,
    )
    with zipfile.ZipFile(BytesIO(report_bytes)) as archive:
        return {
            "index": archive.read("report/index.html").decode("utf-8"),
            "subreport": archive.read("report/questions/q_001.html").decode("utf-8"),
        }


def test_report_markdown_renders_headings_lists_and_emphasis() -> None:
    rendered = str(render_report_markdown("## Finding\n\n- First\n- **Second**"))

    assert "<h2>Finding</h2>" in rendered
    assert "<ul>" in rendered
    assert "<li>First</li>" in rendered
    assert "<strong>Second</strong>" in rendered


def test_report_markdown_escapes_raw_html_and_strips_javascript_links() -> None:
    rendered = str(
        render_report_markdown(
            "Safe </div></a><section>text</section> "
            "[unsafe link](javascript:alert('x'))"
        )
    )
    fragment = BeautifulSoup(rendered, "html.parser")

    assert "</div></a><section>text</section>" in fragment.get_text()
    assert fragment.find("section") is None
    assert fragment.find("div") is None
    unsafe_link = fragment.find("a", string="unsafe link")
    assert unsafe_link is not None
    assert "href" not in unsafe_link.attrs
    assert "javascript:" not in rendered.lower()


def test_index_markdown_keeps_question_preview_as_one_link_card() -> None:
    html = _render_report_files(
        short_answer=(
            "### Key points\n\n"
            "- **One finding**\n"
            "- [Supporting site](https://example.com)\n\n"
            "</a></div><section>Untrusted HTML</section>"
        ),
        bluf="## Bottom line\n\n- **Readable** summary",
    )["index"]
    document = BeautifulSoup(html, "html.parser")
    cards = document.select("a.question-card")

    assert len(cards) == 1
    card = cards[0]
    assert card.select_one(".question-card__answer h3").get_text(strip=True) == "Key points"
    assert [item.get_text(" ", strip=True) for item in card.select("li")] == [
        "One finding",
        "Supporting site",
    ]
    assert card.find("a") is None
    assert card.find("section") is None
    assert "</a></div><section>Untrusted HTML</section>" in card.get_text()
    assert document.select_one(".summary-text h2").get_text(strip=True) == "Bottom line"
    assert document.select_one(".summary-text strong").get_text(strip=True) == "Readable"


def test_subreport_renders_sanitized_markdown_answer() -> None:
    html = _render_report_files(
        short_answer="Short answer",
        bluf="Bottom line",
        answer=(
            "## Detailed finding\n\n"
            "- **Strong evidence**\n"
            "- [Source](https://example.com)\n"
            "- [Unsafe](javascript:alert('x'))\n\n"
            "</article><section>Untrusted HTML</section>"
        ),
    )["subreport"]
    document = BeautifulSoup(html, "html.parser")
    answer = document.select_one("article.answer-body")

    assert answer.select_one("h2").get_text(strip=True) == "Detailed finding"
    assert answer.select_one('a[href="https://example.com"]').get_text(strip=True) == "Source"
    assert answer.find("a", string="Unsafe").get("href") is None
    assert answer.find("section") is None
    assert "</article><section>Untrusted HTML</section>" in answer.get_text()

import os
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.models import StepStatus, TelegramMedia, TelegramMessage
from app.services.report_builder import (
    build_report_gallery_item,
    sort_report_gallery_items,
)
from app.workers.report_worker import ReportWorker


def _message(message_id: int, timestamp: datetime | None, sender: str = "Alice") -> TelegramMessage:
    return TelegramMessage(
        id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        telegram_message_id=message_id,
        timestamp=timestamp,
        sender_id="user-1",
        sender_name=sender,
        message_type="message",
        text="Media",
        raw={},
    )


def _media(path: str, **overrides) -> TelegramMedia:
    values = {
        "id": uuid.uuid4(),
        "job_id": uuid.uuid4(),
        "message_id": uuid.uuid4(),
        "media_type": "image",
        "original_path": path,
        "size_bytes": 1234,
        "status": StepStatus.completed,
    }
    values.update(overrides)
    return TelegramMedia(**values)


def _stats(media_total: int) -> dict:
    return {
        "messages_total": 2,
        "chunks_total": 0,
        "questions_total": 0,
        "evidence_chunks_total": 0,
        "media_total": media_total,
        "media_completed": media_total,
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


def test_gallery_links_only_safe_available_export_media() -> None:
    message = _message(42, datetime(2026, 1, 1, tzinfo=timezone.utc))

    available = build_report_gallery_item(_media("photos/photo 1.jpg"), message)
    collected = build_report_gallery_item(
        _media("telegram/source/file.pdf", source_media_id=uuid.uuid4()),
        message,
    )
    missing = build_report_gallery_item(
        _media("videos/missing.mp4", missing_reason="not_included_in_export"),
        message,
    )
    unsafe = build_report_gallery_item(_media("../photos/escape.jpg"), message)

    assert available.relative_href == "../photos/photo 1.jpg"
    assert collected.relative_href is None
    assert "direkt synchronisierte" in collected.link_unavailable_reason
    assert missing.relative_href is None
    assert "nicht verfügbar" in missing.link_unavailable_reason
    assert unsafe.relative_href is None
    assert "nicht sicher" in unsafe.link_unavailable_reason


def test_gallery_items_sort_by_timestamp_message_id_and_path_with_unknowns_last() -> None:
    early = _message(20, datetime(2026, 1, 1, tzinfo=timezone.utc))
    early_lower_id = _message(10, datetime(2026, 1, 1, tzinfo=timezone.utc))
    later = _message(1, datetime(2026, 1, 2, tzinfo=timezone.utc))
    items = [
        build_report_gallery_item(_media("photos/unknown.jpg"), None),
        build_report_gallery_item(_media("photos/later.jpg"), later),
        build_report_gallery_item(_media("photos/b.jpg"), early),
        build_report_gallery_item(_media("photos/a.jpg"), early_lower_id),
    ]

    ordered = sort_report_gallery_items(items)

    assert [item.filename for item in ordered] == [
        "a.jpg",
        "b.jpg",
        "later.jpg",
        "unknown.jpg",
    ]


def test_report_zip_contains_link_only_gallery_and_no_media_binaries() -> None:
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml", "html.j2"]),
    )
    env.filters["score"] = lambda value: "–" if value is None else f"{value:.4f}"
    message = _message(42, datetime(2026, 1, 1, tzinfo=timezone.utc), sender="<Admin>")
    media_gallery = [
        build_report_gallery_item(_media("photos/<script>.jpg"), message),
        build_report_gallery_item(
            _media("telegram/source/video.mp4", media_type="video", source_media_id=uuid.uuid4()),
            message,
        ),
    ]

    report_bytes = ReportWorker()._render_report_zip(
        env,
        job=SimpleNamespace(id=uuid.uuid4(), source_name="Example Group"),
        generated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
        questions=[],
        media_gallery=media_gallery,
        stats=_stats(len(media_gallery)),
        bluf="Keine beantworteten Fragen.",
    )

    with zipfile.ZipFile(BytesIO(report_bytes)) as archive:
        assert set(archive.namelist()) == {
            "report/index.html",
            "report/media_gallery.html",
            "report/assets/report.css",
            "report/assets/report.js",
        }
        index_html = archive.read("report/index.html").decode("utf-8")
        gallery_html = archive.read("report/media_gallery.html").decode("utf-8")

    assert 'href="media_gallery.html"' in index_html
    assert "Mediengalerie <span>2</span>" in index_html
    assert 'href="../photos/&lt;script&gt;.jpg"' in gallery_html
    assert "&lt;Admin&gt;" in gallery_html
    assert "<script>.jpg" not in gallery_html
    assert "Originaldatei öffnen" in gallery_html
    assert "Für direkt synchronisierte Medien" in gallery_html
    assert "<img" not in gallery_html
    assert "<video" not in gallery_html
    assert "<audio" not in gallery_html


def test_gallery_template_renders_empty_state() -> None:
    template_dir = Path(__file__).resolve().parents[1] / "app" / "templates" / "report"
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(["html", "xml", "html.j2"]),
    )

    html = env.get_template("media_gallery.html.j2").render(
        job=SimpleNamespace(source_name="Empty Chat"),
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_gallery=[],
    )

    assert "Keine Medienreferenzen" in html

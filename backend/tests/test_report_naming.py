from datetime import datetime, timezone
from types import SimpleNamespace

from app.models import JobSourceType
from app.services.report_naming import (
    attachment_content_disposition,
    build_report_filename,
    report_date_for_job,
    sanitize_report_source_name,
)


def test_report_filename_uses_readable_safe_group_slug() -> None:
    filename = build_report_filename(
        '  Gruppe / "Blau" & Größe  ',
        datetime(2026, 8, 1, tzinfo=timezone.utc).date(),
    )

    assert filename == "chat-analyse-gruppe-blau-grösse-2026-08-01.zip"
    assert sanitize_report_source_name("///") == "telegram-chat"
    assert len(sanitize_report_source_name("界" * 100).encode("utf-8")) <= 120


def test_report_date_uses_telegram_period_end_in_utc() -> None:
    telegram_job = SimpleNamespace(
        source_type=JobSourceType.telegram_chat,
        report_end_at=datetime(2026, 8, 2, 0, 30, tzinfo=timezone.utc),
    )
    upload_job = SimpleNamespace(
        source_type=JobSourceType.upload,
        report_end_at=None,
    )
    generated_at = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)

    assert report_date_for_job(telegram_job, generated_at).isoformat() == "2026-08-02"
    assert report_date_for_job(upload_job, generated_at).isoformat() == "2026-08-03"


def test_content_disposition_has_ascii_fallback_and_unicode_filename() -> None:
    filename = "chat-analyse-gruppe-grün-2026-08-01.zip"

    header = attachment_content_disposition(filename)

    assert header.startswith('attachment; filename="chat-analyse-gruppe-grun-2026-08-01.zip"')
    assert "filename*=UTF-8''chat-analyse-gruppe-gr%C3%BCn-2026-08-01.zip" in header

from datetime import timezone

import pytest

from app.services.telegram_export import (
    TelegramExportError,
    classify_media,
    extract_media_references,
    normalize_export_path,
    parse_message,
    parse_text,
)


def test_parse_text_joins_telegram_entity_list():
    assert parse_text(["hello ", {"type": "bold", "text": "world"}, "!"]) == "hello world!"


def test_normalize_export_path_rejects_zip_slip_paths():
    with pytest.raises(TelegramExportError):
        normalize_export_path("../photos/x.jpg")
    with pytest.raises(TelegramExportError):
        normalize_export_path("/photos/x.jpg")


def test_parse_message_preserves_core_metadata_and_media():
    parsed = parse_message(
        {
            "id": 42,
            "type": "message",
            "date": "2025-01-01T12:00:00",
            "date_unixtime": "1735732800",
            "edited": "2025-01-01T13:00:00",
            "from": "Alice",
            "from_id": "user123",
            "reply_to_message_id": 41,
            "forwarded_from": "Channel X",
            "reactions": [{"type": "emoji", "count": 3, "emoji": "👍"}],
            "text": ["Siehe ", {"type": "link", "text": "https://example.org"}],
            "photo": "photos/photo_1.jpg",
        }
    )

    assert parsed is not None
    assert parsed.telegram_message_id == 42
    assert parsed.timestamp.tzinfo == timezone.utc
    assert parsed.sender_id == "user123"
    assert parsed.sender_name == "Alice"
    assert parsed.reply_to_message_id == 41
    assert parsed.forwarded_from == "Channel X"
    assert parsed.reactions[0]["emoji"] == "👍"
    assert parsed.text == "Siehe https://example.org"
    assert parsed.media[0].media_type == "image"
    assert parsed.media[0].original_path == "photos/photo_1.jpg"


def test_extract_media_references_marks_missing_video_file():
    refs = extract_media_references(
        {
            "id": 7,
            "type": "message",
            "media_type": "video_file",
            "file": "(File not included. Change data exporting settings to download.)",
        }
    )

    assert len(refs) == 1
    assert refs[0].media_type == "video"
    assert refs[0].missing_reason == "not_included_in_export"


def test_classify_video_from_mime_type_without_extension():
    assert classify_media("files/blob", {"mime_type": "video/mp4"}, "file") == "video"

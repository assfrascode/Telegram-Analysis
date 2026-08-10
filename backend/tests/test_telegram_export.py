import json
from decimal import Decimal
from datetime import timezone
from io import BytesIO

import pytest

from app.services.telegram_export import (
    TelegramExportError,
    classify_media,
    extract_media_references,
    iter_result_messages,
    normalize_export_path,
    parse_message,
    parse_text,
)
from app.services.telegram_html_export import (
    TelegramHtmlPage,
    convert_html_export_pages,
    find_html_export_pages,
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


def test_parse_message_normalizes_decimal_values_for_json_columns():
    large_id = 9_007_199_254_740_993
    parsed = parse_message(
        {
            "id": large_id,
            "type": "message",
            "date_unixtime": Decimal("1735732800"),
            "text": "Decimal metadata",
            "reactions": [
                {
                    "type": "emoji",
                    "count": Decimal("3"),
                    "emoji": "👍",
                    "recent": [{"weight": Decimal("1.25")}],
                }
            ],
            "metadata": {
                "fraction": Decimal("1.25"),
                "large_integer": Decimal(str(large_id)),
                "items": [Decimal("2.5")],
            },
        }
    )

    assert parsed is not None
    assert parsed.telegram_message_id == large_id
    assert parsed.raw["metadata"]["fraction"] == 1.25
    assert isinstance(parsed.raw["metadata"]["fraction"], float)
    assert parsed.raw["metadata"]["large_integer"] == large_id
    assert isinstance(parsed.raw["metadata"]["large_integer"], int)
    assert parsed.reactions[0]["count"] == 3
    assert isinstance(parsed.reactions[0]["count"], int)
    assert parsed.reactions[0]["recent"][0]["weight"] == 1.25
    json.dumps(parsed.raw)
    json.dumps(parsed.reactions)


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


def test_classify_audio_and_voice_media() -> None:
    assert classify_media("files/audio.mp3", {}, "file") == "audio"
    assert classify_media(
        "files/voice.ogg",
        {"mime_type": "audio/ogg", "media_type": "voice_message"},
        "file",
    ) == "voice"
    assert classify_media("files/blob.webm", {"mime_type": "audio/webm"}, "file") == "audio"
    assert classify_media("files/audio.mpeg", {"media_type": "audio_file"}, "file") == "audio"


def test_convert_telegram_html_message_preserves_core_fields_and_media() -> None:
    html = """
    <html>
      <body>
        <div class="page_header"><div class="text">Example Chat</div></div>
        <div class="message default clearfix" id="message42">
          <div class="body">
            <div class="pull_right date details" title="01.01.2025 12:00:00 UTC+00:00">12:00</div>
            <div class="from_name">Alice</div>
            <div class="reply_to details">
              In reply to <a href="#go_to_message41" onclick="return GoToMessage(41)">message</a>
            </div>
            <div class="forwarded body"><div class="from_name">Forwarded from Channel X</div></div>
            <a class="photo_wrap clearfix pull_left" href="photos/photo_1.jpg">photo</a>
            <a class="media_wrap clearfix pull_left video_file" href="video_files/video_1.mp4">video</a>
            <div class="text">Siehe <a href="https://example.org">https://example.org</a><br>zweite Zeile</div>
          </div>
        </div>
      </body>
    </html>
    """

    converted = convert_html_export_pages([TelegramHtmlPage(relative_path="messages.html", html=html)])

    assert converted.messages_total == 1
    assert converted.data["name"] == "Example Chat"
    message = converted.data["messages"][0]
    assert message["id"] == 42
    assert message["from"] == "Alice"
    assert message["reply_to_message_id"] == 41
    assert message["forwarded_from"] == "Channel X"
    assert message["text"] == "Siehe https://example.org\nzweite Zeile"
    assert message["photo"] == "photos/photo_1.jpg"
    assert message["file"] == "video_files/video_1.mp4"

    parsed = parse_message(message)

    assert parsed is not None
    assert parsed.telegram_message_id == 42
    assert parsed.sender_name == "Alice"
    assert parsed.text == "Siehe https://example.org\nzweite Zeile"
    assert [media.media_type for media in parsed.media] == ["image", "video"]


def test_convert_telegram_html_split_pages_are_ordered_by_export_page_number() -> None:
    paths = [
        "ChatExport/messages2.html",
        "ChatExport/files/ignored.html",
        "ChatExport/messages.html",
        "ChatExport/Nested/messages.html",
    ]
    html_by_page = {
        "messages2.html": '<div class="message default clearfix" id="message2"><div class="text">two</div></div>',
        "messages.html": '<div class="message default clearfix" id="message1"><div class="text">one</div></div>',
    }

    pages = [
        TelegramHtmlPage(relative_path=path.split("/")[-1], html=html_by_page[path.split("/")[-1]])
        for path in find_html_export_pages(paths)
    ]
    converted = convert_html_export_pages(pages)

    assert [page.relative_path for page in pages] == ["messages.html", "messages2.html"]
    assert [message["id"] for message in converted.data["messages"]] == [1, 2]


def test_convert_telegram_html_unsafe_media_path_does_not_break_import() -> None:
    html = """
    <div class="message default clearfix" id="message7">
      <a class="photo_wrap clearfix pull_left" href="../photos/escape.jpg">photo</a>
      <div class="text">unsafe media</div>
    </div>
    """

    converted = convert_html_export_pages([TelegramHtmlPage(relative_path="messages.html", html=html)])
    parsed = parse_message(converted.data["messages"][0])

    assert parsed is not None
    assert parsed.media[0].original_path == "../photos/escape.jpg"
    assert parsed.media[0].missing_reason == "unsafe_path"


def test_streaming_json_parser_rejects_oversized_single_message() -> None:
    payload = json.dumps({"messages": [{"id": 1, "text": "x" * 500}]}).encode()

    with pytest.raises(TelegramExportError, match="size limit|per-message limit"):
        list(iter_result_messages(BytesIO(payload), max_messages=10, max_message_bytes=100))


def test_streaming_json_parser_rejects_excess_message_count() -> None:
    payload = json.dumps({"messages": [{"id": 1}, {"id": 2}]}).encode()

    with pytest.raises(TelegramExportError, match="too many messages"):
        list(iter_result_messages(BytesIO(payload), max_messages=1, max_message_bytes=1000))

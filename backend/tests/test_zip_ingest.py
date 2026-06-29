import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from app.services.zip_ingest import extract_zip_to_minio


SETTINGS = SimpleNamespace(
    max_zip_files=100,
    max_file_bytes=1024 * 1024,
    max_extracted_bytes=10 * 1024 * 1024,
)


class FakeMinio:
    def __init__(self, archive_bytes: bytes) -> None:
        self.archive_bytes = archive_bytes
        self.objects: dict[str, dict] = {}

    def fget_object(self, bucket: str, object_key: str, path: str) -> None:
        Path(path).write_bytes(self.archive_bytes)

    def put_object(
        self,
        bucket: str,
        object_key: str,
        stream,
        length: int,
        part_size: int | None = None,
        content_type: str | None = None,
    ) -> None:
        data = stream.read()
        assert len(data) == length
        self.objects[object_key] = {"data": data, "content_type": content_type}


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            archive.writestr(path, data)
    return buffer.getvalue()


def test_extract_zip_prefers_existing_result_json_over_html_fallback() -> None:
    result_json = b'{"messages":[{"id":99,"text":"json wins"}]}'
    client = FakeMinio(
        _zip_bytes(
            {
                "Chat/messages.html": '<div class="message default clearfix" id="message1"></div>',
                "Chat/result.json": result_json,
            }
        )
    )

    result = extract_zip_to_minio(
        client=client,
        bucket="bucket",
        upload_object_key="upload.zip",
        extracted_prefix="users/u/jobs/j/extract/",
        settings=SETTINGS,
    )

    assert result.result_json_object_key == "users/u/jobs/j/extract/Chat/result.json"
    assert result.source_format == "json"
    assert result.html_pages_total == 0
    assert client.objects[result.result_json_object_key]["data"] == result_json


def test_extract_zip_generates_result_json_from_html_only_export() -> None:
    html = """
    <div class="page_header"><div class="text">HTML Chat</div></div>
    <div class="message default clearfix" id="message5">
      <div class="pull_right date details" title="01.01.2025 12:00:00 UTC+00:00">12:00</div>
      <div class="from_name">Alice</div>
      <a class="photo_wrap clearfix pull_left" href="photos/photo_5.jpg">photo</a>
      <div class="text">hello</div>
    </div>
    """
    client = FakeMinio(
        _zip_bytes(
            {
                "Chat/messages.html": html,
                "Chat/photos/photo_5.jpg": b"image",
            }
        )
    )

    result = extract_zip_to_minio(
        client=client,
        bucket="bucket",
        upload_object_key="upload.zip",
        extracted_prefix="users/u/jobs/j/extract/",
        settings=SETTINGS,
    )

    assert result.result_json_object_key == "users/u/jobs/j/extract/Chat/result.json"
    assert result.source_format == "html"
    assert result.html_pages_total == 1
    assert result.html_messages_total == 1

    generated = json.loads(client.objects[result.result_json_object_key]["data"])
    assert generated["name"] == "HTML Chat"
    assert generated["messages"][0]["id"] == 5
    assert generated["messages"][0]["photo"] == "photos/photo_5.jpg"
    assert "users/u/jobs/j/extract/Chat/photos/photo_5.jpg" in client.objects


def test_extract_zip_without_json_or_html_returns_no_parseable_export() -> None:
    client = FakeMinio(_zip_bytes({"Chat/readme.txt": "not a Telegram export"}))

    result = extract_zip_to_minio(
        client=client,
        bucket="bucket",
        upload_object_key="upload.zip",
        extracted_prefix="users/u/jobs/j/extract/",
        settings=SETTINGS,
    )

    assert result.result_json_object_key is None
    assert result.source_format == "json"
    assert result.html_pages_total == 0
    assert result.html_messages_total == 0

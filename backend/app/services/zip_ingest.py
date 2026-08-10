
import json
import logging
import os
import posixpath
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from minio import Minio
from minio.deleteobjects import DeleteObject

from app.config import Settings
from app.services.telegram_export import read_result_name
from app.services.telegram_html_export import (
    TelegramHtmlExportError,
    TelegramHtmlPage,
    find_html_export_pages,
    parse_html_export_page,
)
from app.services.telegram_export import normalize_export_name


class ZipSecurityError(ValueError):
    pass


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExtractedFile:
    relative_path: str
    object_key: str
    size_bytes: int


@dataclass(frozen=True)
class ExtractionResult:
    extracted_prefix: str
    files_total: int
    bytes_total: int
    result_json_object_key: str | None
    source_format: str = "json"
    html_pages_total: int = 0
    html_messages_total: int = 0
    source_name: str | None = None


def normalize_zip_member_path(name: str) -> str:
    cleaned = name.replace("\\", "/").strip()
    if not cleaned:
        raise ZipSecurityError("empty member name")
    if cleaned.startswith("/"):
        raise ZipSecurityError(f"absolute paths are not allowed: {name!r}")

    normalized = posixpath.normpath(cleaned)
    pure = PurePosixPath(normalized)
    if normalized in {".", ""} or any(part in {"", ".", ".."} for part in pure.parts):
        raise ZipSecurityError(f"unsafe member path: {name!r}")
    return str(pure)


def validate_zip_infos(infos: list[zipfile.ZipInfo], settings: Settings) -> tuple[int, int]:
    files_total = 0
    bytes_total = 0

    if len(infos) > settings.max_zip_files:
        raise ZipSecurityError(f"ZIP contains too many entries: {len(infos)}")

    seen_paths: set[str] = set()
    for info in infos:
        if info.is_dir():
            continue

        # Reject symlinks and special Unix file types when metadata is present.
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode in {0o120000, 0o060000, 0o020000, 0o010000}:
            raise ZipSecurityError(f"unsupported special file in ZIP: {info.filename}")

        normalized_path = normalize_zip_member_path(info.filename)
        if normalized_path in seen_paths:
            raise ZipSecurityError(f"duplicate ZIP member path: {normalized_path}")
        seen_paths.add(normalized_path)
        if info.flag_bits & 0x1:
            raise ZipSecurityError(f"encrypted ZIP members are not supported: {info.filename}")
        if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ZipSecurityError(f"unsupported ZIP compression method: {info.filename}")
        files_total += 1
        bytes_total += info.file_size

        if info.file_size > settings.max_file_bytes:
            raise ZipSecurityError(f"ZIP member exceeds max file size: {info.filename}")
        if bytes_total > settings.max_extracted_bytes:
            raise ZipSecurityError("ZIP exceeds configured max extracted size")
        if info.file_size > 0 and info.compress_size == 0:
            raise ZipSecurityError(f"invalid compressed size for {info.filename}")
        if info.compress_size > 0 and info.file_size / info.compress_size > 200:
            raise ZipSecurityError(f"suspicious compression ratio for {info.filename}")

    return files_total, bytes_total


def download_object_to_tempfile(
    client: Minio,
    bucket: str,
    object_key: str,
    *,
    max_bytes: int,
) -> str:
    temp = tempfile.NamedTemporaryFile(prefix="chat-analyse-upload-", suffix=".zip", delete=False)
    response = None
    received = 0
    try:
        response = client.get_object(bucket, object_key)
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > max_bytes:
                raise ZipSecurityError("uploaded ZIP exceeds configured max size")
            temp.write(chunk)
        temp.close()
        return temp.name
    except Exception:
        temp.close()
        try:
            os.unlink(temp.name)
        except FileNotFoundError:
            pass
        raise
    finally:
        if response is not None:
            try:
                response.close()
            finally:
                response.release_conn()


def _cleanup_extracted_prefix(client: Minio, bucket: str, prefix: str) -> None:
    """Best-effort removal of objects written by a failed extraction attempt."""

    try:
        objects = (
            DeleteObject(item.object_name)
            for item in client.list_objects(bucket, prefix=prefix, recursive=True)
        )
        errors = list(client.remove_objects(bucket, objects))
        if errors:
            logger.warning(
                "Could not remove %d objects after failed ZIP extraction",
                len(errors),
            )
    except Exception:
        # Preserve the original validation/extraction error. Operators should
        # still configure a lifecycle policy as a final orphan-data backstop.
        logger.exception("Could not clean failed ZIP extraction prefix")


def _convert_html_export_to_result_json(
    *,
    archive: zipfile.ZipFile,
    infos_by_path: dict[str, zipfile.ZipInfo],
    html_page_paths: list[str],
    client: Minio,
    bucket: str,
    extracted_prefix: str,
    settings: Settings,
) -> tuple[str | None, int, int, str | None]:
    if not html_page_paths:
        return None, 0, 0, None

    first_page = PurePosixPath(html_page_paths[0])
    export_root = "" if str(first_page.parent) == "." else f"{first_page.parent}/"
    message_spool = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    pages_total = 0
    messages_total = 0
    source_name: str | None = None
    first_message = True
    for relative_path in html_page_paths:
        info = infos_by_path.get(relative_path)
        if info is None:
            continue
        page_path_in_export = relative_path[len(export_root) :] if relative_path.startswith(export_root) else relative_path
        if info.file_size > settings.max_html_page_bytes:
            raise TelegramHtmlExportError(f"HTML message page exceeds configured size limit: {relative_path}")
        with archive.open(info, mode="r") as source:
            html = source.read(settings.max_html_page_bytes + 1)
        if len(html) > settings.max_html_page_bytes:
            raise TelegramHtmlExportError(f"HTML message page exceeds configured size limit: {relative_path}")
        page_name, messages = parse_html_export_page(
            TelegramHtmlPage(relative_path=page_path_in_export, html=html),
            max_messages=settings.max_telegram_messages_per_export,
            max_message_bytes=settings.max_telegram_message_chars,
        )
        source_name = source_name or normalize_export_name(page_name)
        pages_total += 1
        messages_total += len(messages)
        if messages_total > settings.max_telegram_messages_per_export:
            raise TelegramHtmlExportError("HTML export contains too many messages")
        for message in messages:
            encoded = json.dumps(
                message,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if not first_message:
                message_spool.write(b",")
            message_spool.write(encoded)
            if message_spool.tell() > settings.max_file_bytes:
                raise TelegramHtmlExportError("Converted HTML export exceeds configured file size limit")
            first_message = False

    if messages_total == 0:
        message_spool.close()
        return None, pages_total, 0, source_name

    output = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    try:
        header = json.dumps(
            {
                "name": source_name or "Telegram HTML export",
                "type": "telegram_html_export",
                "exported_from": "telegram_desktop_html",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        output.write(header[:-1])
        output.write(b',"messages":[')
        message_spool.seek(0)
        while chunk := message_spool.read(1024 * 1024):
            output.write(chunk)
        output.write(b"]}")
        length = output.tell()
        if length > settings.max_file_bytes:
            raise TelegramHtmlExportError("Converted HTML export exceeds configured file size limit")
        output.seek(0)
        result_json_object_key = f"{extracted_prefix}{export_root}result.json"
        client.put_object(
            bucket,
            result_json_object_key,
            output,
            length=length,
            content_type="application/json",
        )
        return result_json_object_key, pages_total, messages_total, source_name
    finally:
        output.close()
        message_spool.close()


def extract_zip_to_minio(
    *,
    client: Minio,
    bucket: str,
    upload_object_key: str,
    extracted_prefix: str,
    settings: Settings,
) -> ExtractionResult:
    """Safely extract an uploaded Telegram export ZIP into a MinIO prefix.

    The ZIP itself is spooled to a temporary file because Python's zipfile needs
    random access to the central directory. Individual members are streamed from
    ZipExtFile to MinIO and are not loaded into process memory.
    """
    temp_path = download_object_to_tempfile(
        client,
        bucket,
        upload_object_key,
        max_bytes=settings.max_upload_bytes,
    )
    files_total = 0
    bytes_total = 0
    result_json_object_key: str | None = None
    result_json_relative_path: str | None = None
    source_format = "json"
    html_pages_total = 0
    html_messages_total = 0
    source_name: str | None = None

    try:
        if not zipfile.is_zipfile(temp_path):
            raise ZipSecurityError("uploaded file is not a valid ZIP archive")

        with zipfile.ZipFile(temp_path, mode="r") as archive:
            infos = archive.infolist()
            files_total, bytes_total = validate_zip_infos(infos, settings)
            infos_by_path: dict[str, zipfile.ZipInfo] = {}

            for info in infos:
                if info.is_dir():
                    continue
                relative_path = normalize_zip_member_path(info.filename)
                infos_by_path[relative_path] = info
                object_key = f"{extracted_prefix}{relative_path}"

                with archive.open(info, mode="r") as source:
                    client.put_object(
                        bucket,
                        object_key,
                        source,
                        length=info.file_size,
                        part_size=10 * 1024 * 1024,
                    )

                if PurePosixPath(relative_path).name == "result.json":
                    # Prefer the shallowest result.json if several exist.
                    if result_json_relative_path is None or relative_path.count("/") < result_json_relative_path.count("/"):
                        result_json_relative_path = relative_path
                        result_json_object_key = object_key

            if result_json_object_key is None:
                html_page_paths = find_html_export_pages(infos_by_path.keys())
                try:
                    (
                        result_json_object_key,
                        html_pages_total,
                        html_messages_total,
                        source_name,
                    ) = _convert_html_export_to_result_json(
                        archive=archive,
                        infos_by_path=infos_by_path,
                        html_page_paths=html_page_paths,
                        client=client,
                        bucket=bucket,
                        extracted_prefix=extracted_prefix,
                        settings=settings,
                    )
                except TelegramHtmlExportError as exc:
                    raise ZipSecurityError(f"Telegram HTML export could not be converted: {exc}") from exc
                if result_json_object_key is not None:
                    source_format = "html"
            elif result_json_relative_path is not None:
                result_info = infos_by_path.get(result_json_relative_path)
                if result_info is not None:
                    with archive.open(result_info, mode="r") as source:
                        source_name = read_result_name(
                            source,
                            max_string_bytes=settings.max_telegram_message_chars,
                        )

        return ExtractionResult(
            extracted_prefix=extracted_prefix,
            files_total=files_total,
            bytes_total=bytes_total,
            result_json_object_key=result_json_object_key,
            source_format=source_format,
            html_pages_total=html_pages_total,
            html_messages_total=html_messages_total,
            source_name=source_name,
        )
    except Exception:
        _cleanup_extracted_prefix(client, bucket, extracted_prefix)
        raise
    finally:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass

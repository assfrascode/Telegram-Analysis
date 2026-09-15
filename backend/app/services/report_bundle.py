import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from minio import Minio

from app.services.telegram_html_export import find_html_export_pages
from app.services.zip_ingest import ZipSecurityError, normalize_zip_member_path


class ReportBundleError(ValueError):
    pass


class ReportBundleConflictError(ReportBundleError):
    pass


@dataclass(frozen=True, slots=True)
class CollectedExportMedia:
    message_id: int
    path: str
    object_key: str | None
    media_type: str
    mime_type: str | None = None


def _desktop_message(message: dict[str, Any], media: list[CollectedExportMedia]) -> dict[str, Any]:
    timestamp = message.get("timestamp")
    if isinstance(timestamp, datetime):
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        timestamp = timestamp.astimezone(timezone.utc)
        date = timestamp.isoformat()
        date_unixtime = str(int(timestamp.timestamp()))
    else:
        date = str(timestamp or "")
        date_unixtime = ""

    text = str(message.get("text") or "")
    source_type = str(message.get("message_type") or "message")
    result: dict[str, Any] = {
        "id": int(message["telegram_message_id"]),
        # Telegram Desktop represents photos/documents as ordinary messages;
        # their media fields below carry the attachment type.
        "type": "service" if source_type == "service" else "message",
        "date": date,
        "date_unixtime": date_unixtime,
        "from": message.get("sender_name"),
        "from_id": message.get("sender_id"),
        "text": text,
        "text_entities": [{"type": "plain", "text": text}] if text else [],
    }
    optional_fields = {
        "edited": message.get("edited_timestamp"),
        "reply_to_message_id": message.get("reply_to_message_id"),
        "forwarded_from": message.get("forwarded_from"),
        "reactions": message.get("reactions"),
    }
    for key, value in optional_fields.items():
        if value not in (None, "", []):
            result[key] = value.isoformat() if isinstance(value, datetime) else value

    available_media = [item for item in media if item.object_key]
    if available_media:
        first = available_media[0]
        if first.media_type == "image":
            result["photo"] = first.path
        else:
            result["file"] = first.path
            if first.mime_type:
                result["mime_type"] = first.mime_type
        if len(available_media) > 1:
            result["files"] = [item.path for item in available_media]
    return result


def build_collected_chat_bundle(
    *,
    client: Minio,
    bucket: str,
    report_object_key: str,
    chat_title: str,
    chat_type: str,
    telegram_chat_id: int,
    messages: list[dict[str, Any]],
    media: list[CollectedExportMedia],
) -> str:
    """Recreate a portable Telegram JSON export and add the static report.

    Collector data is normalized rather than being a byte-for-byte Telegram
    Desktop export. The generated result.json follows Desktop's useful fields,
    while media paths deliberately match the paths embedded in the report.
    """
    bundle_temp = tempfile.NamedTemporaryFile(
        prefix="chat-analyse-collected-chat-", suffix=".zip", delete=False
    )
    report_temp = tempfile.NamedTemporaryFile(
        prefix="chat-analyse-report-", suffix=".zip", delete=False
    )
    bundle_temp.close()
    report_temp.close()
    try:
        media_by_message: dict[int, list[CollectedExportMedia]] = {}
        normalized_media: list[tuple[CollectedExportMedia, str]] = []
        used_paths = {"result.json"}
        for item in media:
            try:
                path = normalize_zip_member_path(item.path)
            except ZipSecurityError as exc:
                raise ReportBundleError(str(exc)) from exc
            if path == "report" or path.startswith("report/"):
                raise ReportBundleConflictError("Collected media conflicts with the report directory")
            if path in used_paths:
                raise ReportBundleConflictError(f"Duplicate collected media path: {path}")
            used_paths.add(path)
            normalized = CollectedExportMedia(
                message_id=item.message_id,
                path=path,
                object_key=item.object_key,
                media_type=item.media_type,
                mime_type=item.mime_type,
            )
            normalized_media.append((normalized, path))
            media_by_message.setdefault(item.message_id, []).append(normalized)

        export = {
            "name": chat_title,
            "type": chat_type,
            "id": telegram_chat_id,
            "messages": [
                _desktop_message(
                    message,
                    media_by_message.get(int(message["telegram_message_id"]), []),
                )
                for message in messages
            ],
        }
        with zipfile.ZipFile(
            bundle_temp.name, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
        ) as bundle:
            bundle.writestr(
                "result.json",
                json.dumps(export, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
            )
            for item, path in normalized_media:
                if not item.object_key:
                    continue
                media_temp = tempfile.NamedTemporaryFile(
                    prefix="chat-analyse-media-", delete=False
                )
                media_temp.close()
                try:
                    client.fget_object(bucket, item.object_key, media_temp.name)
                    bundle.write(media_temp.name, arcname=path)
                finally:
                    remove_temp_file(media_temp.name)

        client.fget_object(bucket, report_object_key, report_temp.name)
        append_report_to_archive(bundle_temp.name, report_temp.name)
        return bundle_temp.name
    except Exception:
        remove_temp_file(bundle_temp.name)
        raise
    finally:
        remove_temp_file(report_temp.name)


def _normalized_member_path(info: zipfile.ZipInfo) -> str:
    name = info.filename.rstrip("/") if info.is_dir() else info.filename
    try:
        return normalize_zip_member_path(name)
    except ZipSecurityError as exc:
        raise ReportBundleError(str(exc)) from exc


def _export_root(infos: list[zipfile.ZipInfo]) -> str:
    paths = [_normalized_member_path(info) for info in infos if not info.is_dir()]
    result_path: str | None = None
    for path in paths:
        if PurePosixPath(path).name != "result.json":
            continue
        if result_path is None or path.count("/") < result_path.count("/"):
            result_path = path

    if result_path is not None:
        parent = PurePosixPath(result_path).parent
        return "" if str(parent) == "." else f"{parent}/"

    html_pages = find_html_export_pages(paths)
    if html_pages:
        parent = PurePosixPath(html_pages[0]).parent
        return "" if str(parent) == "." else f"{parent}/"

    raise ReportBundleError("Telegram export root could not be located")


def _copy_zip_info(info: zipfile.ZipInfo, filename: str) -> zipfile.ZipInfo:
    copied = zipfile.ZipInfo(filename=filename, date_time=info.date_time)
    copied.compress_type = info.compress_type
    copied.comment = info.comment
    copied.create_system = info.create_system
    copied.create_version = info.create_version
    copied.extract_version = info.extract_version
    copied.internal_attr = info.internal_attr
    copied.external_attr = info.external_attr
    copied.volume = info.volume
    return copied


def append_report_to_archive(bundle_path: str, report_path: str) -> None:
    if not zipfile.is_zipfile(bundle_path):
        raise ReportBundleError("Original upload is not a valid ZIP archive")
    if not zipfile.is_zipfile(report_path):
        raise ReportBundleError("Generated report is not a valid ZIP archive")

    with zipfile.ZipFile(bundle_path, mode="r") as bundle:
        bundle_infos = bundle.infolist()
        export_root = _export_root(bundle_infos)
        existing_paths = {_normalized_member_path(info) for info in bundle_infos}

    report_root = f"{export_root}report"
    if any(path == report_root or path.startswith(f"{report_root}/") for path in existing_paths):
        raise ReportBundleConflictError(
            f"Original upload already contains a report directory at {report_root}/"
        )

    with (
        zipfile.ZipFile(report_path, mode="r") as report,
        zipfile.ZipFile(bundle_path, mode="a", allowZip64=True) as bundle,
    ):
        report_infos = report.infolist()
        normalized_report_paths: list[tuple[zipfile.ZipInfo, str]] = []
        for info in report_infos:
            path = _normalized_member_path(info)
            if path != "report" and not path.startswith("report/"):
                raise ReportBundleError(f"Unexpected generated report path: {path}")
            normalized_report_paths.append((info, path))

        for info, path in normalized_report_paths:
            destination = f"{export_root}{path}"
            copied_info = _copy_zip_info(info, f"{destination}/" if info.is_dir() else destination)
            if info.is_dir():
                bundle.writestr(copied_info, b"")
                continue
            with report.open(info, mode="r") as source, bundle.open(
                copied_info, mode="w", force_zip64=True
            ) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def build_report_bundle(
    *,
    client: Minio,
    bucket: str,
    upload_object_key: str,
    report_object_key: str,
) -> str:
    bundle_temp = tempfile.NamedTemporaryFile(
        prefix="chat-analyse-download-all-", suffix=".zip", delete=False
    )
    report_temp = tempfile.NamedTemporaryFile(
        prefix="chat-analyse-report-", suffix=".zip", delete=False
    )
    bundle_temp.close()
    report_temp.close()

    try:
        client.fget_object(bucket, upload_object_key, bundle_temp.name)
        client.fget_object(bucket, report_object_key, report_temp.name)
        append_report_to_archive(bundle_temp.name, report_temp.name)
        return bundle_temp.name
    except Exception:
        remove_temp_file(bundle_temp.name)
        raise
    finally:
        remove_temp_file(report_temp.name)


def remove_temp_file(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass

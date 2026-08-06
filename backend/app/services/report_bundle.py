import os
import shutil
import tempfile
import zipfile
from pathlib import PurePosixPath

from minio import Minio

from app.services.telegram_html_export import find_html_export_pages
from app.services.zip_ingest import ZipSecurityError, normalize_zip_member_path


class ReportBundleError(ValueError):
    pass


class ReportBundleConflictError(ReportBundleError):
    pass


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

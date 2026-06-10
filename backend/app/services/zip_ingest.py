
import posixpath
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

from minio import Minio

from app.config import Settings


class ZipSecurityError(ValueError):
    pass


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

    for info in infos:
        if info.is_dir():
            continue

        # Reject symlinks and special Unix file types when metadata is present.
        unix_mode = (info.external_attr >> 16) & 0o170000
        if unix_mode in {0o120000, 0o060000, 0o020000, 0o010000}:
            raise ZipSecurityError(f"unsupported special file in ZIP: {info.filename}")

        normalize_zip_member_path(info.filename)
        files_total += 1
        bytes_total += info.file_size

        if info.file_size > settings.max_file_bytes:
            raise ZipSecurityError(f"ZIP member exceeds max file size: {info.filename}")
        if bytes_total > settings.max_extracted_bytes:
            raise ZipSecurityError("ZIP exceeds configured max extracted size")
        if info.compress_size > 0 and info.file_size / info.compress_size > 200:
            raise ZipSecurityError(f"suspicious compression ratio for {info.filename}")

    return files_total, bytes_total


def download_object_to_tempfile(client: Minio, bucket: str, object_key: str) -> str:
    temp = tempfile.NamedTemporaryFile(prefix="chat-analyse-upload-", suffix=".zip", delete=False)
    temp.close()
    client.fget_object(bucket, object_key, temp.name)
    return temp.name


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
    temp_path = download_object_to_tempfile(client, bucket, upload_object_key)
    files_total = 0
    bytes_total = 0
    result_json_object_key: str | None = None
    result_json_relative_path: str | None = None

    try:
        if not zipfile.is_zipfile(temp_path):
            raise ZipSecurityError("uploaded file is not a valid ZIP archive")

        with zipfile.ZipFile(temp_path, mode="r") as archive:
            infos = archive.infolist()
            files_total, bytes_total = validate_zip_infos(infos, settings)

            for info in infos:
                if info.is_dir():
                    continue
                relative_path = normalize_zip_member_path(info.filename)
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

        return ExtractionResult(
            extracted_prefix=extracted_prefix,
            files_total=files_total,
            bytes_total=bytes_total,
            result_json_object_key=result_json_object_key,
        )
    finally:
        try:
            import os

            os.unlink(temp_path)
        except FileNotFoundError:
            pass

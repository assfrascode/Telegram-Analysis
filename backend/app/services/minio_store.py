from __future__ import annotations

from datetime import timedelta
from io import BytesIO
from typing import Any, BinaryIO, Iterator

from minio import Minio
from minio.error import S3Error

from app.config import get_settings

settings = get_settings()


def minio_client() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


def public_minio_client() -> Minio:
    """Client used only to generate browser-facing presigned URLs.

    The backend talks to MinIO via the Docker-internal endpoint (for example
    ``minio:9000``). A browser on the host machine cannot resolve that name, so
    presigned URLs must be signed against the public endpoint, usually
    ``localhost:9000`` in local development.
    """
    return Minio(
        settings.minio_public_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        region="us-east-1",
    )


def ensure_bucket() -> None:
    client = minio_client()
    try:
        exists = client.bucket_exists(settings.minio_bucket)
        if not exists:
            client.make_bucket(settings.minio_bucket)
    except S3Error as exc:
        raise RuntimeError(f"Could not ensure MinIO bucket: {exc}") from exc


def presigned_put(object_key: str, expires: timedelta = timedelta(hours=12)) -> str:
    client = public_minio_client()
    return client.presigned_put_object(settings.minio_bucket, object_key, expires=expires)


def presigned_get(object_key: str, expires: timedelta = timedelta(hours=1)) -> str:
    client = public_minio_client()
    return client.presigned_get_object(settings.minio_bucket, object_key, expires=expires)


def presigned_get_internal(object_key: str, expires: timedelta = timedelta(hours=1)) -> str:
    """Generate a MinIO URL for services inside the Docker/internal network.

    Use this when the vLLM server can resolve ``MINIO_ENDPOINT`` such as
    ``minio:9000``. Browser-facing downloads should keep using
    ``presigned_get``.
    """
    client = minio_client()
    return client.presigned_get_object(settings.minio_bucket, object_key, expires=expires)


def put_bytes(object_key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    client = minio_client()
    client.put_object(
        settings.minio_bucket,
        object_key,
        BytesIO(data),
        length=len(data),
        content_type=content_type,
    )


def put_stream(
    object_key: str,
    stream: BinaryIO,
    length: int,
    content_type: str = "application/octet-stream",
) -> None:
    client = minio_client()
    client.put_object(
        settings.minio_bucket,
        object_key,
        stream,
        length=length,
        content_type=content_type,
    )


def get_bytes(object_key: str) -> bytes:
    client = minio_client()
    response = client.get_object(settings.minio_bucket, object_key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def stat_object(object_key: str):
    client = minio_client()
    return client.stat_object(settings.minio_bucket, object_key)


def object_exists(object_key: str) -> bool:
    try:
        stat_object(object_key)
        return True
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
            return False
        raise


def list_objects(prefix: str, recursive: bool = True) -> Iterator[Any]:
    client = minio_client()
    yield from client.list_objects(settings.minio_bucket, prefix=prefix, recursive=recursive)


def extracted_prefix(owner_user_id, job_id) -> str:
    return f"users/{owner_user_id}/jobs/{job_id}/extract/"


def get_stream(object_key: str):
    """Return a streaming MinIO response. Caller must close/release it."""
    client = minio_client()
    return client.get_object(settings.minio_bucket, object_key)

from __future__ import annotations

import base64
import mimetypes
from datetime import timedelta

from app.config import get_settings
from app.services.minio_store import get_bytes, presigned_get_internal

settings = get_settings()


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def guess_media_mime_type(path: str, media_type: str) -> str:
    mime_type, _ = mimetypes.guess_type(path)
    if mime_type:
        return mime_type
    if media_type == "video":
        return "video/mp4"
    return "image/jpeg"


def build_data_url(*, object_key: str, mime_type: str) -> str:
    data = get_bytes(object_key)
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def build_internal_presigned_url(object_key: str) -> str:
    return presigned_get_internal(object_key, expires=timedelta(hours=2))


def build_media_source_url(*, object_key: str, original_path: str, media_type: str) -> str:
    transport = settings.media_analysis_transport.lower().strip()
    if transport == "internal_presigned_url":
        return build_internal_presigned_url(object_key)
    if transport != "data_url":
        raise ValueError(
            "Unsupported MEDIA_ANALYSIS_TRANSPORT. Use 'data_url' or 'internal_presigned_url'."
        )
    return build_data_url(
        object_key=object_key,
        mime_type=guess_media_mime_type(original_path, media_type),
    )

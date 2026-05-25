import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.dependencies import get_current_user
from app.models import Upload, UploadStatus, User
from app.schemas import UploadCreateRequest, UploadCreateResponse
from app.services.access_control import get_owned_upload_or_404
from app.services.minio_store import presigned_put, put_stream, stat_object

settings = get_settings()
router = APIRouter(prefix="/uploads", tags=["uploads"])


def _safe_upload_name(filename: str) -> str:
    safe_name = filename.replace("/", "_").replace("\\", "_").strip()
    return safe_name or "telegram-export.zip"


def _validate_declared_upload(payload: UploadCreateRequest) -> None:
    if payload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Upload exceeds configured max size",
        )
    if not payload.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .zip uploads are supported")


@router.post("", response_model=UploadCreateResponse)
async def create_upload(
    payload: UploadCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UploadCreateResponse:
    _validate_declared_upload(payload)

    upload_id = uuid.uuid4()
    safe_name = _safe_upload_name(payload.filename)
    object_key = f"users/{user.id}/uploads/{upload_id}/{safe_name}"

    upload = Upload(
        id=upload_id,
        owner_user_id=user.id,
        filename=safe_name,
        size_bytes=payload.size_bytes,
        object_key=object_key,
        status=UploadStatus.created,
    )
    session.add(upload)
    await session.commit()

    return UploadCreateResponse(
        upload_id=upload.id,
        object_key=upload.object_key,
        presigned_put_url=presigned_put(upload.object_key),
        backend_upload_url=f"/uploads/{upload.id}/content",
    )


@router.post("/{upload_id}/content")
async def upload_content(
    upload_id: uuid.UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    upload = await get_owned_upload_or_404(session, upload_id=upload_id, user=user)

    if upload.status not in {UploadStatus.created, UploadStatus.uploaded}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload is not writable")

    if upload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload too large")

    content_type = file.content_type or "application/zip"
    await file.seek(0)
    await asyncio.to_thread(
        put_stream,
        upload.object_key,
        file.file,
        upload.size_bytes,
        content_type,
    )

    try:
        stat = await asyncio.to_thread(stat_object, upload.object_key)
    except S3Error as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Uploaded object could not be verified") from exc

    if getattr(stat, "size", None) != upload.size_bytes:
        upload.status = UploadStatus.rejected
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object size does not match declared size")

    upload.status = UploadStatus.uploaded
    upload.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True, "upload_id": str(upload.id)}


@router.post("/{upload_id}/complete")
async def complete_upload(
    upload_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    upload = await get_owned_upload_or_404(session, upload_id=upload_id, user=user)

    try:
        stat = await asyncio.to_thread(stat_object, upload.object_key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object not found") from exc
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Uploaded object could not be verified") from exc

    if getattr(stat, "size", None) != upload.size_bytes:
        upload.status = UploadStatus.rejected
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded object size does not match declared size")

    upload.status = UploadStatus.uploaded
    upload.completed_at = datetime.now(timezone.utc)
    await session.commit()
    return {"ok": True, "upload_id": str(upload.id)}

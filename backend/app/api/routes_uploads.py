import asyncio
import tempfile
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.dependencies import get_current_user
from app.models import Upload, UploadStatus, User
from app.schemas import UploadCreateRequest, UploadCreateResponse
from app.services.access_control import get_owned_upload_or_404
from app.services.minio_store import put_stream, remove_object, stat_object

settings = get_settings()
router = APIRouter(prefix="/uploads", tags=["uploads"])
upload_slots = asyncio.Semaphore(settings.max_concurrent_uploads)


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
        backend_upload_url=f"/uploads/{upload.id}/content",
    )


async def _reject_upload(session: AsyncSession, upload: Upload) -> None:
    upload.status = UploadStatus.rejected
    await session.commit()
    try:
        await asyncio.to_thread(remove_object, upload.object_key)
    except Exception:
        # The object may not exist yet. A failed best-effort cleanup must not
        # turn a bounded client error into an unbounded retry loop.
        pass


async def _reserve_upload(session: AsyncSession, upload: Upload, user: User) -> None:
    """Lock a still-created upload without waiting behind another writer."""
    result = await session.execute(
        select(Upload.id)
        .where(
            Upload.id == upload.id,
            Upload.owner_user_id == user.id,
            Upload.status == UploadStatus.created,
        )
        .with_for_update(skip_locked=True)
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload is not writable")
    upload.status = UploadStatus.uploading
    await session.flush()


@router.put("/{upload_id}/content")
async def upload_content(
    upload_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    upload = await get_owned_upload_or_404(session, upload_id=upload_id, user=user)

    if upload.status != UploadStatus.created:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload is not writable")

    if upload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload too large")

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/zip":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Content-Type must be application/zip",
        )

    raw_content_length = request.headers.get("content-length")
    content_length: int | None = None
    if raw_content_length is not None:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Content-Length") from exc
        if content_length > settings.max_upload_bytes:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload too large")

    if upload_slots.locked():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Upload capacity is currently busy",
            headers={"Retry-After": "1"},
        )
    await upload_slots.acquire()
    spool = None
    object_key = upload.object_key
    try:
        await _reserve_upload(session, upload, user)
        if content_length is not None and content_length != upload.size_bytes:
            await _reject_upload(session, upload)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload size does not match declared size",
            )

        spool = tempfile.SpooledTemporaryFile(max_size=min(8 * 1024 * 1024, settings.max_upload_bytes))
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > settings.max_upload_bytes:
                await _reject_upload(session, upload)
                raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload too large")
            if received > upload.size_bytes:
                await _reject_upload(session, upload)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Upload size does not match declared size",
                )
            spool.write(chunk)

        if received != upload.size_bytes:
            await _reject_upload(session, upload)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload size does not match declared size")

        spool.seek(0)
        try:
            await asyncio.to_thread(
                put_stream,
                object_key,
                spool,
                received,
                "application/zip",
            )
        except Exception as exc:
            try:
                await asyncio.to_thread(remove_object, object_key)
            except Exception:
                pass
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Upload storage failed",
            ) from exc

        try:
            stat = await asyncio.to_thread(stat_object, object_key)
        except Exception as exc:
            try:
                await asyncio.to_thread(remove_object, object_key)
            except Exception:
                pass
            await session.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Uploaded object could not be verified",
            ) from exc

        if getattr(stat, "size", None) != upload.size_bytes:
            await _reject_upload(session, upload)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded object size does not match declared size",
            )

        upload.status = UploadStatus.uploaded
        upload.completed_at = datetime.now(timezone.utc)
        await session.commit()
        return {"ok": True, "upload_id": str(upload.id)}
    finally:
        if spool is not None:
            spool.close()
        upload_slots.release()

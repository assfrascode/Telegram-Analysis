import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.config import get_settings
from app.dependencies import get_current_user
from app.models import TelegramChat, User
from app.schemas import (
    TelegramChatResponse,
    TelegramIngestChatUpsertRequest,
    TelegramIngestChatUpsertResponse,
    TelegramIngestChatTokenAssignRequest,
    TelegramIngestClaimResponse,
    TelegramIngestMessagesRequest,
    TelegramIngestRunCompleteRequest,
    TelegramIngestTokenCreateRequest,
    TelegramIngestTokenCreateResponse,
)
from app.services.telegram_ingest import (
    IngestPrincipal,
    authenticate_ingest_token,
    claim_next_external_chat,
    complete_external_run,
    create_ingest_token,
    heartbeat_external_run,
    reassign_external_chat_token,
    revoke_ingest_token,
    upsert_external_chat,
    upsert_external_media,
    upsert_external_messages,
)
from app.services.minio_store import remove_object

router = APIRouter(prefix="/telegram/ingest", tags=["telegram-ingest"])
settings = get_settings()


def chat_response(chat: TelegramChat) -> TelegramChatResponse:
    return TelegramChatResponse(
        id=chat.id,
        telegram_chat_id=chat.telegram_chat_id,
        ingest_mode=chat.ingest_mode.value,
        title=chat.title,
        username=chat.username,
        chat_type=chat.chat_type,
        initial_sync_from=chat.initial_sync_from,
        sync_interval_minutes=chat.sync_interval_minutes,
        status=chat.status.value,
        last_error=chat.last_error,
        last_sync_at=chat.last_sync_at,
        last_collected_message_id=chat.last_collected_message_id,
        next_sync_at=chat.next_sync_at,
        coverage_start=chat.coverage_start,
        coverage_end=chat.coverage_end,
    )


async def get_current_ingest_principal(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> IngestPrincipal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing ingest token")
    principal = await authenticate_ingest_token(session, authorization.split(" ", 1)[1])
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid ingest token")
    return principal


@router.post("/tokens", response_model=TelegramIngestTokenCreateResponse)
async def create_token(
    payload: TelegramIngestTokenCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramIngestTokenCreateResponse:
    token, raw_token = await create_ingest_token(
        session,
        owner_user_id=user.id,
        name=payload.name,
        expires_in_days=payload.expires_in_days,
    )
    await session.commit()
    return TelegramIngestTokenCreateResponse(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
        last_used_at=token.last_used_at,
        token=raw_token,
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await revoke_ingest_token(session, owner_user_id=user.id, token_id=token_id)
    await session.commit()


@router.post("/chats", response_model=TelegramIngestChatUpsertResponse)
async def upsert_chat(
    payload: TelegramIngestChatUpsertRequest,
    principal: IngestPrincipal = Depends(get_current_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> TelegramIngestChatUpsertResponse:
    chat = await upsert_external_chat(session, principal=principal, payload=payload)
    await session.commit()
    return TelegramIngestChatUpsertResponse(chat=chat_response(chat))


@router.put("/chats/{chat_id}/token", response_model=TelegramIngestChatUpsertResponse)
async def assign_chat_token(
    chat_id: uuid.UUID,
    payload: TelegramIngestChatTokenAssignRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramIngestChatUpsertResponse:
    chat = await reassign_external_chat_token(
        session,
        owner_user_id=user.id,
        chat_id=chat_id,
        token_id=payload.token_id,
    )
    await session.commit()
    return TelegramIngestChatUpsertResponse(chat=chat_response(chat))


@router.post("/claims/next", response_model=TelegramIngestClaimResponse)
async def claim_next(
    principal: IngestPrincipal = Depends(get_current_ingest_principal),
    session: AsyncSession = Depends(get_session),
):
    claimed = await claim_next_external_chat(session, principal=principal)
    if claimed is None:
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    run, chat, after_message_id = claimed
    await session.commit()
    return TelegramIngestClaimResponse(
        run_id=run.id,
        chat=chat_response(chat),
        requested_start=run.requested_start,
        requested_end=run.requested_end,
        after_message_id=after_message_id,
    )


@router.post("/runs/{run_id}/heartbeat")
async def heartbeat_run(
    run_id: uuid.UUID,
    principal: IngestPrincipal = Depends(get_current_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await heartbeat_external_run(session, principal=principal, run_id=run_id)
    await session.commit()
    return {"ok": True}


@router.post("/runs/{run_id}/messages")
async def post_messages(
    run_id: uuid.UUID,
    payload: TelegramIngestMessagesRequest,
    principal: IngestPrincipal = Depends(get_current_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    count = await upsert_external_messages(
        session,
        principal=principal,
        run_id=run_id,
        messages=payload.messages,
    )
    await session.commit()
    return {"ok": True, "messages": count}


@router.post("/runs/{run_id}/media")
async def post_media(
    run_id: uuid.UUID,
    telegram_message_id: int = Form(..., ge=1),
    telegram_media_key: str = Form(..., min_length=1, max_length=512),
    media_type: str = Form(..., min_length=1, max_length=64),
    filename: str = Form(..., min_length=1, max_length=900),
    mime_type: str | None = Form(default=None, max_length=255),
    size_bytes: int | None = Form(default=None, ge=0, le=settings.max_ingest_media_bytes),
    sha256: str | None = Form(default=None, pattern=r"^[0-9a-fA-F]{64}$"),
    error_message: str | None = Form(default=None, max_length=4000),
    file: UploadFile | None = File(default=None),
    principal: IngestPrincipal = Depends(get_current_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    result = await upsert_external_media(
        session,
        principal=principal,
        run_id=run_id,
        telegram_message_id=telegram_message_id,
        telegram_media_key=telegram_media_key,
        media_type=media_type,
        filename=filename,
        mime_type=mime_type,
        declared_size_bytes=size_bytes,
        declared_sha256=sha256,
        file=file,
        error_message=error_message,
    )
    try:
        await session.commit()
    except Exception:
        if result.new_object_key:
            try:
                await asyncio.to_thread(remove_object, result.new_object_key)
            except Exception:
                pass
        raise
    if result.superseded_object_key:
        try:
            await asyncio.to_thread(remove_object, result.superseded_object_key)
        except Exception:
            pass
    media = result.media
    return {
        "ok": True,
        "media_id": str(media.id),
        "status": media.status.value,
        "minio_object_key": media.minio_object_key,
    }


@router.post("/runs/{run_id}/complete")
async def complete_run(
    run_id: uuid.UUID,
    payload: TelegramIngestRunCompleteRequest,
    principal: IngestPrincipal = Depends(get_current_ingest_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    run = await complete_external_run(session, principal=principal, run_id=run_id, payload=payload)
    await session.commit()
    return {"ok": True, "status": run.status.value}

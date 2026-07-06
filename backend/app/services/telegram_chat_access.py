from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    TelegramChat,
    TelegramConnection,
    TelegramConnectionStatus,
    TelegramIngestMode,
)


BACKEND_CONNECTION_UNAVAILABLE_DETAIL = (
    "Telegram connection is not available for this backend-connected chat. "
    "Use an external collector chat or connect Telegram first."
)


async def ensure_chat_sync_source_available(session: AsyncSession, chat: TelegramChat) -> None:
    if chat.ingest_mode == TelegramIngestMode.external_push:
        return

    connection = await session.get(TelegramConnection, chat.connection_id) if chat.connection_id else None
    if connection is None or connection.status != TelegramConnectionStatus.connected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=BACKEND_CONNECTION_UNAVAILABLE_DETAIL,
        )

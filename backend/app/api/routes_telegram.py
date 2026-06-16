import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_current_user
from app.models import (
    TelegramChat,
    TelegramChatStatus,
    TelegramConnectionStatus,
    User,
)
from app.schemas import (
    TelegramChatCreateRequest,
    TelegramChatResponse,
    TelegramChatUpdateRequest,
    TelegramConnectionResponse,
    TelegramDialogResponse,
    TelegramLoginCodeRequest,
    TelegramLoginPasswordRequest,
    TelegramLoginStartRequest,
    TelegramReportScheduleCreateRequest,
    TelegramReportScheduleResponse,
    TelegramReportScheduleUpdateRequest,
)
from app.services.report_schedules import (
    create_report_schedule,
    delete_report_schedule,
    get_owned_report_schedule,
    list_report_schedules,
    response as report_schedule_response,
    update_report_schedule,
)
from app.services.telegram_accounts import (
    disconnect_account,
    get_connection,
    get_group_dialog,
    list_group_dialogs,
    start_login,
    verify_login_code,
    verify_login_password,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def connection_response(connection) -> TelegramConnectionResponse:
    if connection is None:
        return TelegramConnectionResponse(connected=False, status="disconnected")
    return TelegramConnectionResponse(
        connected=connection.status == TelegramConnectionStatus.connected,
        status=connection.status.value,
        telegram_user_id=connection.telegram_user_id,
        phone=connection.phone,
        display_name=connection.display_name,
        last_error=connection.last_error,
        last_verified_at=connection.last_verified_at,
    )


def chat_response(chat: TelegramChat) -> TelegramChatResponse:
    return TelegramChatResponse(
        id=chat.id,
        telegram_chat_id=chat.telegram_chat_id,
        title=chat.title,
        username=chat.username,
        chat_type=chat.chat_type,
        initial_sync_from=chat.initial_sync_from,
        sync_interval_minutes=chat.sync_interval_minutes,
        status=chat.status.value,
        last_error=chat.last_error,
        last_sync_at=chat.last_sync_at,
        next_sync_at=chat.next_sync_at,
        coverage_start=chat.coverage_start,
        coverage_end=chat.coverage_end,
    )


async def owned_chat(session: AsyncSession, owner_user_id: uuid.UUID, chat_id: uuid.UUID) -> TelegramChat:
    chat = (
        await session.execute(
            select(TelegramChat).where(
                TelegramChat.id == chat_id, TelegramChat.owner_user_id == owner_user_id
            )
        )
    ).scalar_one_or_none()
    if chat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")
    return chat


@router.get("/connection", response_model=TelegramConnectionResponse)
async def connection_status(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramConnectionResponse:
    return connection_response(await get_connection(session, user.id))


@router.post("/connection/start")
async def connection_start(
    payload: TelegramLoginStartRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    challenge = await start_login(
        session,
        owner_user_id=user.id,
        api_id=payload.api_id,
        api_hash=payload.api_hash,
        phone=payload.phone,
    )
    return {"challenge_id": str(challenge.id), "expires_at": challenge.expires_at}


@router.post("/connection/code")
async def connection_code(
    payload: TelegramLoginCodeRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    connection, requires_password = await verify_login_code(
        session,
        owner_user_id=user.id,
        challenge_id=payload.challenge_id,
        code=payload.code,
    )
    return {
        "requires_password": requires_password,
        "connection": connection_response(connection).model_dump() if connection else None,
    }


@router.post("/connection/password", response_model=TelegramConnectionResponse)
async def connection_password(
    payload: TelegramLoginPasswordRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramConnectionResponse:
    connection = await verify_login_password(
        session,
        owner_user_id=user.id,
        challenge_id=payload.challenge_id,
        password=payload.password,
    )
    return connection_response(connection)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
async def connection_disconnect(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    connection = await get_connection(session, user.id)
    if connection is not None:
        await disconnect_account(session, connection)


@router.get("/dialogs", response_model=list[TelegramDialogResponse])
async def dialogs(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TelegramDialogResponse]:
    connection = await get_connection(session, user.id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect Telegram first")
    return [TelegramDialogResponse(**item) for item in await list_group_dialogs(connection)]


@router.get("/chats", response_model=list[TelegramChatResponse])
async def list_chats(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TelegramChatResponse]:
    rows = (
        await session.execute(
            select(TelegramChat)
            .where(TelegramChat.owner_user_id == user.id)
            .order_by(desc(TelegramChat.created_at))
        )
    ).scalars()
    return [chat_response(chat) for chat in rows]


@router.get("/report-schedules", response_model=list[TelegramReportScheduleResponse])
async def list_schedules(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[TelegramReportScheduleResponse]:
    return await list_report_schedules(session, owner_user_id=user.id)


@router.post("/report-schedules", response_model=TelegramReportScheduleResponse)
async def create_schedule(
    payload: TelegramReportScheduleCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramReportScheduleResponse:
    schedule = await create_report_schedule(session, owner_user_id=user.id, payload=payload)
    await session.commit()
    return report_schedule_response(schedule)


@router.patch("/report-schedules/{schedule_id}", response_model=TelegramReportScheduleResponse)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: TelegramReportScheduleUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramReportScheduleResponse:
    schedule = await get_owned_report_schedule(session, owner_user_id=user.id, schedule_id=schedule_id)
    schedule = await update_report_schedule(
        session,
        owner_user_id=user.id,
        schedule=schedule,
        payload=payload,
    )
    await session.commit()
    return report_schedule_response(schedule)


@router.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(
    schedule_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await delete_report_schedule(session, owner_user_id=user.id, schedule_id=schedule_id)
    await session.commit()


@router.post("/chats", response_model=TelegramChatResponse)
async def create_chat(
    payload: TelegramChatCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramChatResponse:
    connection = await get_connection(session, user.id)
    if connection is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Connect Telegram first")
    dialog = await get_group_dialog(connection, payload.telegram_chat_id)
    if dialog is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Telegram group or channel is no longer accessible",
        )
    existing = (
        await session.execute(
            select(TelegramChat).where(
                TelegramChat.owner_user_id == user.id,
                TelegramChat.telegram_chat_id == payload.telegram_chat_id,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.connection_id = connection.id
        existing.title = dialog["title"]
        existing.username = dialog["username"]
        existing.chat_type = dialog["chat_type"]
        existing.access_hash = dialog["access_hash"]
        existing.initial_sync_from = payload.initial_sync_from
        existing.sync_interval_minutes = payload.sync_interval_minutes
        existing.status = TelegramChatStatus.active
        existing.next_sync_at = utc_now()
        existing.updated_at = utc_now()
        chat = existing
    else:
        chat = TelegramChat(
            owner_user_id=user.id,
            connection_id=connection.id,
            telegram_chat_id=dialog["telegram_chat_id"],
            access_hash=dialog["access_hash"],
            title=dialog["title"],
            username=dialog["username"],
            chat_type=dialog["chat_type"],
            initial_sync_from=payload.initial_sync_from,
            sync_interval_minutes=payload.sync_interval_minutes,
            next_sync_at=utc_now(),
        )
        session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return chat_response(chat)


@router.patch("/chats/{chat_id}", response_model=TelegramChatResponse)
async def update_chat(
    chat_id: uuid.UUID,
    payload: TelegramChatUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TelegramChatResponse:
    chat = await owned_chat(session, user.id, chat_id)
    if payload.sync_interval_minutes is not None:
        chat.sync_interval_minutes = payload.sync_interval_minutes
        chat.next_sync_at = utc_now()
    if payload.archived is not None:
        chat.status = TelegramChatStatus.archived if payload.archived else TelegramChatStatus.active
        if not payload.archived:
            chat.next_sync_at = utc_now()
    chat.updated_at = utc_now()
    await session.commit()
    return chat_response(chat)


@router.post("/chats/{chat_id}/sync")
async def request_chat_sync(
    chat_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    chat = await owned_chat(session, user.id, chat_id)
    if chat.status == TelegramChatStatus.archived:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat is archived")
    chat.next_sync_at = utc_now()
    await session.commit()
    return {"ok": True, "next_sync_at": chat.next_sync_at}

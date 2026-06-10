import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from telethon import TelegramClient
from telethon.errors import (
    ApiIdInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Chat
from telethon.utils import get_display_name

from app.config import get_settings
from app.models import (
    TelegramChat,
    TelegramChatStatus,
    TelegramConnection,
    TelegramConnectionStatus,
    TelegramLoginChallenge,
)
from app.services.telegram_crypto import decrypt_telegram_secret, encrypt_telegram_secret

settings = get_settings()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def _client(api_id: int, api_hash: str, session_value: str = "") -> TelegramClient:
    client = TelegramClient(StringSession(session_value), api_id, api_hash)
    await client.connect()
    return client


async def start_login(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    api_id: int,
    api_hash: str,
    phone: str,
) -> TelegramLoginChallenge:
    await session.execute(
        delete(TelegramLoginChallenge).where(TelegramLoginChallenge.owner_user_id == owner_user_id)
    )
    client = await _client(api_id, api_hash)
    try:
        sent = await client.send_code_request(phone)
        challenge = TelegramLoginChallenge(
            owner_user_id=owner_user_id,
            api_id=api_id,
            api_hash_encrypted=encrypt_telegram_secret(api_hash),
            phone=phone,
            phone_code_hash_encrypted=encrypt_telegram_secret(sent.phone_code_hash),
            session_encrypted=encrypt_telegram_secret(client.session.save()),
            expires_at=utc_now() + timedelta(minutes=settings.telegram_login_challenge_minutes),
        )
        session.add(challenge)
        await session.commit()
        return challenge
    except ApiIdInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram rejected the API ID or API hash",
        ) from exc
    finally:
        await client.disconnect()


async def _owned_challenge(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    challenge_id: uuid.UUID,
) -> TelegramLoginChallenge:
    challenge = (
        await session.execute(
            select(TelegramLoginChallenge).where(
                TelegramLoginChallenge.id == challenge_id,
                TelegramLoginChallenge.owner_user_id == owner_user_id,
            )
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Login challenge not found")
    if challenge.expires_at <= utc_now():
        await session.delete(challenge)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Login challenge expired")
    return challenge


async def _save_connection(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    challenge: TelegramLoginChallenge,
    client: TelegramClient,
) -> TelegramConnection:
    me = await client.get_me()
    if me is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram login did not return an account",
        )

    connection = (
        await session.execute(
            select(TelegramConnection).where(TelegramConnection.owner_user_id == owner_user_id)
        )
    ).scalar_one_or_none()
    values = {
        "api_id": challenge.api_id,
        "api_hash_encrypted": challenge.api_hash_encrypted,
        "session_encrypted": encrypt_telegram_secret(client.session.save()),
        "telegram_user_id": int(me.id),
        "phone": getattr(me, "phone", None) or challenge.phone,
        "display_name": get_display_name(me) or None,
        "status": TelegramConnectionStatus.connected,
        "last_error": None,
        "updated_at": utc_now(),
        "last_verified_at": utc_now(),
    }
    if connection is None:
        connection = TelegramConnection(owner_user_id=owner_user_id, **values)
        session.add(connection)
    else:
        for key, value in values.items():
            setattr(connection, key, value)

    await session.delete(challenge)
    await session.commit()
    await session.refresh(connection)
    return connection


async def verify_login_code(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    challenge_id: uuid.UUID,
    code: str,
) -> tuple[TelegramConnection | None, bool]:
    challenge = await _owned_challenge(
        session, owner_user_id=owner_user_id, challenge_id=challenge_id
    )
    client = await _client(
        challenge.api_id,
        decrypt_telegram_secret(challenge.api_hash_encrypted),
        decrypt_telegram_secret(challenge.session_encrypted),
    )
    try:
        try:
            await client.sign_in(
                phone=challenge.phone,
                code=code.strip(),
                phone_code_hash=decrypt_telegram_secret(challenge.phone_code_hash_encrypted),
            )
        except SessionPasswordNeededError:
            challenge.requires_password = True
            challenge.session_encrypted = encrypt_telegram_secret(client.session.save())
            await session.commit()
            return None, True
        except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Telegram login code is invalid or expired",
            ) from exc
        return await _save_connection(
            session, owner_user_id=owner_user_id, challenge=challenge, client=client
        ), False
    finally:
        await client.disconnect()


async def verify_login_password(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    challenge_id: uuid.UUID,
    password: str,
) -> TelegramConnection:
    challenge = await _owned_challenge(
        session, owner_user_id=owner_user_id, challenge_id=challenge_id
    )
    if not challenge.requires_password:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This login challenge does not require a two-step verification password",
        )
    client = await _client(
        challenge.api_id,
        decrypt_telegram_secret(challenge.api_hash_encrypted),
        decrypt_telegram_secret(challenge.session_encrypted),
    )
    try:
        try:
            await client.sign_in(password=password)
        except PasswordHashInvalidError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Telegram two-step verification password is invalid",
            ) from exc
        return await _save_connection(
            session, owner_user_id=owner_user_id, challenge=challenge, client=client
        )
    finally:
        await client.disconnect()


async def get_connection(
    session: AsyncSession, owner_user_id: uuid.UUID
) -> TelegramConnection | None:
    return (
        await session.execute(
            select(TelegramConnection).where(TelegramConnection.owner_user_id == owner_user_id)
        )
    ).scalar_one_or_none()


async def connected_client(connection: TelegramConnection) -> TelegramClient:
    client = await _client(
        connection.api_id,
        decrypt_telegram_secret(connection.api_hash_encrypted),
        decrypt_telegram_secret(connection.session_encrypted),
    )
    if not await client.is_user_authorized():
        await client.disconnect()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram session is no longer authorized; reconnect the account",
        )
    return client


async def list_group_dialogs(connection: TelegramConnection) -> list[dict]:
    client = await connected_client(connection)
    try:
        dialogs = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            chat_type = None
            if isinstance(entity, Channel):
                if entity.broadcast:
                    chat_type = "channel"
                elif entity.megagroup:
                    chat_type = "megagroup"
            elif isinstance(entity, Chat):
                chat_type = "group"
            if chat_type is None:
                continue
            dialogs.append(
                {
                    "telegram_chat_id": int(entity.id),
                    "access_hash": (
                        str(entity.access_hash)
                        if getattr(entity, "access_hash", None) is not None
                        else None
                    ),
                    "title": dialog.name or get_display_name(entity) or str(entity.id),
                    "username": getattr(entity, "username", None),
                    "chat_type": chat_type,
                }
            )
        return sorted(dialogs, key=lambda item: item["title"].casefold())
    finally:
        await client.disconnect()


async def get_group_dialog(
    connection: TelegramConnection,
    telegram_chat_id: int,
) -> dict | None:
    """Return canonical dialog metadata directly from Telegram.

    The browser is not a reliable transport for 64-bit access hashes because
    JavaScript Numbers lose integer precision above 2**53. Resolve the selected
    dialog again with the authenticated Telethon client before persisting it.
    """
    client = await connected_client(connection)
    try:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if int(entity.id) != int(telegram_chat_id):
                continue

            if isinstance(entity, Channel):
                if entity.broadcast:
                    chat_type = "channel"
                elif entity.megagroup:
                    chat_type = "megagroup"
                else:
                    continue
            elif isinstance(entity, Chat):
                chat_type = "group"
            else:
                continue

            return {
                "telegram_chat_id": int(entity.id),
                "access_hash": getattr(entity, "access_hash", None),
                "title": dialog.name or get_display_name(entity) or str(entity.id),
                "username": getattr(entity, "username", None),
                "chat_type": chat_type,
            }
        return None
    finally:
        await client.disconnect()


async def disconnect_account(session: AsyncSession, connection: TelegramConnection) -> None:
    try:
        client = await connected_client(connection)
    except HTTPException:
        client = None
    if client is not None:
        try:
            await client.log_out()
        finally:
            await client.disconnect()
    rows = (
        await session.execute(
            select(TelegramChat).where(TelegramChat.connection_id == connection.id)
        )
    ).scalars()
    for chat in rows:
        chat.status = TelegramChatStatus.archived
        chat.updated_at = utc_now()
    connection.status = TelegramConnectionStatus.disconnected
    connection.session_encrypted = encrypt_telegram_secret("revoked")
    connection.last_error = None
    connection.updated_at = utc_now()
    await session.commit()

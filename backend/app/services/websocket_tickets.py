import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import WebSocketTicket


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_websocket_ticket(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    job_id: uuid.UUID,
) -> tuple[str, datetime]:
    raw_token = "wst_" + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(
        seconds=get_settings().websocket_ticket_expire_seconds
    )
    await session.execute(
        delete(WebSocketTicket).where(
            or_(
                WebSocketTicket.expires_at <= now,
                WebSocketTicket.used_at.is_not(None),
                and_(
                    WebSocketTicket.owner_user_id == owner_user_id,
                    WebSocketTicket.job_id == job_id,
                ),
            )
        )
    )
    session.add(
        WebSocketTicket(
            token_hash=_token_hash(raw_token),
            owner_user_id=owner_user_id,
            job_id=job_id,
            expires_at=expires_at,
        )
    )
    await session.commit()
    return raw_token, expires_at


async def consume_websocket_ticket(
    session: AsyncSession,
    *,
    raw_token: str,
    job_id: uuid.UUID,
) -> uuid.UUID | None:
    """Atomically consume a valid job-scoped ticket and return its owner."""

    if not raw_token or len(raw_token) > 256:
        return None
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(WebSocketTicket)
        .where(
            WebSocketTicket.token_hash == _token_hash(raw_token),
            WebSocketTicket.job_id == job_id,
            WebSocketTicket.expires_at > now,
            WebSocketTicket.used_at.is_(None),
        )
        .values(used_at=now)
        .returning(WebSocketTicket.owner_user_id)
    )
    owner_user_id = result.scalar_one_or_none()
    if owner_user_id is None:
        await session.rollback()
        return None
    await session.commit()
    return owner_user_id

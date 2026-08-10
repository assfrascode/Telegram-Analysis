import asyncio
import os
import socket
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import (
    TelegramChat,
    TelegramChatStatus,
    TelegramConnection,
    TelegramConnectionStatus,
    TelegramIngestMode,
    TelegramSyncRun,
    TelegramSyncStatus,
)
from app.services.telegram_ingest import ensure_utc, report_job_needing_coverage
from app.services.telegram_sync import missing_sync_range, periodic_sync_start, synchronize_chat

settings = get_settings()
COLLECTOR_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def claim_due_chat() -> uuid.UUID | None:
    now = utc_now()
    async with SessionLocal() as session:
        chat = (
            await session.execute(
                select(TelegramChat)
                .join(TelegramConnection, TelegramChat.connection_id == TelegramConnection.id)
                .where(
                    TelegramChat.ingest_mode == TelegramIngestMode.backend_pull,
                    TelegramConnection.status == TelegramConnectionStatus.connected,
                    TelegramChat.status.in_(
                        [
                            TelegramChatStatus.active,
                            TelegramChatStatus.error,
                            TelegramChatStatus.syncing,
                        ]
                    ),
                    TelegramChat.next_sync_at <= now,
                    or_(
                        TelegramChat.lease_expires_at.is_(None),
                        TelegramChat.lease_expires_at < now,
                    ),
                )
                .order_by(TelegramChat.next_sync_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalar_one_or_none()
        if chat is None:
            return None
        chat.lease_owner = COLLECTOR_ID
        chat.lease_expires_at = now + timedelta(minutes=settings.telegram_sync_lease_minutes)
        await session.commit()
        return chat.id


async def sync_request_for_chat(
    session,
    chat: TelegramChat,
    now: datetime,
) -> tuple[datetime, datetime, uuid.UUID | None]:
    report_job = await report_job_needing_coverage(session, chat)
    if report_job is not None:
        missing_range = missing_sync_range(
            chat,
            ensure_utc(report_job.report_start_at),
            ensure_utc(report_job.report_end_at),
        )
        if missing_range is not None:
            return missing_range[0], missing_range[1], report_job.id
    return periodic_sync_start(chat), now, None


async def release_orphaned_collector_leases() -> int:
    """Recover leases left behind when the singleton collector restarts.

    Report-owned leases use the ``report:`` prefix and must remain intact.
    Docker Compose runs one collector instance, so any other lease owner at
    startup belongs to a collector process that no longer exists.
    """
    now = utc_now()
    resume_after = now + timedelta(seconds=settings.telegram_sync_poll_seconds)
    async with SessionLocal() as session:
        chats = list(
            (
                await session.execute(
                    select(TelegramChat).where(
                        TelegramChat.lease_owner.is_not(None),
                        ~TelegramChat.lease_owner.startswith("report:"),
                        ~TelegramChat.lease_owner.startswith("external:"),
                    )
                )
            )
            .scalars()
            .all()
        )
        for chat in chats:
            running_runs = (
                (
                    await session.execute(
                        select(TelegramSyncRun).where(
                            TelegramSyncRun.chat_id == chat.id,
                            TelegramSyncRun.status == TelegramSyncStatus.running,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for run in running_runs:
                run.status = TelegramSyncStatus.failed
                run.error_message = "Telegram collector restarted during synchronization"
                run.completed_at = now
            chat.lease_owner = None
            chat.lease_expires_at = None
            if chat.status == TelegramChatStatus.syncing:
                chat.status = TelegramChatStatus.error
                chat.last_error = "Telegram collector restarted during synchronization"
            # Give a report worker already waiting on this chat one polling
            # interval to acquire its report-specific lease first.
            if chat.next_sync_at < resume_after:
                chat.next_sync_at = resume_after
        if chats:
            await session.commit()
        return len(chats)


async def collect(chat_id: uuid.UUID) -> None:
    async with SessionLocal() as session:
        chat = await session.get(TelegramChat, chat_id)
        if chat is None or chat.lease_owner != COLLECTOR_ID:
            return
        requested_end = utc_now()
        requested_start, requested_end, job_id = await sync_request_for_chat(
            session,
            chat,
            requested_end,
        )
        print(
            f"Telegram collector sync started chat_id={chat.id} title={chat.title!r} "
            f"range={requested_start.isoformat()}..{requested_end.isoformat()}",
            flush=True,
        )
        run = await synchronize_chat(
            session,
            chat=chat,
            requested_start=requested_start,
            requested_end=requested_end,
            job_id=job_id,
        )
        print(
            f"Telegram collector sync completed chat_id={chat.id} "
            f"messages={run.messages_seen} attachments={run.attachments_seen} "
            f"attachment_failures={run.attachments_failed}",
            flush=True,
        )


async def record_collection_failure(chat_id: uuid.UUID, exc: Exception) -> None:
    now = utc_now()
    error_message = str(exc) or exc.__class__.__name__
    async with SessionLocal() as session:
        chat = await session.get(TelegramChat, chat_id)
        if chat is None or chat.lease_owner != COLLECTOR_ID:
            return

        running_runs = (
            (
                await session.execute(
                    select(TelegramSyncRun).where(
                        TelegramSyncRun.chat_id == chat_id,
                        TelegramSyncRun.status == TelegramSyncStatus.running,
                    )
                )
            )
            .scalars()
            .all()
        )
        for run in running_runs:
            run.status = TelegramSyncStatus.failed
            run.error_message = error_message[:4000]
            run.completed_at = now

        chat.status = TelegramChatStatus.error
        chat.last_error = error_message[:4000]
        chat.next_sync_at = now + timedelta(
            minutes=settings.telegram_sync_retry_minutes
        )
        chat.lease_owner = None
        chat.lease_expires_at = None
        chat.updated_at = now
        await session.commit()


async def collection_heartbeat(chat_id: uuid.UUID, stopped: asyncio.Event) -> None:
    interval_seconds = max(
        10,
        min(60, settings.telegram_sync_lease_minutes * 60 // 3),
    )
    while True:
        try:
            await asyncio.wait_for(stopped.wait(), timeout=interval_seconds)
            return
        except TimeoutError:
            pass

        now = utc_now()
        async with SessionLocal() as session:
            result = await session.execute(
                update(TelegramChat)
                .where(
                    TelegramChat.id == chat_id,
                    TelegramChat.lease_owner == COLLECTOR_ID,
                )
                .values(
                    lease_expires_at=now
                    + timedelta(minutes=settings.telegram_sync_lease_minutes),
                    updated_at=now,
                )
            )
            await session.commit()
            if result.rowcount != 1:
                return
        print(
            f"Telegram collector sync still running chat_id={chat_id}",
            flush=True,
        )


async def collect_safely(chat_id: uuid.UUID) -> None:
    stopped = asyncio.Event()
    heartbeat = asyncio.create_task(collection_heartbeat(chat_id, stopped))
    try:
        try:
            await collect(chat_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(
                f"Telegram collection failed chat_id={chat_id}: "
                f"{exc.__class__.__name__}: {exc}",
                flush=True,
            )
            traceback.print_exc()
            try:
                await record_collection_failure(chat_id, exc)
            except Exception as recovery_exc:
                print(
                    f"Telegram collection failure could not be persisted chat_id={chat_id}: "
                    f"{recovery_exc.__class__.__name__}: {recovery_exc}",
                    flush=True,
                )
                traceback.print_exc()
    finally:
        stopped.set()
        await heartbeat


async def main() -> None:
    if settings.app_role not in {"telegram_collector", "all"}:
        raise RuntimeError("Telegram collector requires APP_ROLE=telegram_collector (or all)")
    await init_db()
    released = await release_orphaned_collector_leases()
    print(
        f"Telegram collector started id={COLLECTOR_ID} "
        f"poll_interval={settings.telegram_sync_poll_seconds}s "
        f"concurrency={settings.telegram_sync_concurrency} "
        f"released_orphaned_leases={released}",
        flush=True,
    )
    active: set[asyncio.Task] = set()
    while True:
        claimed_any = False
        while len(active) < max(1, settings.telegram_sync_concurrency):
            chat_id = await claim_due_chat()
            if chat_id is None:
                break
            claimed_any = True
            active.add(asyncio.create_task(collect_safely(chat_id)))

        if not active:
            await asyncio.sleep(settings.telegram_sync_poll_seconds)
            continue

        done, active = await asyncio.wait(
            active,
            timeout=0 if claimed_any else settings.telegram_sync_poll_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            task.result()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    CollectedMediaAnalysis,
    CollectedMediaTranscript,
    CollectedTelegramMedia,
    CollectedTelegramMessage,
    Job,
    JobStatus,
    MediaAnalysis,
    MediaTranscript,
    StepStatus,
    TelegramChat,
    TelegramIngestMode,
    TelegramMedia,
    TelegramMessage,
    TelegramSyncRun,
    TelegramSyncStatus,
)
from app.services.telegram_ingest import job_allows_partial_telegram_sync
from app.services.telegram_sync import (
    TelegramSyncError,
    chat_covers_interval,
    forward_sync_cursor,
    missing_sync_range,
    synchronize_chat,
)
from app.workers import subjects
from app.workers.base import Worker
from app.workers.pipeline import next_tasks_after_messages

settings = get_settings()


class TelegramSnapshotWorker(Worker):
    subject = subjects.TELEGRAM_SNAPSHOT
    durable = "telegram-snapshot-worker"
    queue = "telegram-snapshot"

    async def handle(self, session: AsyncSession, payload: dict) -> None:
        job = await session.get(Job, uuid.UUID(payload["job_id"]))
        if job is None or job.telegram_chat_id is None:
            raise TelegramSyncError("Telegram report job is missing its chat")
        chat = await session.get(TelegramChat, job.telegram_chat_id)
        if chat is None or job.report_start_at is None or job.report_end_at is None:
            raise TelegramSyncError("Telegram report interval is incomplete")
        print(
            f"Telegram report sync started job_id={job.id} chat_id={chat.id} "
            f"range={job.report_start_at.isoformat()}..{job.report_end_at.isoformat()}",
            flush=True,
        )

        job.status = JobStatus.running
        job.started_at = job.started_at or datetime.now(timezone.utc)
        await self.emit_event(
            session,
            job=job,
            event_type="telegram.sync.started",
            message="Telegram-Nachrichten werden bis zum Berichtszeitpunkt synchronisiert",
            payload={
                "chat_id": str(chat.id),
                "start_at": job.report_start_at.isoformat(),
                "end_at": job.report_end_at.isoformat(),
            },
        )
        await session.commit()

        allow_partial_sync = job_allows_partial_telegram_sync(job)
        try:
            if allow_partial_sync:
                await self._prepare_partial_report_sync(session, job, chat)
                run = None
            elif chat.ingest_mode == TelegramIngestMode.external_push:
                run = await self._wait_for_external_coverage(session, job, chat)
            else:
                run = await self._synchronize_backend_coverage(session, job, chat)
        except TelegramSyncError as exc:
            print(
                f"Telegram report sync failed job_id={job.id} chat_id={chat.id}: {exc}",
                flush=True,
            )
            job = await session.get(Job, job.id)
            job.status = JobStatus.failed
            job.error_message = f"Telegram synchronization failed: {exc}"
            job.completed_at = datetime.now(timezone.utc)
            await self.emit_event(
                session,
                job=job,
                event_type="telegram.sync.failed",
                message=job.error_message,
                level="error",
            )
            return

        messages_seen = run.messages_seen if run is not None else 0
        attachments_seen = run.attachments_seen if run is not None else 0
        attachments_failed = run.attachments_failed if run is not None else 0
        print(
            f"Telegram report sync completed job_id={job.id} chat_id={chat.id} "
            f"messages={messages_seen} attachments={attachments_seen} "
            f"attachment_failures={attachments_failed}",
            flush=True,
        )
        await self.emit_event(
            session,
            job=job,
            event_type="telegram.sync.completed",
            message="Telegram-Synchronisierung abgeschlossen",
            payload={
                "messages_seen": messages_seen,
                "attachments_seen": attachments_seen,
                "attachments_failed": attachments_failed,
                "ingest_mode": chat.ingest_mode.value,
                "allow_partial_telegram_sync": allow_partial_sync,
            },
        )
        await session.commit()

        await session.execute(delete(TelegramMedia).where(TelegramMedia.job_id == job.id))
        await session.execute(delete(TelegramMessage).where(TelegramMessage.job_id == job.id))
        await session.flush()

        source_messages = list(
            (
                await session.execute(
                    select(CollectedTelegramMessage)
                    .where(
                        CollectedTelegramMessage.chat_id == chat.id,
                        CollectedTelegramMessage.timestamp >= job.report_start_at,
                        CollectedTelegramMessage.timestamp < job.report_end_at,
                    )
                    .order_by(
                        CollectedTelegramMessage.timestamp,
                        CollectedTelegramMessage.telegram_message_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not source_messages:
            job.status = JobStatus.failed
            job.error_message = "No collected Telegram messages exist in the requested interval"
            job.completed_at = datetime.now(timezone.utc)
            await self.emit_event(
                session,
                job=job,
                event_type="telegram.snapshot.failed",
                message=job.error_message,
                level="error",
            )
            return

        source_to_job: dict[uuid.UUID, TelegramMessage] = {}
        for source in source_messages:
            message = TelegramMessage(
                job_id=job.id,
                telegram_message_id=source.telegram_message_id,
                timestamp=source.timestamp,
                edited_timestamp=source.edited_timestamp,
                sender_id=source.sender_id,
                sender_name=source.sender_name,
                message_type=source.message_type,
                reply_to_message_id=source.reply_to_message_id,
                forwarded_from=source.forwarded_from,
                reactions=source.reactions,
                text=source.text,
                raw=source.raw,
            )
            session.add(message)
            await session.flush()
            source_to_job[source.id] = message

        source_media = list(
            (
                await session.execute(
                    select(CollectedTelegramMedia).where(
                        CollectedTelegramMedia.message_id.in_(source_to_job.keys())
                    )
                )
            )
            .scalars()
            .all()
        )
        for source in source_media:
            cached = (
                await session.execute(
                    select(CollectedMediaAnalysis).where(
                        CollectedMediaAnalysis.media_id == source.id,
                        CollectedMediaAnalysis.model_name == settings.vision_model,
                        CollectedMediaAnalysis.prompt_version
                        == settings.media_analysis_prompt_version,
                    )
                )
            ).scalar_one_or_none()
            cached_transcript = (
                await session.execute(
                    select(CollectedMediaTranscript).where(
                        CollectedMediaTranscript.media_id == source.id,
                        CollectedMediaTranscript.provider == "openai",
                        CollectedMediaTranscript.model_name == settings.openai_transcription_model,
                        CollectedMediaTranscript.response_format == "text",
                    )
                )
            ).scalar_one_or_none()
            analyzable = source.media_type in {"image", "video"}
            media_status = source.status
            if analyzable and source.minio_object_key and cached is None:
                media_status = StepStatus.pending
            elif analyzable and source.minio_object_key and cached is not None:
                media_status = StepStatus.completed
            elif source.status != StepStatus.completed:
                media_status = StepStatus.failed_permanent

            media = TelegramMedia(
                job_id=job.id,
                message_id=source_to_job[source.message_id].id,
                source_media_id=source.id,
                media_type=source.media_type,
                original_path=f"telegram/{source.id}/{source.filename}",
                minio_object_key=source.minio_object_key,
                size_bytes=source.size_bytes,
                sha256=source.sha256,
                status=media_status,
                missing_reason=source.error_message,
            )
            session.add(media)
            await session.flush()
            if cached is not None:
                session.add(
                    MediaAnalysis(
                        media_id=media.id,
                        model_name=cached.model_name,
                        prompt_version=cached.prompt_version,
                        description=cached.description,
                        raw_response=cached.raw_response,
                    )
                )
                media.analyzed_at = cached.created_at
            if cached_transcript is not None:
                session.add(
                    MediaTranscript(
                        job_id=job.id,
                        media_id=media.id,
                        provider=cached_transcript.provider,
                        model_name=cached_transcript.model_name,
                        response_format=cached_transcript.response_format,
                        status=cached_transcript.status,
                        attempts=cached_transcript.attempts,
                        transcript_text=cached_transcript.transcript_text,
                        error_message=cached_transcript.error_message,
                        raw_response=cached_transcript.raw_response,
                        created_at=cached_transcript.created_at,
                        updated_at=cached_transcript.updated_at,
                    )
                )

        await self.emit_event(
            session,
            job=job,
            event_type="telegram.snapshot.completed",
            message="Unveränderlicher Berichtszeitraum wurde vorbereitet",
            payload={
                "messages_total": len(source_messages),
                "media_total": len(source_media),
                "start_at": job.report_start_at.isoformat(),
                "end_at": job.report_end_at.isoformat(),
            },
        )
        await session.commit()
        for next_subject, next_key in next_tasks_after_messages(job):
            await self.enqueue(
                next_subject,
                {
                    "job_id": str(job.id),
                    "owner_user_id": str(job.owner_user_id),
                    "task_key": f"{next_key}:{job.id}",
                },
            )

    async def _prepare_partial_report_sync(
        self,
        session: AsyncSession,
        job: Job,
        chat: TelegramChat,
    ) -> None:
        now = datetime.now(timezone.utc)
        chat.next_sync_at = now
        chat.updated_at = now
        await self.emit_event(
            session,
            job=job,
            event_type="telegram.sync.partial",
            message="Bericht nutzt vorhandene Telegram-Nachrichten; fehlende Synchronisierung läuft nach",
            level="warning",
            payload={
                "chat_id": str(chat.id),
                "ingest_mode": chat.ingest_mode.value,
                "start_at": job.report_start_at.isoformat() if job.report_start_at else None,
                "end_at": job.report_end_at.isoformat() if job.report_end_at else None,
                "coverage_start": chat.coverage_start.isoformat()
                if chat.coverage_start
                else None,
                "coverage_end": chat.coverage_end.isoformat()
                if chat.coverage_end
                else None,
            },
        )
        await session.commit()

    def _chat_covers_report(self, chat: TelegramChat, job: Job) -> bool:
        return bool(
            job.report_start_at
            and job.report_end_at
            and chat_covers_interval(chat, job.report_start_at, job.report_end_at)
        )

    async def _synchronize_backend_coverage(
        self,
        session: AsyncSession,
        job: Job,
        chat: TelegramChat,
    ) -> TelegramSyncRun | None:
        latest_run = None
        while True:
            missing_range = missing_sync_range(
                chat,
                job.report_start_at,
                job.report_end_at,
            )
            if missing_range is None:
                return latest_run

            await self._wait_for_chat_lease(session, job, chat)
            now = datetime.now(timezone.utc)
            chat.lease_owner = f"report:{job.id}"
            chat.lease_expires_at = now + timedelta(minutes=settings.telegram_sync_lease_minutes)
            await session.commit()
            requested_start, requested_end = missing_range
            stopped = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._report_lease_heartbeat(chat.id, f"report:{job.id}", stopped)
            )
            try:
                latest_run = await synchronize_chat(
                    session,
                    chat=chat,
                    requested_start=requested_start,
                    requested_end=requested_end,
                    job_id=job.id,
                    after_message_id=forward_sync_cursor(chat, requested_start),
                )
            finally:
                stopped.set()
                try:
                    await heartbeat
                except Exception as exc:
                    print(
                        f"Telegram report lease heartbeat failed job_id={job.id} "
                        f"chat_id={chat.id}: {exc}",
                        flush=True,
                    )
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="telegram.sync.cancelled",
                message="Telegram-Synchronisierung wurde abgebrochen",
            )

    async def _report_lease_heartbeat(
        self,
        chat_id: uuid.UUID,
        lease_owner: str,
        stopped: asyncio.Event,
    ) -> None:
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

            now = datetime.now(timezone.utc)
            async with SessionLocal() as heartbeat_session:
                result = await heartbeat_session.execute(
                    update(TelegramChat)
                    .where(
                        TelegramChat.id == chat_id,
                        TelegramChat.lease_owner == lease_owner,
                    )
                    .values(
                        lease_expires_at=now
                        + timedelta(minutes=settings.telegram_sync_lease_minutes),
                        updated_at=now,
                    )
                )
                await heartbeat_session.commit()
                if result.rowcount != 1:
                    return

    async def _latest_completed_external_run(
        self,
        session: AsyncSession,
        job: Job,
        chat: TelegramChat,
    ) -> TelegramSyncRun | None:
        return (
            await session.execute(
                select(TelegramSyncRun)
                .where(
                    TelegramSyncRun.chat_id == chat.id,
                    TelegramSyncRun.status == TelegramSyncStatus.completed,
                    TelegramSyncRun.requested_start <= job.report_start_at,
                    TelegramSyncRun.requested_end >= job.report_end_at,
                )
                .order_by(desc(TelegramSyncRun.completed_at))
                .limit(1)
            )
        ).scalar_one_or_none()

    async def _wait_for_external_coverage(
        self,
        session: AsyncSession,
        job: Job,
        chat: TelegramChat,
    ) -> TelegramSyncRun | None:
        if self._chat_covers_report(chat, job):
            return await self._latest_completed_external_run(session, job, chat)

        now = datetime.now(timezone.utc)
        chat.next_sync_at = now
        chat.updated_at = now
        await session.commit()

        last_activity_at = now
        waiting_event_emitted = False
        while True:
            await session.refresh(chat)
            if self._chat_covers_report(chat, job):
                return await self._latest_completed_external_run(session, job, chat)

            current_time = datetime.now(timezone.utc)
            chat_activity_at = chat.updated_at or last_activity_at
            if chat_activity_at.tzinfo is None:
                chat_activity_at = chat_activity_at.replace(tzinfo=timezone.utc)
            else:
                chat_activity_at = chat_activity_at.astimezone(timezone.utc)
            last_activity_at = max(last_activity_at, chat_activity_at)
            if (
                current_time - last_activity_at
                >= timedelta(seconds=settings.telegram_external_inactivity_timeout_seconds)
            ):
                detail = (
                    "External Telegram collector made no progress for "
                    f"{settings.telegram_external_inactivity_timeout_seconds} seconds"
                )
                if chat.last_error:
                    detail = f"{detail}: {chat.last_error}"
                raise TelegramSyncError(detail)

            if not waiting_event_emitted:
                await self.emit_event(
                    session,
                    job=job,
                    event_type="telegram.sync.waiting",
                    message="Bericht wartet auf externe Telegram-Synchronisierung",
                    level="warning",
                    payload={
                        "chat_id": str(chat.id),
                        "ingest_mode": chat.ingest_mode.value,
                        "coverage_start": chat.coverage_start.isoformat()
                        if chat.coverage_start
                        else None,
                        "coverage_end": chat.coverage_end.isoformat()
                        if chat.coverage_end
                        else None,
                        "inactivity_timeout_seconds": (
                            settings.telegram_external_inactivity_timeout_seconds
                        ),
                    },
                )
                await session.commit()
                waiting_event_emitted = True

            await asyncio.sleep(2)
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="telegram.sync.cancelled",
                message="Warten auf externe Telegram-Synchronisierung wurde abgebrochen",
            )

    async def _wait_for_chat_lease(
        self,
        session: AsyncSession,
        job: Job,
        chat: TelegramChat,
    ) -> None:
        waiting_event_emitted = False
        report_lease_owner = f"report:{job.id}"
        while True:
            await session.refresh(chat)
            now = datetime.now(timezone.utc)
            lease_is_active = bool(
                chat.lease_owner
                and chat.lease_owner != report_lease_owner
                and chat.lease_expires_at
                and chat.lease_expires_at > now
            )
            if not lease_is_active:
                return

            if not waiting_event_emitted:
                wait_seconds = max(1, int((chat.lease_expires_at - now).total_seconds()))
                await self.emit_event(
                    session,
                    job=job,
                    event_type="telegram.sync.waiting",
                    message="Bericht wartet auf die laufende Telegram-Synchronisierung",
                    level="warning",
                    payload={
                        "chat_id": str(chat.id),
                        "lease_owner": chat.lease_owner,
                        "lease_expires_at": chat.lease_expires_at.isoformat(),
                        "maximum_wait_seconds": wait_seconds,
                    },
                )
                await session.commit()
                print(
                    f"Telegram report sync waiting job_id={job.id} chat_id={chat.id} "
                    f"lease_owner={chat.lease_owner} lease_expires_at={chat.lease_expires_at.isoformat()}",
                    flush=True,
                )
                waiting_event_emitted = True

            await asyncio.sleep(2)
            await self.checkpoint_cancelled(
                session,
                job,
                event_type="telegram.sync.cancelled",
                message="Warten auf Telegram-Synchronisierung wurde abgebrochen",
            )

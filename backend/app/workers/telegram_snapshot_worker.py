import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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
    TelegramMedia,
    TelegramMessage,
)
from app.services.telegram_sync import TelegramSyncError, synchronize_chat
from app.workers import subjects
from app.workers.base import Worker
from app.workers.pipeline import next_subject_after_messages

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
        await self._wait_for_chat_lease(session, job, chat)
        now = datetime.now(timezone.utc)
        chat.lease_owner = f"report:{job.id}"
        chat.lease_expires_at = now + timedelta(minutes=settings.telegram_sync_lease_minutes)
        await session.commit()
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

        try:
            run = await synchronize_chat(
                session,
                chat=chat,
                requested_start=job.report_start_at,
                requested_end=job.report_end_at,
                job_id=job.id,
            )
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

        print(
            f"Telegram report sync completed job_id={job.id} chat_id={chat.id} "
            f"messages={run.messages_seen} attachments={run.attachments_seen} "
            f"attachment_failures={run.attachments_failed}",
            flush=True,
        )
        await self.emit_event(
            session,
            job=job,
            event_type="telegram.sync.completed",
            message="Telegram-Synchronisierung abgeschlossen",
            payload={
                "messages_seen": run.messages_seen,
                "attachments_seen": run.attachments_seen,
                "attachments_failed": run.attachments_failed,
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
        next_subject, next_key = next_subject_after_messages(job)
        await self.enqueue(
            next_subject,
            {
                "job_id": str(job.id),
                "owner_user_id": str(job.owner_user_id),
                "task_key": f"{next_key}:{job.id}",
            },
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

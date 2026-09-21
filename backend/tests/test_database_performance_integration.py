"""Behavior and SQL-count regressions; use a disposable PostgreSQL test database."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    CollectedMediaAnalysis, CollectedMediaTranscript, CollectedTelegramMedia,
    CollectedTelegramMessage, Job, JobSourceType, JobStatus, MediaAnalysis,
    MediaTranscript, MessageChunk, MessageTranslation, Question, QuestionRun,
    RetrievalHit, StepStatus, TelegramChat, TelegramIngestMode, TelegramIngestToken,
    TelegramMedia, TelegramMessage, TelegramSyncRun, TelegramSyncStatus, User, WorkerTask,
)
from app.schemas import TelegramIngestMessageInput
from app.services.capacity import _db_counts
from app.services.telegram_export import parse_message
from app.services.telegram_ingest import IngestPrincipal, upsert_external_messages
from app.workers.parser_worker import _persist_message_batch
from app.workers.report_worker import ReportWorker
from app.workers.telegram_snapshot_worker import TelegramSnapshotWorker, settings

DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Requires disposable PostgreSQL test database")
NOW = datetime(2026, 9, 21, 12, tzinfo=timezone.utc)


@asynccontextmanager
async def database():
    if not (make_url(DATABASE_URL).database or "").endswith("_migration_test"):
        raise RuntimeError("Database name must end in _migration_test")
    engine = create_async_engine(DATABASE_URL)
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def count_sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    try:
        # The migration integration test (or alembic upgrade head) creates the schema.
        async with engine.begin() as connection:
            await connection.execute(text("TRUNCATE users CASCADE"))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            user = User(email="performance@example.test", password_hash="unused")
            session.add(user)
            await session.flush()
            job = Job(owner_user_id=user.id, status=JobStatus.running, options={})
            session.add(job)
            await session.commit()
            statements.clear()
            yield session, job, statements, factory
    finally:
        await engine.dispose()


async def collected_source(session, job):
    now = datetime.now(timezone.utc)
    token = TelegramIngestToken(
        owner_user_id=job.owner_user_id, name="test", token_hash="test-hash",
        expires_at=now + timedelta(days=1),
    )
    session.add(token)
    await session.flush()
    chat = TelegramChat(
        owner_user_id=job.owner_user_id, ingest_token_id=token.id,
        telegram_chat_id=42, title="Test", chat_type="channel",
        ingest_mode=TelegramIngestMode.external_push, initial_sync_from=NOW - timedelta(days=1),
        coverage_start=NOW - timedelta(days=1), coverage_end=NOW,
    )
    session.add(chat)
    await session.flush()
    run = TelegramSyncRun(
        chat_id=chat.id, owner_user_id=job.owner_user_id, ingest_token_id=token.id,
        status=TelegramSyncStatus.running, requested_start=NOW - timedelta(days=1),
        requested_end=NOW,
    )
    session.add(run)
    await session.flush()
    chat.lease_owner = f"external:{run.id}"
    chat.lease_expires_at = now + timedelta(minutes=5)
    await session.commit()
    return chat, run, IngestPrincipal(token_id=token.id, owner_user_id=job.owner_user_id)


def test_parser_batches_duplicates_and_unchanged_retry():
    async def scenario():
        async with database() as (session, job, statements, _):
            parsed = [parse_message({
                "id": index, "type": "message", "date": "2026-09-21T11:00:00",
                "text": "before", "photo": f"photos/{index}.jpg",
            }) for index in range(1, 501)]
            available = {f"photos/{i}.jpg": {"object_key": f"media/{i}", "size_bytes": i}
                         for i in range(1, 501)}
            result = await _persist_message_batch(session, job, parsed, available)
            assert result == (500, 500, 0)
            assert len(statements) == 4  # two SELECTs and two batched INSERTs
            original_id = (await session.execute(select(TelegramMessage.id).where(
                TelegramMessage.telegram_message_id == 1
            ))).scalar_one()
            statements.clear()
            await _persist_message_batch(session, job, parsed, available)
            assert len(statements) == 2  # an unchanged retry writes nothing
            duplicate = parse_message({
                "id": 1, "type": "message", "date": "2026-09-21T11:00:00",
                "text": "after", "photo": "photos/1.jpg",
            })
            result = await _persist_message_batch(session, job, [parsed[0], duplicate], available)
            assert result == (2, 2, 0)
            row = (await session.execute(select(TelegramMessage).where(
                TelegramMessage.telegram_message_id == 1
            ))).scalar_one()
            assert row.id == original_id and row.text == "after"
            assert len((await session.execute(select(TelegramMedia))).scalars().all()) == 500
    asyncio.run(scenario())


def test_external_ingest_upsert_chunks_and_preserves_ids_under_retries_and_concurrency():
    async def scenario():
        async with database() as (session, job, statements, factory):
            chat, run, principal = await collected_source(session, job)
            messages = [TelegramIngestMessageInput(
                telegram_message_id=i, timestamp=NOW, text="before", raw={"value": i}
            ) for i in range(1, 1000)]
            messages.append(messages[0].model_copy(update={"text": "last duplicate"}))
            statements.clear()
            assert await upsert_external_messages(
                session, principal=principal, run_id=run.id, messages=messages
            ) == 1000
            await session.commit()
            writes = [s for s in statements if s.startswith("INSERT INTO collected_telegram_messages")]
            assert len(writes) == 2
            rows = (await session.execute(select(CollectedTelegramMessage))).scalars().all()
            assert len(rows) == 999
            first = next(row for row in rows if row.telegram_message_id == 1)
            original_id = first.id
            assert first.text == "last duplicate"
            await session.commit()

            async def ingest(value):
                async with factory() as concurrent_session:
                    await upsert_external_messages(
                        concurrent_session, principal=principal, run_id=run.id,
                        messages=[messages[0].model_copy(update={"text": value})],
                    )
                    await concurrent_session.commit()

            await asyncio.gather(ingest("concurrent one"), ingest("concurrent two"))
            await session.refresh(first)
            assert first.id == original_id and first.text in {"concurrent one", "concurrent two"}
            assert first.raw == {"value": 1} and first.chat_id == chat.id
    asyncio.run(scenario())


def test_snapshot_batches_messages_media_and_cached_children():
    async def scenario():
        async with database() as (session, job, statements, _):
            chat, _, _ = await collected_source(session, job)
            job.telegram_chat_id = chat.id
            job.source_type = JobSourceType.telegram_chat
            job.report_start_at = NOW - timedelta(days=1)
            job.report_end_at = NOW
            job.options = {"force_partial_telegram_sync": True}
            message_ids = [uuid.uuid4() for _ in range(501)]
            media_ids = [uuid.uuid4() for _ in range(501)]
            await session.execute(insert(CollectedTelegramMessage), [{
                "id": message_id, "chat_id": chat.id, "owner_user_id": job.owner_user_id,
                "telegram_message_id": i + 1, "timestamp": NOW - timedelta(hours=1), "text": "text",
            } for i, message_id in enumerate(message_ids)])
            await session.execute(insert(CollectedTelegramMedia), [{
                "id": media_ids[i], "chat_id": chat.id, "owner_user_id": job.owner_user_id,
                "message_id": message_id, "telegram_media_key": str(i),
                "media_type": "audio" if i == 1 else "image", "filename": f"{i}.jpg",
                "status": StepStatus.failed_retryable if i == 3 else StepStatus.completed,
                "minio_object_key": None if i == 3 else f"media/{i}",
            } for i, message_id in enumerate(message_ids)])
            session.add_all([
                CollectedMediaAnalysis(media_id=media_ids[0], model_name=settings.vision_model,
                                       prompt_version=settings.media_analysis_prompt_version,
                                       description="cached analysis"),
                CollectedMediaAnalysis(media_id=media_ids[0], model_name="old-model",
                                       prompt_version=settings.media_analysis_prompt_version,
                                       description="ignore"),
                CollectedMediaTranscript(media_id=media_ids[1], provider="openai",
                                         model_name=settings.openai_transcription_model,
                                         response_format="text", status=StepStatus.completed,
                                         attempts=2, transcript_text="cached transcript"),
            ])
            await session.commit()
            worker = TelegramSnapshotWorker()
            worker.emit_event = AsyncMock()
            worker.enqueue = AsyncMock()
            statements.clear()
            await worker.handle(session, {"job_id": str(job.id)})
            assert sum(s.startswith("INSERT INTO telegram_messages ") for s in statements) == 2
            assert sum(s.startswith("INSERT INTO telegram_media ") for s in statements) == 2
            cache_reads = [s for s in statements if s.startswith("SELECT")
                           and "JOIN collected_media_analysis" in s]
            assert len(cache_reads) == 2
            rows = (await session.execute(select(TelegramMedia))).scalars().all()
            assert len(rows) == 501
            by_source = {row.source_media_id: row for row in rows}
            assert by_source[media_ids[0]].status == StepStatus.completed
            assert by_source[media_ids[2]].status == StepStatus.pending
            assert by_source[media_ids[3]].status == StepStatus.failed_permanent
            assert (await session.execute(select(MediaAnalysis.description))).scalar_one() == "cached analysis"
            transcript = (await session.execute(select(MediaTranscript))).scalar_one()
            assert transcript.transcript_text == "cached transcript" and transcript.attempts == 2
    asyncio.run(scenario())


def test_report_reuses_evidence_and_keeps_latest_runs_order_and_job_scope():
    async def scenario():
        async with database() as (session, job, statements, _):
            messages = [TelegramMessage(job_id=job.id, telegram_message_id=i, text=str(i))
                        for i in (1, 2)]
            questions = [Question(job_id=job.id, question_index=i, text=f"Question {i}")
                         for i in (1, 2, 3)]
            foreign_job = Job(owner_user_id=job.owner_user_id, options={})
            session.add_all([*messages, *questions, foreign_job])
            await session.flush()
            foreign_message = TelegramMessage(job_id=foreign_job.id, telegram_message_id=99, text="private")
            session.add(foreign_message)
            await session.flush()
            chunks = [MessageChunk(
                job_id=job.id, chunk_index=i, chunk_hash=str(i), text="chunk",
                message_ids=[str(messages[1].id), str(messages[0].id), str(foreign_message.id), "bad-uuid"],
            ) for i in range(2)]
            runs = [QuestionRun(
                job_id=job.id, question_id=questions[i].id, retrieval_k=10, rerank_k=5,
                status=StepStatus.completed, answer=f"Answer {i}", created_at=NOW,
            ) for i in range(2)]
            old_run = QuestionRun(job_id=job.id, question_id=questions[0].id,
                                  retrieval_k=1, rerank_k=1, answer="old",
                                  created_at=NOW - timedelta(hours=1))
            session.add_all([*chunks, *runs, old_run])
            await session.flush()
            session.add(MessageTranslation(job_id=job.id, message_id=messages[0].id,
                                           provider="libretranslate", target_language="en",
                                           source_text_hash="hash", translated_text="English"))
            session.add_all([RetrievalHit(
                question_run_id=run.id, chunk_id=chunk.id, retrieval_rank=i + 1,
                rerank_rank=None if i == 0 else 1, used_in_answer=True,
            ) for run in runs for i, chunk in enumerate(chunks)])
            await session.commit()
            statements.clear()
            rendered = await ReportWorker()._load_questions(session, job)
            assert len(statements) == 5  # questions/runs, hits, messages, media, translations
            assert "telegram_messages.raw" not in "\n".join(statements)
            assert "raw_response" not in "\n".join(statements)
            assert [q.answer for q in rendered] == ["Answer 0", "Answer 1", "No answer has been saved yet."]
            for question in rendered[:2]:
                assert [item.chunk_index for item in question.evidence] == [1, 0]
                for chunk in question.evidence:
                    assert [message.telegram_message_id for message in chunk.messages] == [2, 1]
                    assert chunk.messages[1].translation_text == "English"
            assert rendered[2].evidence == []
            # More than one bind batch, including missing IDs, must remain safe.
            chunks[0].message_ids = [str(uuid.uuid4()) for _ in range(501)]
            await session.commit()
            statements.clear()
            await ReportWorker()._load_questions(session, job)
            assert len(statements) == 8
    asyncio.run(scenario())


def test_capacity_counts_use_one_query_and_match_mixed_statuses():
    async def scenario():
        async with database() as (session, job, statements, _):
            session.add_all([WorkerTask(job_id=job.id, task_key=str(i), subject="test", status=status)
                             for i, status in enumerate(StepStatus)])
            session.add_all([TelegramMedia(job_id=job.id, media_type="image",
                                          original_path=str(i), status=status)
                             for i, status in enumerate(StepStatus)])
            await session.commit()
            statements.clear()
            assert await _db_counts(session) == {
                "active_jobs": 1, "pending_media_tasks": 3, "pending_worker_tasks": 3,
                "failed_retryable_tasks": 1, "dead_letters_total": 0,
            }
            assert len(statements) == 1
            statements.clear()
            stats = await ReportWorker()._load_stats(session, job)
            assert len(statements) == 5
            assert stats["media_total"] == 6
            assert stats["media_completed"] == 1 and stats["media_failed"] == 2
            assert stats["messages_total"] == 0 and stats["media_missing"] == 0
    asyncio.run(scenario())


def test_retrieval_batches_existence_checks_and_preserves_rank_and_scope(monkeypatch):
    from types import SimpleNamespace
    from app.workers import rag_worker

    async def scenario():
        async with database() as (session, job, statements, _):
            other_job = Job(owner_user_id=job.owner_user_id, options={})
            question = Question(job_id=job.id, question_index=1, text="question")
            session.add_all([other_job, question])
            await session.flush()
            chunks = [MessageChunk(job_id=owner.id, chunk_index=i, chunk_hash=str(i), text="chunk")
                      for i, owner in enumerate([job, job, other_job])]
            session.add_all(chunks)
            await session.commit()
            hits = [{"id": str(chunk.id), "score": score}
                    for chunk, score in [(chunks[1], 0.9), (chunks[2], 0.8),
                                         (chunks[1], 0.7), (chunks[0], 0.6)]]
            hits += [{"id": str(uuid.uuid4()), "score": 0.1} for _ in range(501)]
            hits += [{"id": "invalid", "score": 0}]
            embedder = SimpleNamespace(embed_parts=AsyncMock(return_value=[
                SimpleNamespace(segment=SimpleNamespace(parent_index=0), vector=[1.0])
            ]))
            monkeypatch.setattr(rag_worker, "EmbeddingClient", lambda: embedder)
            monkeypatch.setattr(rag_worker, "QdrantIndex", object)
            monkeypatch.setattr(rag_worker, "_search_unique_parent_hits", AsyncMock(return_value=hits))
            worker = rag_worker.RetrieveWorker()
            worker.emit_event = AsyncMock()
            worker.enqueue = AsyncMock()
            for _ in range(2):
                statements.clear()
                await worker.handle(session, {"job_id": str(job.id)})
                lookups = [s for s in statements if s.startswith("SELECT message_chunks.id")]
                assert len(lookups) == 2
                saved = (await session.execute(
                    select(RetrievalHit).order_by(RetrievalHit.retrieval_rank)
                )).scalars().all()
                assert [(hit.chunk_id, hit.retrieval_rank, hit.retrieval_score) for hit in saved] == [
                    (chunks[1].id, 1, 0.9), (chunks[0].id, 2, 0.6),
                ]
                await session.commit()
    asyncio.run(scenario())


def test_media_progress_aggregates_and_transcript_batch_retries():
    from app.workers.media_worker import MediaWorker
    from app.workers.transcription_worker import TranscriptionWorker

    async def scenario():
        async with database() as (session, job, statements, _):
            media = [TelegramMedia(job_id=job.id, media_type="video", original_path=str(i),
                                   minio_object_key=f"media/{i}" if i != 2 else None,
                                   status=status)
                     for i, status in enumerate(StepStatus)]
            session.add_all(media)
            await session.flush()
            transcripts = [MediaTranscript(
                job_id=job.id, media_id=row.id, provider="openai",
                model_name=settings.openai_transcription_model, response_format="text",
                status=status, attempts=i, transcript_text=f"text {i}",
            ) for i, (row, status) in enumerate(zip(media[:5], (
                StepStatus.completed, StepStatus.running, StepStatus.failed_retryable,
                StepStatus.failed_permanent, StepStatus.pending,
            ), strict=True))]
            session.add_all(transcripts)
            session.add(MediaTranscript(job_id=job.id, media_id=media[0].id, provider="openai",
                                        model_name="old-model", response_format="text",
                                        status=StepStatus.completed))
            await session.commit()
            statements.clear()
            assert await MediaWorker()._stats(session, job.id) == {
                "media_total": 6, "media_done": 1, "media_pending": 1, "media_running": 1,
                "media_retryable_failed": 1, "media_permanent_failed": 1,
            }
            assert len(statements) == 1
            statements.clear()
            worker = TranscriptionWorker()
            counts = await worker._stats(session, job.id)
            assert len(statements) == 1
            assert counts == {
                "total": 5, "done": 1, "transcription_total": 5, "transcription_done": 1,
                "transcription_pending": 1, "transcription_running": 1,
                "transcription_retryable_failed": 1, "transcription_permanent_failed": 1,
            }
            original_id = transcripts[4].id
            statements.clear()
            next_rows = await worker._next_batch(session, job.id)
            assert {row.id for row in next_rows} == {media[i].id for i in (1, 4, 5)}
            statements.clear()
            await worker._mark_batch_running(session, next_rows)
            assert sum(s.startswith("SELECT") for s in statements) == 1
            assert transcripts[4].id == original_id and transcripts[4].attempts == 4
            assert transcripts[4].transcript_text == "text 4"
            rows = (await session.execute(select(MediaTranscript).where(
                MediaTranscript.media_id.in_([row.id for row in next_rows]),
                MediaTranscript.model_name == settings.openai_transcription_model,
            ))).scalars().all()
            assert len(rows) == 3 and all(row.status == StepStatus.running for row in rows)
    asyncio.run(scenario())

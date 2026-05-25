import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    cancelling = "cancelling"
    cancelled = "cancelled"
    failed = "failed"
    completed = "completed"


class StepStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed_retryable = "failed_retryable"
    failed_permanent = "failed_permanent"
    skipped = "skipped"


class UploadStatus(str, enum.Enum):
    created = "created"
    uploaded = "uploaded"
    rejected = "rejected"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class QuestionSet(Base):
    __tablename__ = "question_sets"
    __table_args__ = (UniqueConstraint("owner_user_id", "name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_translate: Mapped[bool] = mapped_column(Boolean, default=False)
    default_analyze_media: Mapped[bool] = mapped_column(Boolean, default=True)
    default_retrieval_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_rerank_k: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list["QuestionSetItem"]] = relationship(
        "QuestionSetItem",
        cascade="all, delete-orphan",
        order_by="QuestionSetItem.question_index",
        lazy="selectin",
    )


class QuestionSetItem(Base):
    __tablename__ = "question_set_items"
    __table_args__ = (UniqueConstraint("question_set_id", "question_index"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_set_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("question_sets.id"), index=True)
    question_index: Mapped[int] = mapped_column(Integer)
    client_question_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str] = mapped_column(Text)


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    object_key: Mapped[str] = mapped_column(String(1024), unique=True)
    status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus), default=UploadStatus.created)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    upload_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("uploads.id"), index=True)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.queued, index=True)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    upload: Mapped[Upload] = relationship("Upload")


class JobStep(Base):
    __tablename__ = "job_steps"
    __table_args__ = (UniqueConstraint("job_id", "step_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    step_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[StepStatus] = mapped_column(Enum(StepStatus), default=StepStatus.pending)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    done: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(128), index=True)
    level: Mapped[str] = mapped_column(String(32), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class WorkerTask(Base):
    __tablename__ = "worker_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_key: Mapped[str] = mapped_column(String(1024), unique=True, index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[StepStatus] = mapped_column(Enum(StepStatus), default=StepStatus.pending)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)




class WorkerDeadLetter(Base):
    __tablename__ = "worker_dead_letters"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    worker_task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("worker_tasks.id"), nullable=True, index=True)
    task_key: Mapped[str] = mapped_column(String(1024), index=True)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(255), index=True)
    error_message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class TelegramMessage(Base):
    __tablename__ = "telegram_messages"
    __table_args__ = (UniqueConstraint("job_id", "telegram_message_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    telegram_message_id: Mapped[int] = mapped_column(BigInteger, index=True)
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    edited_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sender_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    sender_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    message_type: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    reply_to_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    forwarded_from: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reactions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    text: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TelegramMedia(Base):
    __tablename__ = "telegram_media"
    __table_args__ = (UniqueConstraint("job_id", "message_id", "original_path"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("telegram_messages.id"), nullable=True, index=True)
    media_type: Mapped[str] = mapped_column(String(64), index=True)
    original_path: Mapped[str] = mapped_column(String(2048))
    minio_object_key: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[StepStatus] = mapped_column(Enum(StepStatus), default=StepStatus.pending)
    missing_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis_attempts: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MediaAnalysis(Base):
    __tablename__ = "media_analysis"
    __table_args__ = (UniqueConstraint("media_id", "model_name", "prompt_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("telegram_media.id"), index=True)
    model_name: Mapped[str] = mapped_column(String(512))
    prompt_version: Mapped[str] = mapped_column(String(128), default="neutral-v1")
    description: Mapped[str] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class MessageChunk(Base):
    __tablename__ = "message_chunks"
    __table_args__ = (UniqueConstraint("job_id", "chunk_index"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_hash: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    message_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    start_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    has_media: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding_model: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    embedding_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    client_question_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    question_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)


class QuestionRun(Base):
    __tablename__ = "question_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("questions.id"), index=True)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), index=True)
    retrieval_k: Mapped[int] = mapped_column(Integer)
    rerank_k: Mapped[int] = mapped_column(Integer)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[StepStatus] = mapped_column(Enum(StepStatus), default=StepStatus.pending)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class RetrievalHit(Base):
    __tablename__ = "retrieval_hits"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    question_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("question_runs.id"), index=True)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("message_chunks.id"), index=True)
    retrieval_rank: Mapped[int] = mapped_column(Integer)
    retrieval_score: Mapped[float | None] = mapped_column(nullable=True)
    rerank_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rerank_score: Mapped[float | None] = mapped_column(nullable=True)
    used_in_answer: Mapped[bool] = mapped_column(Boolean, default=False)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id"), unique=True, index=True)
    object_key: Mapped[str] = mapped_column(String(2048))
    filename: Mapped[str] = mapped_column(String(512), default="report.zip")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

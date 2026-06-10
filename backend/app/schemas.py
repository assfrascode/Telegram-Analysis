import uuid
from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    email: str
    password: str


class UploadCreateRequest(BaseModel):
    filename: str
    size_bytes: int = Field(gt=0)


class UploadCreateResponse(BaseModel):
    upload_id: uuid.UUID
    object_key: str
    presigned_put_url: str
    backend_upload_url: str


class QuestionInput(BaseModel):
    id: str | None = None
    text: str = Field(min_length=1)


class JobOptions(BaseModel):
    translate: bool = False
    analyze_media: bool = True
    retrieval_k: int = Field(default=50, ge=1, le=200)
    rerank_k: int = Field(default=15, ge=1, le=100)

    @model_validator(mode="after")
    def validate_rerank_not_larger_than_retrieval(self) -> Self:
        if self.rerank_k > self.retrieval_k:
            raise ValueError("rerank_k must not be greater than retrieval_k")
        return self


class JobCreateRequest(BaseModel):
    upload_id: uuid.UUID
    questions: list[QuestionInput] | None = None
    question_set_id: uuid.UUID | None = None
    options: JobOptions = Field(default_factory=JobOptions)

    @model_validator(mode="after")
    def validate_question_source(self) -> Self:
        if not self.questions and self.question_set_id is None:
            raise ValueError("Either questions or question_set_id must be provided")
        return self


class TelegramReportCreateRequest(BaseModel):
    telegram_chat_id: uuid.UUID
    start_at: datetime
    end_at: datetime
    questions: list[QuestionInput] | None = None
    question_set_id: uuid.UUID | None = None
    options: JobOptions = Field(default_factory=JobOptions)

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if not self.questions and self.question_set_id is None:
            raise ValueError("Either questions or question_set_id must be provided")
        if self.start_at >= self.end_at:
            raise ValueError("start_at must be before end_at")
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("start_at and end_at must include a timezone")
        return self


class JobResponse(BaseModel):
    id: uuid.UUID
    status: str
    source_type: str = "upload"
    telegram_chat_id: uuid.UUID | None = None
    report_start_at: datetime | None = None
    report_end_at: datetime | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None


class EventResponse(BaseModel):
    id: int
    event_type: str
    level: str
    message: str
    payload: dict
    created_at: datetime


class QuestionSetItemInput(BaseModel):
    id: str | None = None
    text: str = Field(min_length=1)


class QuestionSetOptions(BaseModel):
    translate: bool = False
    analyze_media: bool = True
    retrieval_k: int = Field(default=50, ge=1, le=200)
    rerank_k: int = Field(default=15, ge=1, le=100)

    @model_validator(mode="after")
    def validate_rerank_not_larger_than_retrieval(self) -> Self:
        if self.rerank_k > self.retrieval_k:
            raise ValueError("rerank_k must not be greater than retrieval_k")
        return self


class QuestionSetCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    questions: list[QuestionSetItemInput] = Field(min_length=1, max_length=100)
    default_options: QuestionSetOptions = Field(default_factory=QuestionSetOptions)


class QuestionSetUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    questions: list[QuestionSetItemInput] | None = Field(default=None, min_length=1, max_length=100)
    default_options: QuestionSetOptions | None = None


class QuestionSetResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    questions: list[QuestionSetItemInput]
    default_options: QuestionSetOptions
    question_count: int
    created_at: datetime
    updated_at: datetime


class TelegramLoginStartRequest(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=8, max_length=128)
    phone: str = Field(min_length=5, max_length=64)


class TelegramLoginCodeRequest(BaseModel):
    challenge_id: uuid.UUID
    code: str = Field(min_length=3, max_length=32)


class TelegramLoginPasswordRequest(BaseModel):
    challenge_id: uuid.UUID
    password: str = Field(min_length=1, max_length=512)


class TelegramConnectionResponse(BaseModel):
    connected: bool
    status: str
    telegram_user_id: int | None = None
    phone: str | None = None
    display_name: str | None = None
    last_error: str | None = None
    last_verified_at: datetime | None = None


class TelegramDialogResponse(BaseModel):
    telegram_chat_id: int
    # Telegram access hashes are signed 64-bit integers. Keep them as decimal
    # strings over JSON so browsers do not round them as JavaScript Numbers.
    access_hash: str | None = None
    title: str
    username: str | None = None
    chat_type: str


class TelegramChatCreateRequest(BaseModel):
    telegram_chat_id: int
    access_hash: str | None = None
    title: str = Field(min_length=1, max_length=512)
    username: str | None = Field(default=None, max_length=255)
    chat_type: str = Field(pattern="^(group|megagroup|channel)$")
    initial_sync_from: datetime
    sync_interval_minutes: int = Field(default=60)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if self.sync_interval_minutes not in {15, 60, 360, 1440}:
            raise ValueError("sync_interval_minutes must be one of 15, 60, 360, 1440")
        return self


class TelegramChatUpdateRequest(BaseModel):
    sync_interval_minutes: int | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if (
            self.sync_interval_minutes is not None
            and self.sync_interval_minutes not in {15, 60, 360, 1440}
        ):
            raise ValueError("sync_interval_minutes must be one of 15, 60, 360, 1440")
        return self


class TelegramChatResponse(BaseModel):
    id: uuid.UUID
    telegram_chat_id: int
    title: str
    username: str | None = None
    chat_type: str
    initial_sync_from: datetime
    sync_interval_minutes: int
    status: str
    last_error: str | None = None
    last_sync_at: datetime | None = None
    next_sync_at: datetime
    coverage_start: datetime | None = None
    coverage_end: datetime | None = None

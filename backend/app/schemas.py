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


class JobResponse(BaseModel):
    id: uuid.UUID
    status: str
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

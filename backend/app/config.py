from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Chat Analyse MVP"
    app_env: str = "local"
    app_base_url: str = "http://localhost:8000"
    secret_key: str
    access_token_expire_minutes: int = 720

    bootstrap_admin_email: str = "admin@example.local"
    bootstrap_admin_password: str = "change-me"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "chat_analyse"
    postgres_user: str = "chat_analyse"
    postgres_password: str = "chat_analyse"

    nats_url: str = "nats://nats:4222"

    minio_endpoint: str = "minio:9000"
    minio_public_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "chat-analyse"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "chat_chunks"
    embedding_batch_size: int = 64

    vllm_text_base_url: str = "http://vllm-text:8000/v1"
    vllm_vision_base_url: str = "http://vllm-vision:8000/v1"
    vllm_embedding_base_url: str = "http://vllm-embedding:8000/v1"
    vllm_reranker_base_url: str = "http://reranker:8000"
    vllm_api_key: str = "local-key"

    text_model: str = "google/gemma-4-E2B-it"
    vision_model: str = "google/gemma-4-E2B-it"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"

    max_active_jobs: int = 2
    max_pending_media_tasks: int = 20000
    max_pending_worker_tasks: int = 50000
    max_failed_retryable_tasks: int = 5000
    max_nats_task_stream_messages: int = 100000
    capacity_health_timeout_seconds: float = 2.0
    capacity_require_postgres: bool = True
    capacity_require_minio: bool = True
    capacity_require_nats: bool = True
    capacity_require_qdrant: bool = True
    capacity_check_vllm: bool = False
    capacity_require_vllm: bool = False
    max_worker_task_attempts: int = 3

    # Subject-specific retry limits override MAX_WORKER_TASK_ATTEMPTS.
    # JSON env example: {"jobs.embedding.create":5,"jobs.report.render":2}
    worker_task_max_attempts_by_subject: dict[str, int] = Field(default_factory=dict)
    # Media row failures are evidence-quality issues by default, not job-fatal.
    # Set true when any permanently failed media analysis should fail the whole job.
    media_fail_job_on_error: bool = False
    worker_retry_base_delay_seconds: int = 10
    worker_retry_max_delay_seconds: int = 300
    max_upload_bytes: int = 50 * 1024 * 1024 * 1024
    max_extracted_bytes: int = 100 * 1024 * 1024 * 1024
    max_zip_files: int = 1_000_000
    max_file_bytes: int = 50 * 1024 * 1024 * 1024

    default_retrieval_k: int = 50
    default_rerank_k: int = 15
    answer_context_max_chars: int = 120_000

    # Mock mode avoids all vLLM/reranker HTTP calls and is useful for fast local
    # pipeline tests without GPUs/model servers.
    llm_mock_enabled: bool = False
    mock_embedding_dimensions: int = 64

    chunk_target_chars: int = 8000
    chunk_overlap_messages: int = 2

    media_analysis_concurrency: int = 4
    media_analysis_batch_size: int = 100
    media_analysis_prompt_version: str = "neutral-v1"
    media_analysis_transport: str = "data_url"  # data_url | internal_presigned_url
    max_inline_media_analysis_bytes: int = 256 * 1024 * 1024
    vllm_media_request_timeout_seconds: float = 300.0
    max_media_analysis_attempts: int = 3

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()

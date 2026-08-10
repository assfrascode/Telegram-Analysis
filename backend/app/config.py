from functools import lru_cache
from typing import Literal, Self
from urllib.parse import quote, unquote, urlsplit

from cryptography.fernet import Fernet
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../.env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Chat Analyse MVP"
    # APP_ENV is deliberately required. Weak development secrets are accepted
    # only when the operator explicitly selects local or test mode.
    app_env: Literal["local", "test", "production"]
    app_role: Literal["api", "worker", "telegram_collector", "scheduler", "all"]
    app_base_url: str = "http://localhost:8000"
    secret_key: str = ""
    access_token_expire_minutes: int = Field(default=60, ge=1, le=24 * 60)
    telegram_credentials_encryption_key: str = ""
    telegram_login_challenge_minutes: int = 15
    telegram_sync_poll_seconds: int = 30
    telegram_sync_lease_minutes: int = 30
    telegram_sync_concurrency: int = 2
    telegram_sync_inactivity_timeout_seconds: int = Field(
        default=15 * 60,
        validation_alias=AliasChoices(
            "TELEGRAM_SYNC_INACTIVITY_TIMEOUT_SECONDS",
            "TELEGRAM_SYNC_TIMEOUT_SECONDS",
        ),
    )
    telegram_media_download_timeout_seconds: int = 10 * 60
    telegram_sync_retry_minutes: int = 5
    telegram_external_inactivity_timeout_seconds: int = Field(
        default=15 * 60,
        validation_alias=AliasChoices(
            "TELEGRAM_EXTERNAL_INACTIVITY_TIMEOUT_SECONDS",
            "TELEGRAM_EXTERNAL_COVERAGE_WAIT_SECONDS",
        ),
    )
    telegram_external_initial_response_timeout_seconds: int = 60
    report_scheduler_poll_seconds: int = 30
    report_scheduler_lease_minutes: int = 5

    bootstrap_admin_enabled: bool = False
    bootstrap_admin_email: str = ""
    bootstrap_admin_password: str = ""

    auth_rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    auth_login_attempts_per_window: int = Field(default=10, ge=1, le=1000)
    auth_register_attempts_per_window: int = Field(default=5, ge=1, le=1000)
    auth_telegram_attempts_per_window: int = Field(default=5, ge=1, le=1000)
    registration_enabled: bool = False
    websocket_ticket_expire_seconds: int = Field(default=60, ge=10, le=300)
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    max_request_body_bytes: int = Field(default=1024 * 1024, gt=0)
    max_concurrent_uploads: int = Field(default=1, ge=1, le=16)
    max_ingest_media_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    max_ingest_messages_body_bytes: int = Field(default=16 * 1024 * 1024, gt=0)

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "chat_analyse"
    postgres_user: str = "chat_analyse"
    postgres_password: str = "chat_analyse"

    nats_url: str = "nats://nats:4222"
    nats_user: str = ""
    nats_password: str = ""
    nats_token: str = ""

    minio_endpoint: str = "minio:9000"
    minio_public_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket: str = "chat-analyse"

    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "chat_chunks"
    qdrant_api_key: str = ""
    embedding_batch_size: int = 64

    libretranslate_base_url: str = ""
    libretranslate_api_key: str = ""
    libretranslate_batch_size: int = 20
    libretranslate_timeout_seconds: float = 60.0

    vllm_text_base_url: str = "http://vllm-text:8000/v1"
    vllm_vision_base_url: str = "http://vllm-vision:8000/v1"
    vllm_embedding_base_url: str = "http://vllm-embedding:8000/v1"
    vllm_reranker_base_url: str = "http://reranker:8000"
    vllm_api_key: str = "local-key"

    openai_api_key: str = ""
    openai_transcription_base_url: str = "https://api.openai.com/v1"
    openai_transcription_model: str = "whisper-1"
    openai_transcription_max_bytes: int = 25 * 1024 * 1024
    openai_transcription_timeout_seconds: float = 300.0
    openai_transcription_batch_size: int = 20
    openai_transcription_max_attempts: int = 3

    text_model: str = "google/gemma-4-E2B-it"
    vision_model: str = "google/gemma-4-E2B-it"
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    reranker_model: str = "Qwen/Qwen3-Reranker-0.6B"
    prompt_limit_safety_tokens: int = 256
    prompt_limit_tiktoken_ratio: float = 0.75
    prompt_limit_tiktoken_encoding: str = "cl100k_base"
    prompt_limit_cache_ttl_seconds: int = 300
    prompt_limit_models_timeout_seconds: float = 10.0
    prompt_limit_mock_max_model_len: int = 128_000
    prompt_limit_chat_message_overhead_tokens: int = 16
    prompt_limit_rerank_pair_overhead_tokens: int = 16
    # Optional JSON map used only when /v1/models does not expose max_model_len.
    # Keys may be the model name or "{base_url}|{model_name}".
    prompt_limit_max_model_len_overrides: dict[str, int] = Field(default_factory=dict)

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
    recover_stale_queued_jobs: bool = True
    stale_queued_job_after_seconds: int = 120
    stale_queued_job_recovery_limit: int = 100

    # Subject-specific retry limits override MAX_WORKER_TASK_ATTEMPTS.
    # JSON env example: {"jobs.embedding.create":5,"jobs.report.render":2}
    worker_task_max_attempts_by_subject: dict[str, int] = Field(default_factory=dict)
    # Media row failures are evidence-quality issues by default, not job-fatal.
    # Set true when any permanently failed media analysis should fail the whole job.
    media_fail_job_on_error: bool = False
    worker_retry_base_delay_seconds: int = 10
    worker_retry_max_delay_seconds: int = 300
    max_upload_bytes: int = 1024 * 1024 * 1024
    max_extracted_bytes: int = 4 * 1024 * 1024 * 1024
    max_zip_files: int = 10_000
    max_file_bytes: int = 1024 * 1024 * 1024
    max_telegram_message_chars: int = 1_000_000
    max_telegram_messages_per_export: int = 2_000_000
    max_html_page_bytes: int = 16 * 1024 * 1024

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
    media_analysis_prompt_version: str = "neutral-en-v2"
    media_analysis_transport: str = "data_url"  # data_url | internal_presigned_url
    max_inline_media_analysis_bytes: int = 25 * 1024 * 1024
    vllm_media_request_timeout_seconds: float = 300.0
    max_media_analysis_attempts: int = 3

    @property
    def database_url(self) -> str:
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql+asyncpg://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @model_validator(mode="after")
    def validate_security_configuration(self) -> Self:
        local_mode = self.app_env in {"local", "test"}
        api_role = self.app_role in {"api", "all"}
        nats_role = self.app_role in {"api", "worker", "scheduler", "all"}
        qdrant_role = self.app_role in {"api", "worker", "scheduler", "all"}
        fernet_role = self.app_role in {"api", "worker", "telegram_collector", "all"}
        vllm_role = self.app_role in {"worker", "all"} or (
            self.app_role in {"api", "scheduler"} and self.capacity_check_vllm
        )
        secret = self.secret_key.strip()
        known_secrets = {
            "change-me",
            "change-this-development-secret",
            "test-secret",
            "local-key",
        }
        if api_role and not secret:
            raise ValueError("SECRET_KEY must not be empty for the API role")
        if not local_mode and api_role and (len(secret) < 32 or secret.lower() in known_secrets):
            raise ValueError("Production SECRET_KEY must be an unpredictable value of at least 32 characters")

        configured_fernet = self.telegram_credentials_encryption_key.strip()
        if configured_fernet:
            try:
                Fernet(configured_fernet.encode("ascii"))
            except (UnicodeEncodeError, ValueError, TypeError) as exc:
                raise ValueError("TELEGRAM_CREDENTIALS_ENCRYPTION_KEY must be a valid Fernet key") from exc
            if configured_fernet == secret:
                raise ValueError("Telegram credential encryption must use a key distinct from SECRET_KEY")
        elif not local_mode and fernet_role:
            raise ValueError("TELEGRAM_CREDENTIALS_ENCRYPTION_KEY is required in production")

        if self.bootstrap_admin_enabled:
            if not self.bootstrap_admin_email.strip() or "@" not in self.bootstrap_admin_email:
                raise ValueError("BOOTSTRAP_ADMIN_EMAIL is required when bootstrap admin is enabled")
            password = self.bootstrap_admin_password
            minimum = 8 if local_mode else 16
            if len(password) < minimum or password.lower() in {"change-me", "password", "admin"}:
                raise ValueError(f"BOOTSTRAP_ADMIN_PASSWORD must contain at least {minimum} characters")

        if api_role and not self.trusted_hosts:
            raise ValueError("TRUSTED_HOSTS must contain at least one host")
        if api_role and any(not host.strip() for host in self.trusted_hosts):
            raise ValueError("TRUSTED_HOSTS cannot contain blank hosts")
        if not local_mode and api_role and "*" in self.trusted_hosts:
            raise ValueError("TRUSTED_HOSTS cannot contain '*' in production")

        if bool(self.nats_user) != bool(self.nats_password):
            raise ValueError("NATS_USER and NATS_PASSWORD must be configured together")
        if self.nats_token and self.nats_user:
            raise ValueError("Configure either NATS_TOKEN or NATS_USER/NATS_PASSWORD, not both")

        if not local_mode:
            app_origin = urlsplit(self.app_base_url)
            if api_role and (
                app_origin.scheme != "https"
                or not app_origin.netloc
                or app_origin.username
                or app_origin.password
                or app_origin.query
                or app_origin.fragment
                or app_origin.path not in {"", "/"}
            ):
                raise ValueError("APP_BASE_URL must be an absolute HTTPS origin in production")
            if not self.postgres_host.strip() or not self.postgres_db.strip():
                raise ValueError("POSTGRES_HOST and POSTGRES_DB must not be empty")
            if not (1 <= len(self.postgres_user.strip()) <= 63):
                raise ValueError("POSTGRES_USER must contain between 1 and 63 characters")
            if self.postgres_user == "chat_analyse" or self.postgres_password == "chat_analyse":
                raise ValueError("Default PostgreSQL credentials are forbidden in production")
            if len(self.postgres_password) < 16:
                raise ValueError("POSTGRES_PASSWORD must contain at least 16 characters in production")
            if not (3 <= len(self.minio_access_key.strip()) <= 128):
                raise ValueError("MINIO_ACCESS_KEY must contain between 3 and 128 characters")
            if self.minio_access_key == "minioadmin" or self.minio_secret_key == "minioadmin":
                raise ValueError("Default MinIO credentials are forbidden in production")
            if not (16 <= len(self.minio_secret_key) <= 128):
                raise ValueError("MINIO_SECRET_KEY must contain between 16 and 128 characters in production")

            parsed_nats_url = urlsplit(self.nats_url)
            embedded_nats_auth = bool(parsed_nats_url.username and parsed_nats_url.password)
            if parsed_nats_url.username and not parsed_nats_url.password:
                raise ValueError("NATS URL user authentication requires a password")
            if embedded_nats_auth and len(unquote(parsed_nats_url.password or "")) < 16:
                raise ValueError("Password embedded in NATS_URL must contain at least 16 characters")
            if nats_role and not (embedded_nats_auth or self.nats_token or self.nats_user):
                raise ValueError("NATS authentication is required in production")
            if nats_role and self.nats_token and len(self.nats_token) < 16:
                raise ValueError("NATS_TOKEN must contain at least 16 characters in production")
            if nats_role and self.nats_password and len(self.nats_password) < 16:
                raise ValueError("NATS_PASSWORD must contain at least 16 characters in production")
            if qdrant_role and len(self.qdrant_api_key) < 16:
                raise ValueError("QDRANT_API_KEY is required in production and must contain at least 16 characters")
            if vllm_role and (self.vllm_api_key == "local-key" or len(self.vllm_api_key) < 16):
                raise ValueError("VLLM_API_KEY must be non-default in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

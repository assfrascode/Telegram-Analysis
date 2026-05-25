from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData, text

from app.config import get_settings

settings = get_settings()

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=convention)


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    # MVP convenience. Replace with Alembic before production.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await apply_mvp_compat_migrations(conn)


async def apply_mvp_compat_migrations(conn) -> None:
    """Small compatibility migrations for the MVP prototype.

    ``Base.metadata.create_all`` creates missing tables but does not alter tables
    that already exist in a persistent Docker volume. Earlier MVP builds created
    ``telegram_messages`` without reply/forward/reaction columns. This helper
    adds the newly required nullable columns in-place so existing local test
    databases keep working. Replace this with Alembic before production use.
    """
    statements = [
        "ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT",
        "ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS forwarded_from VARCHAR(512)",
        "ALTER TABLE telegram_messages ADD COLUMN IF NOT EXISTS reactions JSON DEFAULT '[]'::json",
        "CREATE INDEX IF NOT EXISTS ix_telegram_messages_reply_to_message_id ON telegram_messages (reply_to_message_id)",
        "ALTER TABLE telegram_media ADD COLUMN IF NOT EXISTS analysis_attempts INTEGER DEFAULT 0",
        "ALTER TABLE telegram_media ADD COLUMN IF NOT EXISTS analyzed_at TIMESTAMPTZ",
        "ALTER TABLE message_chunks ADD COLUMN IF NOT EXISTS embedding_model VARCHAR(512)",
        "ALTER TABLE message_chunks ADD COLUMN IF NOT EXISTS embedding_hash VARCHAR(64)",
        "ALTER TABLE message_chunks ADD COLUMN IF NOT EXISTS qdrant_point_id VARCHAR(128)",
        "ALTER TABLE message_chunks ADD COLUMN IF NOT EXISTS embedded_at TIMESTAMPTZ",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_message_chunks_job_id_chunk_index ON message_chunks (job_id, chunk_index)",
        "CREATE INDEX IF NOT EXISTS ix_message_chunks_embedding_model ON message_chunks (embedding_model)",
        "CREATE INDEX IF NOT EXISTS ix_message_chunks_embedding_hash ON message_chunks (embedding_hash)",
        "CREATE INDEX IF NOT EXISTS ix_message_chunks_qdrant_point_id ON message_chunks (qdrant_point_id)",
    ]
    for statement in statements:
        await conn.execute(text(statement))

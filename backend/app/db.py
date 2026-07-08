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
        await conn.execute(
            text(
                """
                DO $$
                BEGIN
                    CREATE TYPE telegramingestmode AS ENUM ('backend_pull', 'external_push');
                EXCEPTION
                    WHEN duplicate_object THEN NULL;
                END $$;
                """
            )
        )
        await conn.execute(
            text(
                """
                ALTER TABLE telegram_chats
                ADD COLUMN IF NOT EXISTS ingest_mode telegramingestmode
                DEFAULT 'backend_pull' NOT NULL
                """
            )
        )
        await conn.execute(text("ALTER TABLE telegram_chats ALTER COLUMN connection_id DROP NOT NULL"))
        await conn.execute(
            text(
                """
                ALTER TABLE telegram_report_schedules
                ADD COLUMN IF NOT EXISTS allow_partial_telegram_sync boolean
                DEFAULT false NOT NULL
                """
            )
        )

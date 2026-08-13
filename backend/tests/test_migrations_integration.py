import asyncio
import os
from pathlib import Path

import pytest


alembic = pytest.importorskip("alembic")
asyncpg = pytest.importorskip("asyncpg")


DATABASE_URL = os.getenv("MIGRATION_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="Set MIGRATION_TEST_DATABASE_URL to a disposable PostgreSQL database ending in _migration_test",
)


def _configure_database_environment() -> None:
    from sqlalchemy.engine import make_url

    parsed = make_url(DATABASE_URL)
    if not (parsed.database or "").endswith("_migration_test"):
        raise RuntimeError("MIGRATION_TEST_DATABASE_URL must name a database ending in _migration_test")
    os.environ.update(
        {
            "APP_ENV": "test",
            "APP_ROLE": "migration",
            "POSTGRES_HOST": parsed.host or "localhost",
            "POSTGRES_PORT": str(parsed.port or 5432),
            "POSTGRES_DB": parsed.database or "",
            "POSTGRES_USER": parsed.username or "",
            "POSTGRES_PASSWORD": parsed.password or "",
        }
    )
    from app.config import get_settings

    get_settings.cache_clear()


async def _reset_database() -> None:
    connection = await asyncpg.connect(DATABASE_URL.replace("+asyncpg", ""))
    try:
        await connection.execute("DROP SCHEMA public CASCADE")
        await connection.execute("CREATE SCHEMA public")
    finally:
        await connection.close()


def _upgrade_head() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.upgrade(config, "head")


def _assert_no_migration_drift() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.check(config)


async def _create_legacy_mvp_schema() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app import models  # noqa: F401
    from app.db import Base

    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(text("DROP INDEX ix_telegram_chats_ingest_mode"))
            await connection.execute(text("ALTER TABLE telegram_chats DROP COLUMN ingest_mode"))
            await connection.execute(
                text("ALTER TABLE telegram_chats DROP COLUMN last_collected_message_id")
            )
            await connection.execute(
                text("ALTER TABLE telegram_chats ALTER COLUMN connection_id SET NOT NULL")
            )
            await connection.execute(
                text("ALTER TABLE telegram_report_schedules DROP COLUMN allow_partial_telegram_sync")
            )
            await connection.execute(text("ALTER TABLE jobs DROP COLUMN source_name"))
            await connection.execute(text("DROP TYPE telegramingestmode"))
    finally:
        await engine.dispose()


async def _schema_state() -> tuple[str, dict[str, set[str]]]:
    connection = await asyncpg.connect(DATABASE_URL.replace("+asyncpg", ""))
    try:
        revision = await connection.fetchval("SELECT version_num FROM alembic_version")
        rows = await connection.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
            """
        )
        columns: dict[str, set[str]] = {}
        for row in rows:
            columns.setdefault(row["table_name"], set()).add(row["column_name"])
        return revision, columns
    finally:
        await connection.close()


def test_clean_install_and_upgrade_from_legacy_mvp_schema():
    _configure_database_environment()

    asyncio.run(_reset_database())
    _upgrade_head()
    revision, clean_columns = asyncio.run(_schema_state())
    assert revision == "20260813_0001"
    assert "users" in clean_columns
    assert "source_name" in clean_columns["jobs"]
    _assert_no_migration_drift()

    asyncio.run(_reset_database())
    asyncio.run(_create_legacy_mvp_schema())
    _upgrade_head()
    revision, upgraded_columns = asyncio.run(_schema_state())
    assert revision == "20260813_0001"
    assert "ingest_mode" in upgraded_columns["telegram_chats"]
    assert "last_collected_message_id" in upgraded_columns["telegram_chats"]
    assert "allow_partial_telegram_sync" in upgraded_columns["telegram_report_schedules"]
    assert "source_name" in upgraded_columns["jobs"]
    _assert_no_migration_drift()

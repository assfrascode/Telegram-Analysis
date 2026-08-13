from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import MetaData, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings

settings = get_settings()
SCHEMA_REVISION = "20260813_0001"

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
    """Fail fast when the deployment did not run Alembic to the expected revision."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            revision = result.scalar_one_or_none()
    except SQLAlchemyError as exc:
        raise RuntimeError(
            "Database schema is not initialized; run `alembic upgrade head` before starting services"
        ) from exc
    if revision != SCHEMA_REVISION:
        raise RuntimeError(
            f"Database schema revision is {revision!r}; expected {SCHEMA_REVISION!r}. "
            "Run `alembic upgrade head` before starting services."
        )

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.security import hash_password, normalize_email
from app.services.minio_store import ensure_bucket

settings = get_settings()


async def bootstrap_admin(session: AsyncSession) -> None:
    admin_email = normalize_email(settings.bootstrap_admin_email)
    result = await session.execute(select(User).where(User.email == admin_email))
    if result.scalar_one_or_none():
        return

    session.add(
        User(
            email=admin_email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            is_active=True,
        )
    )
    await session.commit()


async def bootstrap_services(session: AsyncSession) -> None:
    ensure_bucket()
    await bootstrap_admin(session)

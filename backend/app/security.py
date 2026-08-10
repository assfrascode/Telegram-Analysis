from datetime import datetime, timedelta, timezone
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models import User

settings = get_settings()
password_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_DUMMY_PASSWORD_HASH = password_hasher.hash("not-a-real-account-password")
ALGORITHM = "HS256"
TOKEN_TYPE_ACCESS = "access"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHash, VerificationError, VerifyMismatchError):
        return False


def create_access_token(user_id: UUID) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": now,
        "typ": TOKEN_TYPE_ACCESS,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User | None:
    normalized_email = normalize_email(email)
    result = await session.execute(select(User).where(User.email == normalized_email, User.is_active.is_(True)))
    user = result.scalar_one_or_none()
    password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_valid = await run_in_threadpool(verify_password, password, password_hash)
    if user and password_valid:
        if password_hasher.check_needs_rehash(user.password_hash):
            user.password_hash = await run_in_threadpool(hash_password, password)
            await session.commit()
        return user
    return None


async def get_user_from_token(session: AsyncSession, token: str) -> User | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("typ") != TOKEN_TYPE_ACCESS:
            return None
        raw_user_id = payload.get("sub")
        if not raw_user_id:
            return None
        user_id = UUID(str(raw_user_id))
    except (JWTError, ValueError, TypeError):
        return None

    result = await session.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    return result.scalar_one_or_none()

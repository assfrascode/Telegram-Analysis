from datetime import datetime, timedelta, timezone
from uuid import UUID

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
TOKEN_TYPE_ACCESS = "access"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


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
    if user and verify_password(password, user.password_hash):
        return user
    return None


async def get_user_from_token(session: AsyncSession, token: str) -> User | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        if payload.get("typ") not in {None, TOKEN_TYPE_ACCESS}:
            return None
        raw_user_id = payload.get("sub")
        if not raw_user_id:
            return None
        user_id = UUID(str(raw_user_id))
    except (JWTError, ValueError, TypeError):
        return None

    result = await session.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
    return result.scalar_one_or_none()

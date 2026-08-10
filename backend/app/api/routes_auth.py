from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.config import get_settings
from app.models import User
from app.schemas import LoginRequest, RegisterRequest, TokenResponse
from app.security import authenticate_user, create_access_token, hash_password, normalize_email
from app.services.auth_rate_limit import enforce_auth_rate_limit
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    enforce_auth_rate_limit(
        action="login",
        client_ip=request.client.host if request.client else "unknown",
        identity=normalize_email(payload.email),
    )
    user = await authenticate_user(session, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return TokenResponse(access_token=create_access_token(user.id))


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    if not settings.registration_enabled:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Registration is disabled")
    enforce_auth_rate_limit(
        action="register",
        client_ip=request.client.host if request.client else "unknown",
    )
    normalized_email = normalize_email(payload.email)
    existing = await session.execute(select(User.id).where(User.email == normalized_email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered")

    user = User(
        email=normalized_email,
        password_hash=await run_in_threadpool(hash_password, payload.password),
        is_active=True,
    )
    session.add(user)

    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        ) from exc

    await session.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id))

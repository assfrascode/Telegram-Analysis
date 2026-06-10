
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_current_user
from app.models import User
from app.services.capacity import capacity_snapshot

router = APIRouter(tags=["capacity"])


@router.get("/capacity")
async def get_capacity(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    # Authenticated because capacity exposes internal service health and queue
    # pressure. The frontend calls this after login and before uploading a ZIP.
    return await capacity_snapshot(session)

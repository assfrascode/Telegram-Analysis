import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.dependencies import get_current_user
from app.models import User
from app.schemas import QuestionSetCreateRequest, QuestionSetResponse, QuestionSetUpdateRequest
from app.services.access_control import get_owned_question_set_or_404
from app.services.question_sets import (
    archive_question_set,
    create_question_set,
    duplicate_question_set,
    list_question_sets,
    update_question_set,
)

router = APIRouter(prefix="/question-sets", tags=["question-sets"])


@router.get("", response_model=list[QuestionSetResponse])
async def get_question_sets(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[QuestionSetResponse]:
    return await list_question_sets(session, user.id)


@router.post("", response_model=QuestionSetResponse)
async def post_question_set(
    payload: QuestionSetCreateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> QuestionSetResponse:
    return await create_question_set(session, user.id, payload)


@router.get("/{question_set_id}", response_model=QuestionSetResponse)
async def get_question_set(
    question_set_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> QuestionSetResponse:
    question_set = await get_owned_question_set_or_404(
        session,
        question_set_id=question_set_id,
        user=user,
    )
    from app.services.question_sets import _response  # local import avoids exposing helper in public API

    return _response(question_set)


@router.patch("/{question_set_id}", response_model=QuestionSetResponse)
async def patch_question_set(
    question_set_id: uuid.UUID,
    payload: QuestionSetUpdateRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> QuestionSetResponse:
    question_set = await get_owned_question_set_or_404(
        session,
        question_set_id=question_set_id,
        user=user,
    )
    return await update_question_set(session, question_set, payload)


@router.delete("/{question_set_id}")
async def delete_question_set(
    question_set_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    question_set = await get_owned_question_set_or_404(
        session,
        question_set_id=question_set_id,
        user=user,
    )
    return await archive_question_set(session, question_set)


@router.post("/{question_set_id}/duplicate", response_model=QuestionSetResponse)
async def duplicate_question_set_route(
    question_set_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> QuestionSetResponse:
    question_set = await get_owned_question_set_or_404(
        session,
        question_set_id=question_set_id,
        user=user,
    )
    return await duplicate_question_set(session, user.id, question_set)

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import QuestionSet, QuestionSetItem
from app.schemas import (
    JobOptions,
    QuestionInput,
    QuestionSetCreateRequest,
    QuestionSetItemInput,
    QuestionSetOptions,
    QuestionSetResponse,
    QuestionSetUpdateRequest,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_name(name: str) -> str:
    value = " ".join((name or "").strip().split())
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question set name is required")
    return value


def _clean_description(description: str | None) -> str | None:
    if description is None:
        return None
    value = description.strip()
    return value or None


def _normalize_items(items: list[QuestionSetItemInput]) -> list[QuestionSetItemInput]:
    normalized: list[QuestionSetItemInput] = []
    for idx, item in enumerate(items, start=1):
        text = (item.text or "").strip()
        if not text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Question {idx} is empty")
        client_id = (item.id or f"q{idx}").strip() or f"q{idx}"
        normalized.append(QuestionSetItemInput(id=client_id, text=text))
    return normalized


def _options_from_model(options: QuestionSetOptions | JobOptions) -> dict:
    return options.model_dump()


def _response(question_set: QuestionSet) -> QuestionSetResponse:
    options = QuestionSetOptions(
        translate=bool(question_set.default_translate),
        analyze_media=bool(question_set.default_analyze_media),
        retrieval_k=question_set.default_retrieval_k or 50,
        rerank_k=question_set.default_rerank_k or 15,
    )
    questions = [
        QuestionSetItemInput(id=item.client_question_id, text=item.text)
        for item in sorted(question_set.items, key=lambda row: row.question_index)
    ]
    return QuestionSetResponse(
        id=question_set.id,
        name=question_set.name,
        description=question_set.description,
        questions=questions,
        default_options=options,
        question_count=len(questions),
        created_at=question_set.created_at,
        updated_at=question_set.updated_at,
    )


async def list_question_sets(session: AsyncSession, owner_user_id: uuid.UUID) -> list[QuestionSetResponse]:
    rows = (
        await session.execute(
            select(QuestionSet)
            .options(selectinload(QuestionSet.items))
            .where(QuestionSet.owner_user_id == owner_user_id, QuestionSet.archived_at.is_(None))
            .order_by(func.lower(QuestionSet.name))
        )
    ).scalars().all()
    return [_response(row) for row in rows]


async def create_question_set(
    session: AsyncSession,
    owner_user_id: uuid.UUID,
    payload: QuestionSetCreateRequest,
) -> QuestionSetResponse:
    options = _options_from_model(payload.default_options)
    question_set = QuestionSet(
        owner_user_id=owner_user_id,
        name=_clean_name(payload.name),
        description=_clean_description(payload.description),
        default_translate=options["translate"],
        default_analyze_media=options["analyze_media"],
        default_retrieval_k=options["retrieval_k"],
        default_rerank_k=options["rerank_k"],
        created_at=_now(),
        updated_at=_now(),
    )
    question_set.items = [
        QuestionSetItem(question_index=idx, client_question_id=item.id, text=item.text)
        for idx, item in enumerate(_normalize_items(payload.questions), start=1)
    ]
    session.add(question_set)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A question set with this name already exists",
        ) from exc
    await session.refresh(question_set, attribute_names=["items"])
    return _response(question_set)


async def update_question_set(
    session: AsyncSession,
    question_set: QuestionSet,
    payload: QuestionSetUpdateRequest,
) -> QuestionSetResponse:
    if payload.name is not None:
        question_set.name = _clean_name(payload.name)
    if payload.description is not None:
        question_set.description = _clean_description(payload.description)
    if payload.default_options is not None:
        options = _options_from_model(payload.default_options)
        question_set.default_translate = options["translate"]
        question_set.default_analyze_media = options["analyze_media"]
        question_set.default_retrieval_k = options["retrieval_k"]
        question_set.default_rerank_k = options["rerank_k"]
    if payload.questions is not None:
        await session.execute(delete(QuestionSetItem).where(QuestionSetItem.question_set_id == question_set.id))
        await session.flush()
        question_set.items = [
            QuestionSetItem(
                question_set_id=question_set.id,
                question_index=idx,
                client_question_id=item.id,
                text=item.text,
            )
            for idx, item in enumerate(_normalize_items(payload.questions), start=1)
        ]
    question_set.updated_at = _now()
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A question set with this name already exists",
        ) from exc
    await session.refresh(question_set, attribute_names=["items"])
    return _response(question_set)


async def archive_question_set(session: AsyncSession, question_set: QuestionSet) -> dict:
    question_set.archived_at = _now()
    question_set.updated_at = _now()
    await session.commit()
    return {"ok": True, "id": str(question_set.id), "archived": True}


async def duplicate_question_set(
    session: AsyncSession,
    owner_user_id: uuid.UUID,
    question_set: QuestionSet,
) -> QuestionSetResponse:
    base = f"{question_set.name} Kopie"
    name = base
    existing_names = {
        row[0]
        for row in (
            await session.execute(
                select(QuestionSet.name).where(
                    QuestionSet.owner_user_id == owner_user_id,
                    QuestionSet.archived_at.is_(None),
                    QuestionSet.name.like(f"{base}%"),
                )
            )
        ).all()
    }
    counter = 2
    while name in existing_names:
        name = f"{base} {counter}"
        counter += 1

    payload = QuestionSetCreateRequest(
        name=name,
        description=question_set.description,
        default_options=QuestionSetOptions(
            translate=question_set.default_translate,
            analyze_media=question_set.default_analyze_media,
            retrieval_k=question_set.default_retrieval_k or 50,
            rerank_k=question_set.default_rerank_k or 15,
        ),
        questions=[
            QuestionSetItemInput(id=item.client_question_id, text=item.text)
            for item in sorted(question_set.items, key=lambda row: row.question_index)
        ],
    )
    return await create_question_set(session, owner_user_id, payload)


def question_inputs_from_set(question_set: QuestionSet) -> list[QuestionInput]:
    return [
        QuestionInput(id=item.client_question_id, text=item.text)
        for item in sorted(question_set.items, key=lambda row: row.question_index)
    ]


def question_set_snapshot(question_set: QuestionSet) -> dict:
    return {
        "id": str(question_set.id),
        "name": question_set.name,
        "snapshot_at": _now().isoformat(),
        "question_count": len(question_set.items),
    }

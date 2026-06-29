import asyncio
import os
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.api.routes_auth import register  # noqa: E402
from app.schemas import RegisterRequest  # noqa: E402
from app.security import authenticate_user, verify_password  # noqa: E402


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeSession:
    def __init__(self, execute_values=None, commit_error=None):
        self.execute_values = list(execute_values or [])
        self.commit_error = commit_error
        self.added = None
        self.committed = False
        self.rolled_back = False
        self.refreshed = None

    async def execute(self, statement):
        value = self.execute_values.pop(0) if self.execute_values else None
        return FakeResult(value)

    def add(self, value):
        self.added = value

    async def commit(self):
        if self.commit_error:
            raise self.commit_error
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, value):
        if value.id is None:
            value.id = uuid.uuid4()
        self.refreshed = value


def test_register_creates_user_and_returns_token():
    session = FakeSession(execute_values=[None])

    response = asyncio.run(
        register(RegisterRequest(email=" New.User@Example.COM ", password="correct horse"), session)
    )

    assert response.token_type == "bearer"
    assert response.access_token.count(".") == 2
    assert session.committed is True
    assert session.refreshed is session.added
    assert session.added.email == "new.user@example.com"
    assert session.added.is_active is True
    assert verify_password("correct horse", session.added.password_hash)


def test_register_rejects_duplicate_email():
    session = FakeSession(execute_values=[uuid.uuid4()])

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(register(RegisterRequest(email="taken@example.com", password="correct horse"), session))

    assert exc_info.value.status_code == 409
    assert session.added is None
    assert session.committed is False


def test_registered_credentials_authenticate():
    session = FakeSession(execute_values=[None])
    asyncio.run(register(RegisterRequest(email="auth@example.com", password="correct horse"), session))

    lookup_session = FakeSession(execute_values=[session.added])
    user = asyncio.run(authenticate_user(lookup_session, " AUTH@EXAMPLE.COM ", "correct horse"))

    assert user is session.added


def test_register_request_rejects_invalid_email():
    with pytest.raises(ValidationError):
        RegisterRequest(email="not-an-email", password="correct horse")


def test_register_request_rejects_short_password():
    with pytest.raises(ValidationError):
        RegisterRequest(email="new@example.com", password="short")


def test_register_rolls_back_unique_constraint_race():
    session = FakeSession(
        execute_values=[None],
        commit_error=IntegrityError("insert user", {}, Exception("duplicate")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(register(RegisterRequest(email="race@example.com", password="correct horse"), session))

    assert exc_info.value.status_code == 409
    assert session.rolled_back is True

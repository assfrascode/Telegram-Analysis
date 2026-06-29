import os
from uuid import uuid4

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.security import create_access_token


def test_access_token_is_non_empty():
    token = create_access_token(uuid4())
    assert isinstance(token, str)
    assert token.count('.') == 2

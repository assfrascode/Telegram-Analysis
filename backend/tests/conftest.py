import os

import pytest


# Tests explicitly opt into the relaxed test-only secret policy before any app
# module constructs its cached Settings instance.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_ROLE", "all")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("REGISTRATION_ENABLED", "true")


@pytest.fixture(autouse=True)
def direct_password_offload_in_sandbox(monkeypatch):
    """The managed test sandbox cannot create worker threads.

    Production keeps password hashing off the event loop; unit tests replace
    only that scheduling boundary while exercising the real Argon2 operation.
    """

    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("app.security.run_in_threadpool", direct)
    monkeypatch.setattr("app.api.routes_auth.run_in_threadpool", direct)
    monkeypatch.setattr("app.bootstrap.run_in_threadpool", direct)

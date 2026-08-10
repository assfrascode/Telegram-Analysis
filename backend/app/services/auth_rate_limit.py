import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

from app.config import get_settings


class AuthRateLimiter:
    """Small fail-closed, process-local sliding-window limiter.

    Deployment-level throttling should still be enabled at the reverse proxy;
    this guard ensures a directly exposed application process is not unlimited.
    """

    def __init__(self) -> None:
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_prune = 0.0
        self._max_keys = 50_000

    def check(self, key: str, *, limit: int, window_seconds: int) -> None:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            if now - self._last_prune >= window_seconds or len(self._attempts) >= self._max_keys:
                for existing_key, existing_attempts in list(self._attempts.items()):
                    while existing_attempts and existing_attempts[0] <= cutoff:
                        existing_attempts.popleft()
                    if not existing_attempts:
                        self._attempts.pop(existing_key, None)
                self._last_prune = now
            if key not in self._attempts and len(self._attempts) >= self._max_keys:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Authentication throttling capacity exceeded",
                    headers={"Retry-After": str(window_seconds)},
                )
            attempts = self._attempts[key]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if len(attempts) >= limit:
                retry_after = max(1, int(window_seconds - (now - attempts[0])) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many authentication attempts",
                    headers={"Retry-After": str(retry_after)},
                )
            attempts.append(now)

    def clear(self) -> None:
        with self._lock:
            self._attempts.clear()


auth_rate_limiter = AuthRateLimiter()


def enforce_auth_rate_limit(*, action: str, client_ip: str, identity: str = "") -> None:
    settings = get_settings()
    if action == "login":
        limit = settings.auth_login_attempts_per_window
    elif action == "register":
        limit = settings.auth_register_attempts_per_window
    else:
        limit = settings.auth_telegram_attempts_per_window
    # An IP-wide bucket prevents attackers from bypassing hashing protection by
    # cycling account names. A second account-wide bucket limits distributed
    # attempts against one identity.
    auth_rate_limiter.check(
        f"{action}:ip:{client_ip}",
        limit=limit,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    normalized_identity = identity.strip().lower()
    if normalized_identity:
        auth_rate_limiter.check(
            f"{action}:identity:{normalized_identity}",
            limit=limit,
            window_seconds=settings.auth_rate_limit_window_seconds,
        )

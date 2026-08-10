from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Enforce request limits from both Content-Length and streamed chunks."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_bytes: int,
        path_limits: list[tuple[str, str, int]] | None = None,
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.path_limits = path_limits or []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.max_bytes
        path = str(scope.get("path") or "")
        for prefix, suffix, path_limit in self.path_limits:
            if path.startswith(prefix) and path.endswith(suffix):
                limit = path_limit
                break

        headers = Headers(scope=scope)
        raw_length = headers.get("content-length")
        if raw_length:
            try:
                if int(raw_length) > limit:
                    await self._send_too_large(send)
                    return
            except ValueError:
                # Let the HTTP server/framework handle a malformed header.
                pass

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._send_too_large(send)

    @staticmethod
    async def _send_too_large(send: Send) -> None:
        body = b'{"detail":"Request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool) -> None:
        self.app = app
        self.production = production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
                            b"object-src 'none'; img-src 'self' data: blob:; media-src 'self' blob:; "
                            b"connect-src 'self' ws: wss:; style-src 'self' 'unsafe-inline'; "
                            b"script-src 'self'",
                        ),
                    ]
                )
                if self.production:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)

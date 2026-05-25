import hmac
import json
from typing import Any


class BearerAuthMiddleware:
    """ASGI middleware enforcing a static bearer token on HTTP/WebSocket scopes."""

    def __init__(self, app: Any, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            # Pass lifespan and other non-HTTP events through untouched.
            await self._app(scope, receive, send)
            return
        auth = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                auth = value
                break
        if not hmac.compare_digest(auth, self._expected):
            await self._send_401(send)
            return
        await self._app(scope, receive, send)

    @staticmethod
    async def _send_401(send: Any) -> None:
        """Send a 401 Unauthorized JSON response and close the ASGI response."""
        body = json.dumps({"error": "unauthorized"}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

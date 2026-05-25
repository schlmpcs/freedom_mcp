import hmac
import json

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from middleware import BearerAuthMiddleware

TOKEN = "correct-token-xyz789"


def _dummy(request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


_base_app = Starlette(routes=[Route("/mcp", _dummy, methods=["GET", "POST"])])
_wrapped = BearerAuthMiddleware(_base_app, TOKEN)
client = TestClient(_wrapped, raise_server_exceptions=False)


def test_missing_authorization_header_returns_401():
    response = client.get("/mcp")
    assert response.status_code == 401


def test_wrong_token_returns_401():
    response = client.get("/mcp", headers={"Authorization": "Bearer wrong-token"})
    assert response.status_code == 401


def test_empty_bearer_returns_401():
    response = client.get("/mcp", headers={"Authorization": "Bearer "})
    assert response.status_code == 401


def test_correct_token_passes_through():
    response = client.get("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.text == "ok"


def test_401_body_is_json():
    response = client.get("/mcp")
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"error": "unauthorized"}


def test_post_correct_token_passes_through():
    response = client.post("/mcp", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200


def test_duplicate_authorization_uses_first():
    """First Authorization header wins; second is ignored."""
    response = client.get(
        "/mcp",
        headers=[
            ("Authorization", f"Bearer {TOKEN}"),
            ("Authorization", "Bearer wrong-token"),
        ],
    )
    assert response.status_code == 200

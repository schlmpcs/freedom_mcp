"""The pre-trade notification gate must block live order submission (fail-closed)."""

import json
from types import SimpleNamespace

import pytest

import freedom24_mcp

# FastMCP wraps the tool; reach the underlying function.
_place_order = getattr(freedom24_mcp.place_order, "fn", freedom24_mcp.place_order)


@pytest.fixture
def live_config(monkeypatch):
    """A non-dry-run config so place_order reaches the submission path."""
    cfg = SimpleNamespace(dry_run=False, order_timeout=10.0,
                          telegram_bot_token=None, telegram_chat_id=None)
    monkeypatch.setattr(freedom24_mcp, "CONFIG", cfg)
    return cfg


@pytest.fixture
def spy_client(monkeypatch):
    """Replace the broker client; record whether an order was actually sent."""
    calls = {"submitted": False, "session_opened": False}

    def call(cmd, params, timeout=None):
        calls["submitted"] = True
        return {"order_id": "PLACEHOLDER"}

    client = SimpleNamespace(
        call=call,
        open_trading_session=lambda: calls.__setitem__("session_opened", True),
    )
    monkeypatch.setattr(freedom24_mcp, "CLIENT", client)
    return calls


def test_order_blocked_when_notification_fails(live_config, spy_client, monkeypatch):
    # Notification fails (telegram unconfigured) -> fail-closed.
    monkeypatch.setattr(freedom24_mcp, "notify_order", lambda cfg, preview: (False, "Telegram not configured"))
    out = json.loads(_place_order("AAPL.US", "buy", 10, price=250.0, confirm=True))
    assert out["status"] == "not_sent"
    assert out["reason"] == "pre-trade notification failed"
    assert spy_client["submitted"] is False  # never reached the broker


def test_order_proceeds_after_successful_notification(live_config, spy_client, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        freedom24_mcp, "notify_order",
        lambda cfg, preview: captured.update(preview=preview) or (True, "sent"),
    )
    out = json.loads(_place_order("AAPL.US", "buy", 10, price=250.0, confirm=True))
    assert spy_client["submitted"] is True
    assert spy_client["session_opened"] is True
    # The notification carried the real order details.
    assert captured["preview"]["ticker"] == "AAPL.US"
    assert captured["preview"]["quantity"] == 10


def test_notify_skip_bypasses_gate(live_config, spy_client, monkeypatch):
    notified = {"called": False}
    monkeypatch.setattr(freedom24_mcp, "notify_order",
                        lambda cfg, preview: notified.__setitem__("called", True) or (True, "sent"))
    out = json.loads(_place_order("AAPL.US", "buy", 10, price=250.0, confirm=True, notify_skip=True))
    assert spy_client["submitted"] is True
    assert notified["called"] is False  # notification skipped entirely


def test_confirm_false_never_notifies_or_submits(live_config, spy_client, monkeypatch):
    notified = {"called": False}
    monkeypatch.setattr(freedom24_mcp, "notify_order",
                        lambda cfg, preview: notified.__setitem__("called", True) or (True, "sent"))
    out = json.loads(_place_order("AAPL.US", "buy", 10, price=250.0, confirm=False))
    assert out["status"] == "not_sent"
    assert notified["called"] is False
    assert spy_client["submitted"] is False

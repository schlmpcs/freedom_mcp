"""Tests for the pre-trade Telegram notifier (placeholder creds only)."""

from types import SimpleNamespace

import requests

from freedom24_core import notify


def _cfg(token="123:PLACEHOLDER", chat_id=999):
    return SimpleNamespace(telegram_bot_token=token, telegram_chat_id=chat_id)


PREVIEW = {
    "action": "buy",
    "order_type": "limit",
    "ticker": "AAPL.US",
    "quantity": 10,
    "price": 250.0,
}


def test_build_notification_includes_full_details():
    msg = notify.build_order_notification(PREVIEW)
    assert "BUY" in msg
    assert "AAPL.US" in msg
    assert "10" in msg
    assert "limit" in msg
    assert "250.0" in msg


def test_build_notification_market_price():
    msg = notify.build_order_notification({**PREVIEW, "order_type": "market", "price": None})
    assert "market" in msg


def test_send_telegram_unconfigured_fails_closed():
    ok, detail = notify.send_telegram(_cfg(token=None), "hi")
    assert ok is False and "not configured" in detail.lower()

    ok, detail = notify.send_telegram(_cfg(chat_id=None), "hi")
    assert ok is False and "not configured" in detail.lower()


def test_send_telegram_success(monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr(notify.requests, "post", fake_post)
    ok, detail = notify.send_telegram(_cfg(), "hello")
    assert ok is True and detail == "sent"
    # Posts to the Bot API with the configured chat id; token is in the URL path.
    assert captured["url"].endswith("/sendMessage")
    assert "/bot123:PLACEHOLDER/" in captured["url"]
    assert captured["json"] == {"chat_id": 999, "text": "hello"}


def test_send_telegram_non_200_fails(monkeypatch):
    monkeypatch.setattr(
        notify.requests, "post",
        lambda url, json, timeout: SimpleNamespace(status_code=403, text="forbidden"),
    )
    ok, detail = notify.send_telegram(_cfg(), "hello")
    assert ok is False and "403" in detail


def test_send_telegram_request_exception_fails(monkeypatch):
    def boom(url, json, timeout):
        raise requests.RequestException("network down")

    monkeypatch.setattr(notify.requests, "post", boom)
    ok, detail = notify.send_telegram(_cfg(), "hello")
    assert ok is False and "error" in detail.lower()


def test_notify_order_builds_and_sends(monkeypatch):
    sent = {}
    monkeypatch.setattr(notify, "send_telegram", lambda cfg, text: sent.update(text=text) or (True, "sent"))
    ok, _ = notify.notify_order(_cfg(), PREVIEW)
    assert ok is True
    assert "AAPL.US" in sent["text"] and "BUY" in sent["text"]

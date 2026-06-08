"""Market-open gate: skip off-hours cycles instead of failing open 24/7.

The agent trades US (.US) tickers, so the gate must reflect the US cash market
(Freedom24 code ``FIX``) and not unrelated always-on markets (e.g. crypto).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from agent import tools


# --- documented getMarketStatus shape (docs/freedom24-docs/docs/market-status.md) ---
def _status_payload(fix_status: str, extra: list[dict] | None = None) -> dict:
    markets = [{"n": "NYSE/NASDAQ", "n2": "FIX", "s": fix_status, "o": "09:30:00"}]
    if extra:
        markets.extend(extra)
    return {"result": {"markets": {"t": "2026-06-08 12:00:00", "m": markets}}}


def test_status_open_when_fix_open():
    assert tools._market_open_from_status(_status_payload("OPEN"), {"FIX"}) is True


def test_status_closed_when_fix_close():
    assert tools._market_open_from_status(_status_payload("CLOSE"), {"FIX"}) is False


def test_crypto_open_does_not_keep_us_gate_open():
    # An always-on crypto market is present and OPEN, but FIX is CLOSE -> closed.
    payload = _status_payload("CLOSE", extra=[{"n2": "CRPT", "s": "OPEN"}])
    assert tools._market_open_from_status(payload, {"FIX"}) is False


def test_unrecognised_shape_returns_none():
    assert tools._market_open_from_status({"weird": 1}, {"FIX"}) is None
    assert tools._market_open_from_status({"result": {"markets": {"m": []}}}, {"FIX"}) is None


def test_fix_absent_returns_none():
    # No FIX row at all -> can't resolve -> None (caller uses time fallback).
    payload = {"result": {"markets": {"m": [{"n2": "CRPT", "s": "OPEN"}]}}}
    assert tools._market_open_from_status(payload, {"FIX"}) is None


def test_unwrapped_and_bare_list_shapes():
    assert tools._market_open_from_status({"m": [{"n2": "FIX", "s": "OPEN"}]}, {"FIX"}) is True
    assert tools._market_open_from_status([{"n2": "FIX", "s": "CLOSE"}], {"FIX"}) is False


# --- US session time fallback ---
def test_session_open_during_regular_hours():
    # 2026-06-08 is a Monday; 14:00 UTC = 10:00 EDT -> open.
    assert tools._us_session_open(datetime(2026, 6, 8, 14, 0, tzinfo=timezone.utc)) is True


def test_session_closed_overnight():
    # 05:23 UTC (the user's report time) = 01:23 EDT -> closed.
    assert tools._us_session_open(datetime(2026, 6, 8, 5, 23, tzinfo=timezone.utc)) is False


def test_session_closed_on_weekend():
    # 2026-06-06 is a Saturday, even at midday UTC.
    assert tools._us_session_open(datetime(2026, 6, 6, 14, 0, tzinfo=timezone.utc)) is False


def test_session_boundaries_half_open():
    # Opens at 13:30 UTC (09:30 EDT), closes at 20:00 UTC (16:00 EDT).
    assert tools._us_session_open(datetime(2026, 6, 8, 13, 30, tzinfo=timezone.utc)) is True
    assert tools._us_session_open(datetime(2026, 6, 8, 20, 0, tzinfo=timezone.utc)) is False


# --- end-to-end get_market_status ---
class _FakeClient:
    def __init__(self, payload):
        self._payload = payload

    def call(self, command, params, timeout=None):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_get_market_status_uses_authoritative_status():
    result = asyncio.run(tools.get_market_status(_FakeClient(_status_payload("CLOSE"))))
    assert result["any_open"] is False
    assert result["source"] == "market-status"


def test_get_market_status_falls_back_to_time_when_shape_unknown(monkeypatch):
    monkeypatch.setattr(tools, "_us_session_open", lambda now: True)
    result = asyncio.run(tools.get_market_status(_FakeClient({"unexpected": "shape"})))
    assert result["any_open"] is True
    assert result["source"] == "time-fallback"


def test_get_market_status_falls_back_to_time_on_error(monkeypatch):
    monkeypatch.setattr(tools, "_us_session_open", lambda now: False)
    result = asyncio.run(tools.get_market_status(_FakeClient(RuntimeError("boom"))))
    assert result["any_open"] is False
    assert result["source"] == "time-fallback"
    assert "boom" in result["error"]

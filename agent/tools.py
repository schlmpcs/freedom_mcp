"""Agent-facing tools that wrap the synchronous ``freedom24_core`` client.

These are plain async Python functions the agent calls directly (NOT MCP tools).
Each broker call is dispatched through :func:`asyncio.to_thread` so the
blocking, ``requests``-based :class:`~freedom24_core.client.TradernetClient`
does not stall the event loop. The parameter shapes mirror those used by the
MCP server in ``freedom24_mcp.py`` so behaviour stays consistent.

All functions are defensive: a failed broker call for one ticker is captured as
an ``{"error": ...}`` entry rather than aborting the whole observation.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta
from typing import Any

from freedom24_core import COMMANDS, TradernetClient
from freedom24_core.client import TradernetError

from agent.portfolio_state import PaperPortfolio

# Candle interval (in minutes) and lookback window, matching freedom24_mcp.py.
_DAILY_INTERVAL_MINUTES = 1440


def _from_date_for_daily(count: int) -> str:
    """Start date covering roughly ``count`` daily bars (with padding)."""
    days_back = max(count, 1) + 5
    return (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")


async def _call(client: TradernetClient, command_key: str, params: dict[str, Any]) -> Any:
    """Run one blocking broker command off the event loop."""
    return await asyncio.to_thread(client.call, COMMANDS[command_key], params)


async def observe_market(
    client: TradernetClient, tickers: list[str], candle_count: int = 20
) -> dict:
    """Fetch quote + daily candles + news for each ticker.

    Returns ``{ticker: {"quote": ..., "candles": ..., "news": ...}}``. Any
    per-field failure is recorded under that field as ``{"error": "..."}`` so a
    single bad symbol never sinks the whole cycle.
    """
    observations: dict[str, dict] = {}
    for ticker in tickers:
        entry: dict[str, Any] = {}

        try:
            entry["quote"] = await _call(client, "quote", {"tickers": ticker})
        except (TradernetError, Exception) as exc:  # noqa: BLE001
            entry["quote"] = {"error": str(exc)}

        try:
            entry["candles"] = await _call(
                client,
                "candles",
                {
                    "ticker": ticker,
                    "interval": _DAILY_INTERVAL_MINUTES,
                    "from": _from_date_for_daily(candle_count),
                    "to": datetime.utcnow().strftime("%Y-%m-%d"),
                    "count": candle_count,
                },
            )
        except (TradernetError, Exception) as exc:  # noqa: BLE001
            entry["candles"] = {"error": str(exc)}

        try:
            entry["news"] = await _call(client, "news", {"ticker": ticker})
        except (TradernetError, Exception) as exc:  # noqa: BLE001
            entry["news"] = {"error": str(exc)}

        observations[ticker] = entry
    return observations


async def get_portfolio_snapshot(client: TradernetClient) -> dict:
    """Return the *real* broker portfolio so the agent can compare to paper."""
    try:
        data = await _call(client, "portfolio", {})
        return {"portfolio": data}
    except (TradernetError, Exception) as exc:  # noqa: BLE001
        return {"error": str(exc)}


async def get_market_status(client: TradernetClient) -> dict:
    """Return market/exchange open status with a derived ``any_open`` flag.

    The raw Tradernet shape varies by account, so ``any_open`` is detected
    best-effort by scanning the response for common "open" indicators. If the
    shape is unrecognised, ``any_open`` defaults to ``True`` (fail-open) so the
    agent still gets a chance to reason rather than silently skipping forever.
    """
    try:
        data = await _call(client, "market_status", {})
    except (TradernetError, Exception) as exc:  # noqa: BLE001
        return {"any_open": False, "error": str(exc)}

    return {"any_open": _detect_any_open(data), "raw": data}


def _detect_any_open(data: Any) -> bool:
    """Best-effort scan for any open market in a market-status response."""
    open_markers = {"open", "opened", "trading", "regular", "1", "true"}

    def walk(node: Any) -> bool:
        if isinstance(node, dict):
            for key, value in node.items():
                key_l = str(key).lower()
                if key_l in ("open", "is_open", "isopen", "opened") and _truthy(value):
                    return True
                if key_l in ("status", "state", "session", "phase") and isinstance(value, str):
                    if value.strip().lower() in open_markers:
                        return True
                if walk(value):
                    return True
        elif isinstance(node, list):
            return any(walk(item) for item in node)
        return False

    found = walk(data)
    if found:
        return True
    # Unrecognised but non-empty payload: fail open.
    return bool(data)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "open", "opened", "trading"}
    return False


async def execute_paper_order(
    portfolio: PaperPortfolio,
    ticker: str,
    action: str,
    quantity: float,
    price: float,
    reason: str,
) -> dict:
    """Execute a buy/sell against the in-memory paper portfolio.

    Returns the fill confirmation (or rejection) from the portfolio. No real
    broker order is ever placed.
    """
    action_l = (action or "").lower().strip()
    if action_l == "buy":
        return portfolio.buy(ticker, quantity, price, reason)
    if action_l == "sell":
        return portfolio.sell(ticker, quantity, price, reason)
    return {"status": "rejected", "reason": f"unsupported paper action: {action!r}"}

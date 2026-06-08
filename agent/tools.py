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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

try:  # DST-correct US session fallback; system tzdata is present on Linux servers.
    from zoneinfo import ZoneInfo

    _NY_TZ: Optional["ZoneInfo"] = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - missing tzdata: fall back to a fixed UTC window
    _NY_TZ = None

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


# Freedom24 market code for US cash equities (NYSE/NASDAQ); see docs market-status.md.
_US_EQUITY_MARKETS = {"FIX"}
_OPEN_STATUS_VALUES = {"open", "opened", "trading", "regular"}
_CLOSED_STATUS_VALUES = {"close", "closed", "halt", "halted", "suspended", "pre", "post"}


async def get_market_status(client: TradernetClient) -> dict:
    """Return market status with a derived ``any_open`` flag for US equities.

    The agent trades ``.US`` tickers, so ``any_open`` reflects the US cash market
    (NYSE/NASDAQ = Freedom24 market code ``FIX``) — *not* unrelated markets such
    as crypto that trade around the clock and would otherwise keep the gate
    permanently open. Resolution order:

    1. **Authoritative:** the ``getMarketStatus`` payload's ``FIX`` row status
       (``result.markets.m[].s``).
    2. **Fallback** (shape unrecognised or the call failed): a US regular-session
       heuristic (Mon-Fri, 09:30-16:00 America/New_York). This makes the agent
       *skip* overnight/weekend cycles instead of failing open and burning a
       model call every interval.
    """
    now = datetime.now(timezone.utc)
    try:
        data = await _call(client, "market_status", {})
    except (TradernetError, Exception) as exc:  # noqa: BLE001
        return {"any_open": _us_session_open(now), "source": "time-fallback", "error": str(exc)}

    status_open = _market_open_from_status(data, _US_EQUITY_MARKETS)
    if status_open is None:
        return {"any_open": _us_session_open(now), "source": "time-fallback", "raw": data}
    return {"any_open": status_open, "source": "market-status", "raw": data}


def _market_status_rows(data: Any) -> list[dict]:
    """Extract the per-market rows from a ``getMarketStatus`` payload.

    Documented shape: ``{"result": {"markets": {"m": [ {row}, ... ]}}}``. Also
    tolerates an already-unwrapped ``{"m": [...]}`` or a bare list of rows.
    """
    node = data
    if isinstance(node, dict) and isinstance(node.get("result"), dict):
        node = node["result"]
    if isinstance(node, dict) and isinstance(node.get("markets"), dict):
        rows = node["markets"].get("m")
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    if isinstance(node, dict) and isinstance(node.get("m"), list):
        return [r for r in node["m"] if isinstance(r, dict)]
    if isinstance(node, list):
        return [r for r in node if isinstance(r, dict)]
    return []


def _market_open_from_status(data: Any, market_codes: set[str]) -> Optional[bool]:
    """Resolve whether any of ``market_codes`` is open from a status payload.

    Returns ``True``/``False`` when at least one matching market row carries a
    recognised status, else ``None`` so the caller falls back to a heuristic.
    """
    rows = _market_status_rows(data)
    if not rows:
        return None
    wanted = {c.upper() for c in market_codes}
    relevant = [r for r in rows if str(r.get("n2") or r.get("n") or "").upper() in wanted]
    if not relevant:
        return None

    resolved = False
    for row in relevant:
        status = str(row.get("s") or "").strip().lower()
        if status in _OPEN_STATUS_VALUES:
            return True  # any matching market open -> open
        if status in _CLOSED_STATUS_VALUES:
            resolved = True
    return False if resolved else None


def _us_session_open(now: datetime) -> bool:
    """US regular cash-session heuristic: Mon-Fri, 09:30-16:00 America/New_York.

    A timezone-only approximation (ignores US market holidays / half-days), used
    only as a fallback when the broker's market-status shape is unavailable. Uses
    ``zoneinfo`` for DST correctness, degrading to a fixed 13:30-20:00 UTC window
    (EDT) when tzdata is missing.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if _NY_TZ is not None:
        local = now.astimezone(_NY_TZ)
        if local.weekday() >= 5:
            return False
        minutes = local.hour * 60 + local.minute
        return 9 * 60 + 30 <= minutes < 16 * 60
    # tzdata missing: approximate with the summer (EDT) UTC offset.
    utc = now.astimezone(timezone.utc)
    if utc.weekday() >= 5:
        return False
    minutes = utc.hour * 60 + utc.minute
    return 13 * 60 + 30 <= minutes < 20 * 60


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

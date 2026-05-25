"""Scheduled reports: pre-market heads-up and daily snapshot."""

from __future__ import annotations

import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo

from freedom24_core import COMMANDS

from .formatting import (
    extract_portfolio, extract_quote, format_market_status, format_orders,
    format_portfolio, fmt_num, fmt_signed_pct,
)
from .scheduling import is_market_weekday

logger = logging.getLogger("freedom24_mcp")


def render_premarket(market: dict, positions_payload: dict,
                     quotes_by_ticker: dict, orders_payload: dict) -> str:
    """Compose the pre-market heads-up message (pure)."""
    lines = ["🌅 Pre-market heads-up"]
    market_line = format_market_status(market)
    if market_line:
        lines.append(market_line)
    positions, _ = extract_portfolio(positions_payload)
    if positions:
        lines.append("\nHoldings overnight:")
        for p in positions:
            ticker = p.get("i", "?")
            q = extract_quote(quotes_by_ticker.get(ticker, {}))
            move = fmt_signed_pct(q.get("pcp")) if q else "-"
            last = fmt_num(q.get("ltp")) if q else fmt_num(p.get("mkt_price"))
            lines.append(f"• {ticker}  {last}  ({move})")
    lines.append("\n" + format_orders(orders_payload))
    return "\n".join(lines)


async def _call(client, key, params=None):
    """Run a synchronous client call off the event loop."""
    return await asyncio.to_thread(client.call, COMMANDS[key], params or {})


async def fetch_and_render_snapshot(client) -> str:
    """Fetch the portfolio and render the daily snapshot message."""
    payload = await _call(client, "portfolio", {})
    return "📈 Daily snapshot\n" + format_portfolio(payload)


async def fetch_and_render_premarket(client) -> str:
    """Fetch market status, holdings, their quotes, and open orders."""
    market = await _call(client, "market_status", {})
    positions_payload = await _call(client, "portfolio", {})
    positions, _ = extract_portfolio(positions_payload)
    quotes: dict = {}
    for p in positions:
        ticker = p.get("i")
        if ticker:
            quotes[ticker] = await _call(client, "quote", {"tickers": ticker})
    orders = await _call(client, "active_orders", {"active_only": 1})
    return render_premarket(market, positions_payload, quotes, orders)


async def daily_snapshot_job(context) -> None:
    """JobQueue callback: send the daily snapshot (skips weekends)."""
    config = context.bot_data["config"]
    now = datetime.datetime.now(ZoneInfo(config.bot_snapshot_tz))
    if not is_market_weekday(now):
        return
    try:
        text = await fetch_and_render_snapshot(context.bot_data["client"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("daily snapshot failed: %s", exc)
        return
    await context.bot.send_message(chat_id=config.telegram_chat_id, text=text)


async def premarket_job(context) -> None:
    """JobQueue callback: send the pre-market heads-up (skips weekends)."""
    config = context.bot_data["config"]
    now = datetime.datetime.now(ZoneInfo(config.bot_premarket_tz))
    if not is_market_weekday(now):
        return
    try:
        text = await fetch_and_render_premarket(context.bot_data["client"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("premarket report failed: %s", exc)
        return
    await context.bot.send_message(chat_id=config.telegram_chat_id, text=text)

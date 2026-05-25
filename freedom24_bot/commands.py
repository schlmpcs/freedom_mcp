"""Read-only slash-command handlers. Thin wrappers over formatting + the client."""

from __future__ import annotations

import asyncio
import logging

from freedom24_core import COMMANDS

from .formatting import format_alerts_list, format_orders, format_portfolio, format_quote
from .reports import fetch_and_render_snapshot

logger = logging.getLogger("freedom24_mcp")

HELP_TEXT = (
    "Freedom24 bot — commands:\n"
    "/portfolio — positions, P&L, cash\n"
    "/quote TICKER — current quote (e.g. /quote AAPL.US)\n"
    "/orders — active orders\n"
    "/alerts — armed price alerts\n"
    "/report — daily snapshot now\n"
    "/status — service health\n"
    "/help — this message"
)


async def _call(client, key, params=None):
    return await asyncio.to_thread(client.call, COMMANDS[key], params or {})


async def _reply_safe(update, coro_text_fn):
    """Run a fetch+format, replying with the text or a clean error string."""
    try:
        text = await coro_text_fn()
    except Exception as exc:  # noqa: BLE001 - surface as a chat message
        logger.warning("command failed: %s", exc)
        text = f"Error: {exc}"
    await update.message.reply_text(text)


async def cmd_help(update, context) -> None:
    await update.message.reply_text(HELP_TEXT)


async def cmd_portfolio(update, context) -> None:
    client = context.bot_data["client"]
    await _reply_safe(update, lambda: _fmt(client, "portfolio", {}, format_portfolio))


async def cmd_orders(update, context) -> None:
    client = context.bot_data["client"]
    await _reply_safe(update, lambda: _fmt(client, "active_orders", {"active_only": 1}, format_orders))


async def cmd_alerts(update, context) -> None:
    client = context.bot_data["client"]
    await _reply_safe(update, lambda: _fmt(client, "alerts", {}, format_alerts_list))


async def cmd_report(update, context) -> None:
    client = context.bot_data["client"]
    await _reply_safe(update, lambda: fetch_and_render_snapshot(client))


async def cmd_quote(update, context) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /quote TICKER  (e.g. /quote AAPL.US)")
        return
    ticker = context.args[0].upper()
    client = context.bot_data["client"]
    await _reply_safe(update, lambda: _fmt(client, "quote", {"tickers": ticker}, format_quote))


async def cmd_status(update, context) -> None:
    client = context.bot_data["client"]
    mode = getattr(client, "mode", None) or "unknown"
    await update.message.reply_text(f"OK. Auth mode: {mode}. Use /help for commands.")


async def _fmt(client, key, params, formatter):
    """Fetch a command result and run it through a formatter."""
    payload = await _call(client, key, params)
    return formatter(payload)

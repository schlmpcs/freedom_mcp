import asyncio
from unittest.mock import AsyncMock, MagicMock

from freedom24_bot.commands import cmd_portfolio, cmd_quote, cmd_help


def _update_ctx(client, args=None):
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    ctx = MagicMock()
    ctx.bot_data = {"client": client, "config": MagicMock()}
    ctx.args = args or []
    return update, ctx


def test_cmd_help_lists_commands():
    update, ctx = _update_ctx(MagicMock())
    asyncio.run(cmd_help(update, ctx))
    text = update.message.reply_text.await_args.args[0]
    assert "/portfolio" in text and "/quote" in text


def test_cmd_portfolio_replies_with_formatted_positions():
    client = MagicMock()
    client.call.return_value = {"result": {"ps": {"acc": [], "pos": [
        {"i": "AAPL.US", "q": 100, "mkt_price": 250.3, "market_value": 25030.0, "profit_price": 283.0},
    ]}}}
    update, ctx = _update_ctx(client)
    asyncio.run(cmd_portfolio(update, ctx))
    assert "AAPL.US" in update.message.reply_text.await_args.args[0]


def test_cmd_quote_requires_ticker_arg():
    update, ctx = _update_ctx(MagicMock(), args=[])
    asyncio.run(cmd_quote(update, ctx))
    assert "Usage" in update.message.reply_text.await_args.args[0]


def test_cmd_quote_replies_with_quote():
    client = MagicMock()
    client.call.return_value = {"result": {"q": [{"c": "AAPL.US", "ltp": 250.3, "pcp": 2.16}]}}
    update, ctx = _update_ctx(client, args=["AAPL.US"])
    asyncio.run(cmd_quote(update, ctx))
    assert "AAPL.US" in update.message.reply_text.await_args.args[0]

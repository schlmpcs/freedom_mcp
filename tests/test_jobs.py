import asyncio
from unittest.mock import AsyncMock, MagicMock

from freedom24_bot.alerts import poll_alerts_job
from freedom24_bot.reports import daily_snapshot_job


def _ctx(client, chat_id=123, state_path="state.json"):
    cfg = MagicMock(
        telegram_chat_id=chat_id,
        bot_state_path=state_path,
        bot_snapshot_tz="UTC",
        bot_premarket_tz="UTC",
    )
    ctx = MagicMock()
    ctx.bot_data = {"client": client, "config": cfg}
    ctx.bot.send_message = AsyncMock()
    return ctx


def test_poll_job_sends_message_on_new_fire(tmp_path):
    client = MagicMock()
    client.call.return_value = {"alerts": [
        {"id": 76, "ticker": "AAPL.US", "trigger_type": "crossing",
         "trigger_price": '{"price":"250"}', "triggered": "1"},
    ]}
    ctx = _ctx(client, state_path=str(tmp_path / "s.json"))
    asyncio.run(poll_alerts_job(ctx))
    ctx.bot.send_message.assert_awaited_once()
    assert "AAPL.US" in ctx.bot.send_message.await_args.kwargs["text"]


def test_poll_job_silent_when_no_fire(tmp_path):
    client = MagicMock()
    client.call.return_value = {"alerts": [{"id": 76, "triggered": "0"}]}
    ctx = _ctx(client, state_path=str(tmp_path / "s.json"))
    asyncio.run(poll_alerts_job(ctx))
    ctx.bot.send_message.assert_not_awaited()


def test_daily_snapshot_job_sends(tmp_path, monkeypatch):
    # Force a weekday so the test is deterministic regardless of the real date.
    monkeypatch.setattr("freedom24_bot.reports.is_market_weekday", lambda now: True)
    client = MagicMock()
    client.call.return_value = {"result": {"ps": {"acc": [], "pos": [
        {"i": "AAPL.US", "q": 100, "mkt_price": 250.3, "market_value": 25030.0, "profit_price": 283.0},
    ]}}}
    ctx = _ctx(client)
    asyncio.run(daily_snapshot_job(ctx))
    ctx.bot.send_message.assert_awaited_once()
    assert "AAPL.US" in ctx.bot.send_message.await_args.kwargs["text"]


def test_daily_snapshot_job_skips_weekend(tmp_path, monkeypatch):
    # On a weekend the job returns without sending.
    monkeypatch.setattr("freedom24_bot.reports.is_market_weekday", lambda now: False)
    client = MagicMock()
    ctx = _ctx(client)
    asyncio.run(daily_snapshot_job(ctx))
    ctx.bot.send_message.assert_not_awaited()

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from telegram.error import Conflict

from freedom24_bot.__main__ import build_application, log_error, register_jobs
from freedom24_core.config import Config


def _config():
    return Config(pub_key="PUB", priv_key="SECRET",
                  telegram_bot_token="123:ABC", telegram_chat_id=123)


def test_build_application_registers_command_handlers():
    app = build_application(_config(), client=MagicMock())
    # Handlers live in group 0; expect our seven commands registered.
    registered = app.handlers.get(0, [])
    assert len(registered) >= 7


def test_build_application_stores_client_and_config_in_bot_data():
    client = MagicMock()
    app = build_application(_config(), client=client)
    assert app.bot_data["client"] is client
    assert app.bot_data["config"].telegram_chat_id == 123
    assert app.error_handlers


def test_log_error_summarizes_polling_conflict(monkeypatch):
    error_log = MagicMock()
    exception_log = MagicMock()
    monkeypatch.setattr("freedom24_bot.__main__.logger.error", error_log)
    monkeypatch.setattr("freedom24_bot.__main__.logger.exception", exception_log)

    ctx = SimpleNamespace(error=Conflict("terminated by other getUpdates request"))
    asyncio.run(log_error(update=None, context=ctx))

    error_log.assert_called_once()
    assert "another process is calling getUpdates" in error_log.call_args.args[0]
    exception_log.assert_not_called()


def test_register_jobs_schedules_poll_and_two_reports():
    jq = MagicMock()
    register_jobs(jq, _config())
    assert jq.run_repeating.call_count == 1
    assert jq.run_daily.call_count == 2

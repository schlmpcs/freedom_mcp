from unittest.mock import MagicMock

from freedom24_bot.__main__ import build_application, register_jobs
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


def test_register_jobs_schedules_poll_and_two_reports():
    jq = MagicMock()
    register_jobs(jq, _config())
    assert jq.run_repeating.call_count == 1
    assert jq.run_daily.call_count == 2

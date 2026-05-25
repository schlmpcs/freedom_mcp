from freedom24_core.config import load_config


def test_bot_poll_seconds_default(monkeypatch):
    monkeypatch.delenv("BOT_ALERT_POLL_SECONDS", raising=False)
    assert load_config().bot_alert_poll_seconds == 60


def test_bot_poll_seconds_from_env(monkeypatch):
    monkeypatch.setenv("BOT_ALERT_POLL_SECONDS", "30")
    assert load_config().bot_alert_poll_seconds == 30


def test_bot_poll_seconds_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("BOT_ALERT_POLL_SECONDS", "soon")
    assert load_config().bot_alert_poll_seconds == 60


def test_telegram_chat_id_parsed_int(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    assert load_config().telegram_chat_id == 123456789


def test_telegram_chat_id_none_when_absent(monkeypatch):
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert load_config().telegram_chat_id is None


def test_report_time_defaults(monkeypatch):
    for var in ("BOT_SNAPSHOT_TIME", "BOT_SNAPSHOT_TZ", "BOT_PREMARKET_TIME", "BOT_PREMARKET_TZ"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.bot_snapshot_time == "08:00"
    assert cfg.bot_snapshot_tz == "Asia/Karachi"
    assert cfg.bot_premarket_time == "08:30"
    assert cfg.bot_premarket_tz == "America/New_York"

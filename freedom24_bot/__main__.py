"""Entry point: build the PTB Application, wire handlers + jobs, run polling."""

from __future__ import annotations

import logging
import sys

from telegram.ext import Application, CommandHandler

from freedom24_core import TradernetClient, load_config, setup_logging
from freedom24_core.config import Config

from . import commands
from .alerts import poll_alerts_job
from .reports import daily_snapshot_job, premarket_job
from .scheduling import parse_time_in_tz
from .security import build_chat_filter

logger = logging.getLogger("freedom24_mcp")

_COMMANDS = [
    ("portfolio", commands.cmd_portfolio),
    ("quote", commands.cmd_quote),
    ("orders", commands.cmd_orders),
    ("alerts", commands.cmd_alerts),
    ("report", commands.cmd_report),
    ("status", commands.cmd_status),
    ("help", commands.cmd_help),
    ("start", commands.cmd_help),
]


def build_application(config: Config, client=None) -> Application:
    """Construct the Application, register handlers, stash shared state."""
    app = Application.builder().token(config.telegram_bot_token).build()
    app.bot_data["client"] = client if client is not None else TradernetClient(config)
    app.bot_data["config"] = config
    chat_filter = build_chat_filter(config.telegram_chat_id)
    for name, handler in _COMMANDS:
        app.add_handler(CommandHandler(name, handler, filters=chat_filter))
    return app


def register_jobs(job_queue, config: Config) -> None:
    """Schedule the alert poll and the two daily reports."""
    job_queue.run_repeating(poll_alerts_job, interval=config.bot_alert_poll_seconds,
                            first=10, name="alert_poll")
    job_queue.run_daily(premarket_job,
                        time=parse_time_in_tz(config.bot_premarket_time, config.bot_premarket_tz),
                        name="premarket")
    job_queue.run_daily(daily_snapshot_job,
                        time=parse_time_in_tz(config.bot_snapshot_time, config.bot_snapshot_tz),
                        name="daily_snapshot")


def main() -> None:
    setup_logging()
    config = load_config()
    if not config.telegram_bot_token or not config.telegram_chat_id:
        logger.error("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required.")
        sys.exit(1)
    app = build_application(config)
    register_jobs(app.job_queue, config)
    logger.info("freedom24-bot starting (long-polling)")
    app.run_polling()


if __name__ == "__main__":
    main()

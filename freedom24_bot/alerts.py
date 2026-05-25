"""Alert relay: detect newly-fired broker alerts and poll the API on a timer."""

from __future__ import annotations

import asyncio
import logging

from freedom24_core import COMMANDS

from .formatting import extract_alerts, format_alert_fire
from .state import load_seen, save_seen

logger = logging.getLogger("freedom24_mcp")

_FALSEY = {"", "0", "false", "none", "null"}


def is_triggered(alert: dict) -> bool:
    """True if the alert's `triggered` field indicates it has fired."""
    value = alert.get("triggered")
    return str(value).strip().lower() not in _FALSEY if value is not None else False


def detect_new_fires(alerts: list[dict], seen: set[int]) -> tuple[list[str], set[int]]:
    """Compare the current alert list against previously-relayed IDs.

    Returns (messages_to_send, next_seen_set). `next_seen_set` is exactly the set
    of currently-fired IDs, so an alert that resets drops out and can re-fire.
    """
    messages: list[str] = []
    fired_now: set[int] = set()
    for alert in alerts:
        if "error" in alert or "id" not in alert:
            continue
        if is_triggered(alert):
            aid = int(alert["id"])
            fired_now.add(aid)
            if aid not in seen:
                messages.append(format_alert_fire(alert))
    return messages, fired_now


async def poll_alerts_job(context) -> None:
    """JobQueue callback: poll getAlertsList, relay any new fires to Telegram."""
    client = context.bot_data["client"]
    config = context.bot_data["config"]
    try:
        payload = await asyncio.to_thread(client.call, COMMANDS["alerts"], {})
    except Exception as exc:  # noqa: BLE001 - log and skip this tick
        logger.warning("alert poll failed: %s", exc)
        return
    seen = load_seen(config.bot_state_path)
    messages, next_seen = detect_new_fires(extract_alerts(payload), seen)
    for text in messages:
        await context.bot.send_message(chat_id=config.telegram_chat_id, text=text)
    if next_seen != seen:
        save_seen(config.bot_state_path, next_seen)

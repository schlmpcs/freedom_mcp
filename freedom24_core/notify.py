"""Pre-trade notifications via Telegram.

This is a *standalone* sender (a plain HTTPS POST to the Telegram Bot API) so it
can be called from inside the synchronous MCP ``place_order`` flow — unlike
``freedom24_bot``, which sends through a long-running ``python-telegram-bot``
process. It reuses the same ``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``
credentials already loaded into :class:`~freedom24_core.config.Config`.

Safety stance: the order path is **fail-closed**. ``notify_order`` returns
``(False, reason)`` when Telegram is not configured or the send fails, and the
caller (``place_order``) must then refuse to submit the order. The user asked to
always be notified before a trade, so "couldn't notify" means "don't trade".

Secrets: the bot token is never logged or echoed into the returned message.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

import requests

logger = logging.getLogger("freedom24_mcp")

# Short timeout: a hung notification must not stall an order indefinitely. A
# timeout is treated as a failed notification (fail-closed).
_NOTIFY_TIMEOUT = 10.0


def build_order_notification(preview: dict) -> str:
    """Render a human-readable pre-trade alert from a ``place_order`` preview.

    ``preview`` carries the order fields assembled in ``place_order``:
    ``action``, ``order_type``, ``ticker``, ``quantity``, ``price``.
    """
    action = str(preview.get("action", "?")).upper()
    order_type = str(preview.get("order_type", "?"))
    ticker = preview.get("ticker", "?")
    qty = preview.get("quantity", "?")
    price = preview.get("price")
    price_line = f"{price}" if price is not None else "market"

    return (
        "⚠️ FREEDOM24 ORDER — about to submit\n"
        f"Action: {action}\n"
        f"Ticker: {ticker}\n"
        f"Quantity: {qty}\n"
        f"Order type: {order_type}\n"
        f"Price: {price_line}\n"
        "This order is being sent to the live broker now."
    )


def send_telegram(config: Any, text: str) -> Tuple[bool, str]:
    """POST ``text`` to the configured Telegram chat. Never raises.

    Returns ``(ok, detail)``. ``detail`` is a short, secret-free reason on
    failure (suitable for surfacing back through the MCP tool result).
    """
    token: Optional[str] = getattr(config, "telegram_bot_token", None)
    chat_id = getattr(config, "telegram_chat_id", None)
    if not token or chat_id is None:
        return False, "Telegram not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=_NOTIFY_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("pre-trade Telegram notification failed: %s", exc)
        return False, f"Telegram request error: {exc}"

    if resp.status_code != 200:
        # Telegram error bodies do not contain our token; safe to surface briefly.
        logger.warning("pre-trade Telegram notification HTTP %s", resp.status_code)
        return False, f"Telegram HTTP {resp.status_code}: {resp.text[:200]}"

    return True, "sent"


def notify_order(config: Any, preview: dict) -> Tuple[bool, str]:
    """Build and send the pre-trade alert for an order ``preview``."""
    return send_telegram(config, build_order_notification(preview))

"""Configuration loader for the Freedom24 / Tradernet MCP server.

Reads credentials and settings from the environment (a local ``.env`` file is
loaded automatically) and exposes a typed :class:`Config`. Secrets are never
logged from here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv

# Load .env from the current working directory (if present). Real environment
# variables always take precedence over .env values.
load_dotenv(override=False)

DEFAULT_API_URL = "https://freedom24.com/api"
DEFAULT_TIMEOUT = 15.0
# Order endpoints (putTradeOrder/delTradeOrder) respond noticeably slower than
# read commands; a short timeout makes the tool report a false failure while the
# order actually lands — dangerous, as a retry would duplicate a live order.
DEFAULT_ORDER_TIMEOUT = 60.0


def _clean(value: Optional[str]) -> Optional[str]:
    """Return a stripped string, or None if empty/missing."""
    if value is None:
        return None
    value = value.strip()
    return value or None


def _as_bool(value: Optional[str]) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    """Resolved server configuration. Mutable so tools can override at runtime."""

    login: Optional[str] = None
    password: Optional[str] = None
    pub_key: Optional[str] = None
    priv_key: Optional[str] = None
    rsa_private_key: Optional[str] = None
    api_url: str = DEFAULT_API_URL
    timeout: float = DEFAULT_TIMEOUT
    order_timeout: float = DEFAULT_ORDER_TIMEOUT
    dry_run: bool = False

    @property
    def has_api_key(self) -> bool:
        return bool(self.pub_key and self.priv_key)

    @property
    def has_login(self) -> bool:
        return bool(self.login and self.password)

    @property
    def has_any_auth(self) -> bool:
        return self.has_api_key or self.has_login


def load_config() -> Config:
    """Build a :class:`Config` from environment variables / ``.env``."""
    timeout_raw = _clean(os.getenv("FREEDOM24_TIMEOUT"))
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT

    order_timeout_raw = _clean(os.getenv("FREEDOM24_ORDER_TIMEOUT"))
    try:
        order_timeout = float(order_timeout_raw) if order_timeout_raw else DEFAULT_ORDER_TIMEOUT
    except ValueError:
        order_timeout = DEFAULT_ORDER_TIMEOUT

    return Config(
        login=_clean(os.getenv("FREEDOM24_LOGIN")),
        password=_clean(os.getenv("FREEDOM24_PASSWORD")),
        pub_key=_clean(os.getenv("FREEDOM24_PUB_KEY")),
        priv_key=_clean(os.getenv("FREEDOM24_PRIV_KEY")),
        rsa_private_key=_clean(os.getenv("FREEDOM24_RSA_PRIVATE_KEY")),
        api_url=_clean(os.getenv("FREEDOM24_API_URL")) or DEFAULT_API_URL,
        timeout=timeout,
        order_timeout=order_timeout,
        dry_run=_as_bool(os.getenv("FREEDOM24_DRY_RUN")),
    )

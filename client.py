"""HTTP client wrapper for the Tradernet / Freedom24 REST API.

`TradernetClient` holds the session in memory, signs requests (API-key mode) or
attaches a ``sid`` (login mode), enforces a timeout, logs every call to stderr
(with secrets redacted), and re-logs-in automatically when a session expires.

All command names live in the central :data:`COMMANDS` map so a name that
differs on your account is a one-line fix.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import requests

from auth import build_signed_request
from config import Config

logger = logging.getLogger("freedom24_mcp")

# ---------------------------------------------------------------------------
# Central command map.
#
# Core commands (quotes, candles, security info, orders, reports) are grounded
# in the public Tradernet/Freedom24 clients. Commands marked "verify" may differ
# by API version/account; if a call returns an "unknown command" style error,
# adjust the value here. See README "Command names" section.
# ---------------------------------------------------------------------------
COMMANDS: dict[str, str] = {
    "login": "getSID",                # verify: login -> sid
    "user_info": "getUserInfo",
    "portfolio": "getPositionJson",
    "cashflows": "getUserCashFlows",
    "quote": "getStockData",
    "candles": "getQuotesHistory",
    "search": "tickerFinder",         # verify
    "ticker_info": "getSecurityInfo",
    "news": "getNewsList",            # verify
    "top": "getTopSecurities",        # verify
    "active_orders": "getOrders",     # verify
    "orders_history": "getOrdersHistory",
    "place_order": "putTradeOrder",
    "cancel_order": "delTradeOrder",
    "market_status": "getMarketStatus",  # verify
    "alerts": "getAlertsList",        # verify
    "add_alert": "addAlert",          # verify
    "delete_alert": "delAlert",       # verify
    "broker_report": "getBrokerReport",
    "trades_history": "getTradesHistory",  # verify
}

_SENSITIVE_KEYS = {
    "password",
    "sid",
    "apiKey",
    "api_key",
    "pub_key",
    "priv_key",
    "private_key",
    "sig",
}

# Substrings that indicate a session/auth failure worth retrying after re-login.
_SESSION_ERROR_HINTS = ("session", "sid", "not logged", "auth", "expired", "login")


class TradernetError(Exception):
    """Raised for transport failures and API-reported errors."""


def _redact(params: Any) -> Any:
    if not isinstance(params, dict):
        return params
    return {k: ("***" if k in _SENSITIVE_KEYS else v) for k, v in params.items()}


class TradernetClient:
    """Stateful client for one brokerage session."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._login = config.login
        self._password = config.password
        self._pub_key = config.pub_key
        self._priv_key = config.priv_key
        self._sid: Optional[str] = None
        self._mode: Optional[str] = None  # "apikey" | "login"
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "freedom24-mcp/1.0"})

    # -- introspection ------------------------------------------------------
    @property
    def mode(self) -> Optional[str]:
        return self._mode

    @property
    def has_session(self) -> bool:
        return self._mode == "apikey" or bool(self._sid)

    # -- credential management ---------------------------------------------
    def set_api_key(self, pub_key: str, private_key: str) -> None:
        """Switch to API-key auth using the provided keys."""
        self._pub_key = pub_key
        self._priv_key = private_key
        self._mode = "apikey"
        self._sid = None

    def ensure_auth(self) -> None:
        """Pick an auth mode and acquire a session if needed."""
        if self._mode == "apikey" and self._pub_key and self._priv_key:
            return
        if self._mode == "login" and self._sid:
            return
        if self._pub_key and self._priv_key:
            self._mode = "apikey"
            logger.info("Auth mode: API key (HMAC-SHA256)")
            return
        if self._login and self._password:
            self.login(self._login, self._password)
            return
        raise TradernetError(
            "No credentials configured. Set FREEDOM24_PUB_KEY + FREEDOM24_PRIV_KEY "
            "(recommended) or FREEDOM24_LOGIN + FREEDOM24_PASSWORD, or call the "
            "login / login_api_key tools."
        )

    def login(self, login: str, password: str) -> dict:
        """Username/password login. Stores the returned ``sid`` in memory."""
        self._login = login
        self._password = password
        resp = self._post_session(
            COMMANDS["login"], {"login": login, "password": password}, with_sid=False
        )
        sid = (
            resp.get("SID")
            or resp.get("sid")
            or (resp.get("session") or {}).get("SID")
            if isinstance(resp, dict)
            else None
        )
        if not sid:
            raise TradernetError(
                "Login succeeded transport-wise but no session id was returned. "
                f"Response keys: {list(resp.keys()) if isinstance(resp, dict) else type(resp)}. "
                "Your account may require API-key auth instead of login/password."
            )
        self._sid = sid
        self._mode = "login"
        logger.info("Logged in via login/password; session id acquired")
        return {"status": "ok", "mode": "login"}

    # -- request dispatch ---------------------------------------------------
    def call(self, cmd: str, params: dict | None = None) -> Any:
        """Execute an authenticated API command and return parsed JSON."""
        self.ensure_auth()
        if self._mode == "apikey":
            return self._post_signed(cmd, params or {})
        return self._post_session(cmd, params or {}, with_sid=True)

    def _post_signed(self, cmd: str, params: dict) -> Any:
        body, headers = build_signed_request(self._pub_key, self._priv_key, cmd, params)
        url = f"{self.config.api_url.rstrip('/')}/v2/cmd/{cmd}"
        logger.info("POST %s cmd=%s params=%s", url, cmd, _redact(params))
        try:
            resp = self._session.post(
                url, data=body, headers=headers, timeout=self.config.timeout
            )
        except requests.Timeout as exc:
            raise TradernetError(f"{cmd}: request timed out after {self.config.timeout}s") from exc
        except requests.RequestException as exc:
            raise TradernetError(f"{cmd}: network error: {exc}") from exc
        return self._handle(resp, cmd)

    def _post_session(
        self,
        cmd: str,
        params: dict,
        with_sid: bool = True,
        _retried: bool = False,
    ) -> Any:
        payload_params = dict(params or {})
        if with_sid:
            if not self._sid:
                if not (self._login and self._password):
                    raise TradernetError("No session id and no login/password to acquire one.")
                self.login(self._login, self._password)
            payload_params["sid"] = self._sid
        body = {"cmd": cmd, "params": payload_params}
        url = self.config.api_url
        logger.info("POST %s cmd=%s params=%s", url, cmd, _redact(payload_params))
        try:
            resp = self._session.post(url, json=body, timeout=self.config.timeout)
        except requests.Timeout as exc:
            raise TradernetError(f"{cmd}: request timed out after {self.config.timeout}s") from exc
        except requests.RequestException as exc:
            raise TradernetError(f"{cmd}: network error: {exc}") from exc

        try:
            return self._handle(resp, cmd)
        except TradernetError as err:
            # Retry once on a likely session-expiry error.
            if with_sid and not _retried and self._is_session_error(str(err)):
                logger.info("Session looks expired; re-logging in and retrying %s", cmd)
                self._sid = None
                self.login(self._login, self._password)
                return self._post_session(cmd, params, with_sid=True, _retried=True)
            raise

    # -- response handling --------------------------------------------------
    def _handle(self, resp: requests.Response, cmd: str) -> Any:
        if resp.status_code >= 500:
            raise TradernetError(f"{cmd}: server error HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            data = resp.json()
        except ValueError:
            if resp.status_code >= 400:
                raise TradernetError(f"{cmd}: HTTP {resp.status_code}: {resp.text[:300]}")
            raise TradernetError(f"{cmd}: response was not JSON: {resp.text[:300]}")

        error = self._extract_error(data)
        if error:
            raise TradernetError(f"{cmd}: API error: {error}")
        return data

    @staticmethod
    def _extract_error(data: Any) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        for key in ("error", "errMsg", "err_msg", "error_msg"):
            value = data.get(key)
            if value:
                return {"message": value, "code": data.get("code") or data.get("errCode")}
        # Some endpoints wrap status in result: {"result": {"code": <nonzero>, "msg": ...}}
        result = data.get("result")
        if isinstance(result, dict):
            code = result.get("code")
            msg = result.get("msg") or result.get("message")
            if msg and code not in (None, 0, "0"):
                return {"message": msg, "code": code}
        return None

    @staticmethod
    def _is_session_error(message: str) -> bool:
        lowered = message.lower()
        return any(hint in lowered for hint in _SESSION_ERROR_HINTS)

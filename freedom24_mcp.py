"""Freedom24 / Tradernet MCP server.

Exposes brokerage operations (portfolio, quotes, candles, orders, alerts,
reports) as MCP tools over stdio so Claude Code can drive a Freedom24 account.

Run directly:  python freedom24_mcp.py
Logs go to stderr; stdout is reserved for the MCP protocol.
"""

from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

from freedom24_core import COMMANDS, TradernetClient, TradernetError, load_config, setup_logging

# --- logging: stderr only (stdout is the MCP transport) --------------------
setup_logging()
logger = logging.getLogger("freedom24_mcp")

CONFIG = load_config()
CLIENT = TradernetClient(CONFIG)
mcp = FastMCP("freedom24")


# --- helpers ----------------------------------------------------------------
def _result(data: Any) -> str:
    """Serialize an API response to a clean JSON string."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _error(tool: str, exc: Exception) -> str:
    """Serialize an error into a clear JSON string and log it to stderr."""
    logger.error("%s failed: %s", tool, exc)
    return json.dumps({"error": str(exc), "tool": tool}, ensure_ascii=False, indent=2)


def _call(tool: str, command_key: str, params: Optional[dict] = None) -> str:
    """Run a command from the COMMANDS map and return JSON, catching errors."""
    try:
        return _result(CLIENT.call(COMMANDS[command_key], params or {}))
    except Exception as exc:  # noqa: BLE001 - surface any failure as clean JSON
        return _error(tool, exc)


# Buy/sell and order-type mappings for putTradeOrder.
_ACTION_IDS = {"buy": 1, "buy_margin": 2, "sell": 3, "sell_short": 4}
_ORDER_TYPE_IDS = {"market": 1, "limit": 2, "stop": 3, "stop_limit": 4}

# Candle interval -> minutes (D/W/M expressed in minutes; verify per account).
_INTERVAL_MINUTES = {
    "1": 1,
    "5": 5,
    "15": 15,
    "60": 60,
    "D": 1440,
    "W": 10080,
    "M": 43200,
}


def _from_date_for_candles(interval: str, count: int) -> str:
    """Estimate a start date that should cover `count` bars of `interval`."""
    today = datetime.utcnow()
    if interval in ("D", "W", "M"):
        per_bar_days = {"D": 1, "W": 7, "M": 30}[interval]
        days_back = per_bar_days * max(count, 1) + 5
    else:
        minutes = _INTERVAL_MINUTES.get(interval, 1)
        # ~390 trading minutes per US session; pad for weekends/holidays.
        days_back = max(1, math.ceil(count * minutes / 390) + 3)
    return (today - timedelta(days=days_back)).strftime("%Y-%m-%d")


# ===========================================================================
# Auth
# ===========================================================================
@mcp.tool()
def login(login: str, password: str) -> str:
    """Authenticate with a Freedom24 username and password.

    Establishes a session and stores the session id in memory for subsequent
    calls. Prefer login_api_key when you have API keys. Returns a status object;
    the session id itself is never echoed.
    """
    try:
        CLIENT.login(login, password)
        return _result({"status": "ok", "mode": "login"})
    except Exception as exc:  # noqa: BLE001
        return _error("login", exc)


@mcp.tool()
def login_api_key(pub_key: str, private_key: str) -> str:
    """Authenticate with a Freedom24 API key pair (recommended).

    `pub_key` is the public/API key and `private_key` is the secret used to sign
    requests with HMAC-SHA256. Verifies the keys by fetching basic user info.
    """
    try:
        CLIENT.set_api_key(pub_key, private_key)
        info = CLIENT.call(COMMANDS["user_info"], {})
        return _result({"status": "ok", "mode": "apikey", "user_info": info})
    except Exception as exc:  # noqa: BLE001
        return _error("login_api_key", exc)


@mcp.tool()
def get_session_info() -> str:
    """Check whether the current brokerage session is valid.

    Reports the active auth mode and fetches basic account info to confirm the
    session/keys still work. Use this to verify connectivity before trading.
    """
    try:
        CLIENT.ensure_auth()
        info = CLIENT.call(COMMANDS["user_info"], {})
        return _result({"authenticated": True, "mode": CLIENT.mode, "user_info": info})
    except Exception as exc:  # noqa: BLE001
        return _error("get_session_info", exc)


# ===========================================================================
# Portfolio
# ===========================================================================
@mcp.tool()
def get_portfolio() -> str:
    """Return all open positions, cash balances, and profit/loss for the account.

    Use this to answer questions about current holdings, account value, buying
    power, or unrealized/realized P&L.
    """
    return _call("get_portfolio", "portfolio", {})


@mcp.tool()
def get_cashflows(limit: int = 200, skip: int = 0, without_refund: bool = False) -> str:
    """Return the account's cash-flow ledger: deposits, withdrawals, fees, dividends.

    Each entry has a `type_code` (e.g. deposit, withdrawal, commission_for_trades),
    `sum`, `currency`, `date`, and `comment`. `limit` caps the number of rows
    (most recent first), `skip` offsets for paging, and `without_refund` excludes
    refund entries. Use this for cash reconciliation and to total net deposits.
    """
    params: dict[str, Any] = {"take": limit, "skip": skip}
    if without_refund:
        params["without_refund"] = 1
    return _call("get_cashflows", "cashflows", params)


# ===========================================================================
# Quotes & instruments
# ===========================================================================
@mcp.tool()
def get_quote(ticker: str) -> str:
    """Return the current quote for `ticker`: last price, bid/ask, and volume.

    Use the Freedom24/Tradernet symbol form, e.g. "AAPL.US" or "SBER.RU".
    """
    return _call("get_quote", "quote", {"tickers": ticker})


@mcp.tool()
def get_candles(ticker: str, interval: str = "D", count: int = 100) -> str:
    """Return OHLCV candle history for `ticker`.

    `interval` is one of 1, 5, 15, 60 (minutes) or D, W, M (day/week/month).
    `count` is the approximate number of most-recent bars to retrieve. A start
    date covering that many bars is computed automatically.
    """
    interval = str(interval).upper() if str(interval).isalpha() else str(interval)
    if interval not in _INTERVAL_MINUTES:
        return _error(
            "get_candles",
            ValueError(f"Unsupported interval '{interval}'. Use one of: {', '.join(_INTERVAL_MINUTES)}"),
        )
    params = {
        "ticker": ticker,
        "interval": _INTERVAL_MINUTES[interval],
        "from": _from_date_for_candles(interval, count),
        "to": datetime.utcnow().strftime("%Y-%m-%d"),
        "count": count,
    }
    return _call("get_candles", "candles", params)


@mcp.tool()
def search_ticker(query: str) -> str:
    """Search for instruments by name or partial symbol.

    Returns matching tickers with their exchange suffixes; use the result with
    other tools (get_quote, place_order, etc.).
    """
    return _call("search_ticker", "search", {"text": query})


@mcp.tool()
def get_ticker_info(ticker: str) -> str:
    """Return full instrument details for `ticker`.

    Includes exchange, currency, lot size, trading hours, and other static
    metadata for the security.
    """
    return _call("get_ticker_info", "ticker_info", {"ticker": ticker, "sup": True})


@mcp.tool()
def get_news(ticker: str) -> str:
    """Return the latest news headlines for `ticker`."""
    return _call("get_news", "news", {"ticker": ticker})


@mcp.tool()
def get_top_securities(market: str = "US") -> str:
    """Return the most-traded instruments for a `market` (e.g. "US", "EU", "RU").

    Useful for discovering active names and gauging market interest.
    """
    return _call("get_top_securities", "top", {"market": market})


# ===========================================================================
# Orders
# ===========================================================================
@mcp.tool()
def get_active_orders() -> str:
    """Return all currently open (unfilled / partially filled) orders."""
    return _call("get_active_orders", "active_orders", {"active_only": 1})


@mcp.tool()
def get_orders_history(days: int = 30) -> str:
    """Return past orders (filled, cancelled, rejected) over the last `days`."""
    today = datetime.utcnow()
    params = {
        # getOrdersHistory wants ISO-8601 `from`/`till` (NOT date_from/date_to).
        "from": (today - timedelta(days=max(days, 1))).strftime("%Y-%m-%dT00:00:00"),
        "till": today.strftime("%Y-%m-%dT23:59:59"),
    }
    return _call("get_orders_history", "orders_history", params)


@mcp.tool()
def place_order(
    ticker: str,
    action: str,
    quantity: float,
    price: Optional[float] = None,
    order_type: str = "limit",
    confirm: bool = False,
) -> str:
    """Place a buy or sell order. THIS SPENDS REAL MONEY.

    Args:
        ticker: instrument symbol, e.g. "AAPL.US".
        action: "buy" or "sell" (also "buy_margin"/"sell_short").
        quantity: number of shares/units (must be > 0).
        price: limit price; required for "limit" and "stop_limit" orders,
            ignored for "market".
        order_type: "market", "limit", "stop", or "stop_limit".
        confirm: SAFETY GATE. The order is NOT sent unless this is True.

    Returns the broker's order acknowledgement, or an explanatory error. Always
    show the user the order details and get explicit approval before setting
    confirm=True.
    """
    try:
        action_key = action.lower().strip()
        type_key = order_type.lower().strip()
        if action_key not in _ACTION_IDS:
            raise ValueError(f"Invalid action '{action}'. Use one of: {', '.join(_ACTION_IDS)}")
        if type_key not in _ORDER_TYPE_IDS:
            raise ValueError(f"Invalid order_type '{order_type}'. Use one of: {', '.join(_ORDER_TYPE_IDS)}")
        if quantity <= 0:
            raise ValueError("quantity must be greater than 0")
        if type_key in ("limit", "stop_limit") and price is None:
            raise ValueError(f"price is required for a {type_key} order")

        params: dict[str, Any] = {
            "instr_name": ticker,
            "action_id": _ACTION_IDS[action_key],
            "order_type_id": _ORDER_TYPE_IDS[type_key],
            "qty": quantity,
            "expiration_id": 1,  # Day order
            "aon": 0,            # allow partial fills
            "submit_ch_c": 1,    # online channel
            "message_id": 0,
            "replace_order_id": 0,
        }
        if type_key in ("limit", "stop_limit"):
            params["limit_price"] = price
        if type_key in ("stop", "stop_limit"):
            params["stop_price"] = price

        preview = {
            "action": action_key,
            "order_type": type_key,
            "ticker": ticker,
            "quantity": quantity,
            "price": price,
        }

        if CONFIG.dry_run:
            logger.info("DRY_RUN active; not sending order: %s", preview)
            return _result({"status": "dry_run", "would_send": preview, "params": params})

        if not confirm:
            return _result(
                {
                    "status": "not_sent",
                    "reason": "confirm flag was not set to true",
                    "order_preview": preview,
                    "next_step": "Re-call place_order with confirm=true to actually submit.",
                }
            )

        logger.info("Submitting order: %s", preview)
        CLIENT.open_trading_session()  # required before putTradeOrder
        return _result(CLIENT.call(COMMANDS["place_order"], params, timeout=CONFIG.order_timeout))
    except Exception as exc:  # noqa: BLE001
        return _error("place_order", exc)


@mcp.tool()
def cancel_order(order_id: str, confirm: bool = False) -> str:
    """Cancel an open order by its id. Requires confirm=True to execute.

    Returns the broker's cancellation acknowledgement, or an explanatory error.
    """
    try:
        if CONFIG.dry_run:
            logger.info("DRY_RUN active; not cancelling order %s", order_id)
            return _result({"status": "dry_run", "would_cancel": order_id})
        if not confirm:
            return _result(
                {
                    "status": "not_sent",
                    "reason": "confirm flag was not set to true",
                    "order_id": order_id,
                    "next_step": "Re-call cancel_order with confirm=true to actually cancel.",
                }
            )
        CLIENT.open_trading_session()  # required before delTradeOrder
        return _result(
            CLIENT.call(COMMANDS["cancel_order"], {"order_id": order_id}, timeout=CONFIG.order_timeout)
        )
    except Exception as exc:  # noqa: BLE001
        return _error("cancel_order", exc)


# ===========================================================================
# Market
# ===========================================================================
@mcp.tool()
def get_market_status() -> str:
    """Return which exchanges/markets are currently open, with session times."""
    return _call("get_market_status", "market_status", {})


@mcp.tool()
def get_alerts() -> str:
    """Return all configured price alerts for the account."""
    return _call("get_alerts", "alerts", {})


@mcp.tool()
def add_alert(ticker: str, price: float, direction: str = "above") -> str:
    """Create a price alert for `ticker` that triggers at `price`.

    `direction` is "above" or "below" — whether to fire when the price rises
    above or falls below the threshold.
    """
    direction_key = direction.lower().strip()
    if direction_key not in ("above", "below"):
        return _error("add_alert", ValueError("direction must be 'above' or 'below'"))
    params = {"ticker": ticker, "price": price, "type": direction_key}
    return _call("add_alert", "add_alert", params)


@mcp.tool()
def delete_alert(alert_id: str) -> str:
    """Delete a previously created price alert by its id."""
    return _call("delete_alert", "delete_alert", {"id": alert_id})


# ===========================================================================
# Reports
# ===========================================================================
@mcp.tool()
def get_broker_report(from_date: str, to_date: str, report_type: Optional[str] = None) -> str:
    """Return the broker report between `from_date` and `to_date` (YYYY-MM-DD).

    The report covers account balances, trades, commissions, corporate actions,
    and cash/securities movements for the period. `report_type` narrows the
    response to one data block; useful values:
      - "in_outs"     — deposits & withdrawals of funds
      - "cash_flows"  — all money movements
      - "trades"      — trades in the period
      - "commissions" — fees charged
      - "account_at_start" / "account_at_end" — balances at period bounds
    Omit `report_type` for the full report.
    """
    params: dict[str, Any] = {
        "date_start": from_date,
        "date_end": to_date,
        "time_period": "23:59:59",  # required end-of-day cut (API error 109 if missing)
        "format": "json",
    }
    if report_type:
        params["type"] = report_type
    return _call("get_broker_report", "broker_report", params)


@mcp.tool()
def get_trades_history(ticker: Optional[str] = None, days: int = 30, max_trades: int = 0) -> str:
    """Return executed trades over the last `days`, optionally filtered by `ticker`.

    Unlike orders, these are actual fills with execution price and time. Each
    trade includes a `profit` field (realized P&L). `max_trades` caps the count;
    0 (the default) returns ALL trades in the window — use it to total realized
    P&L since inception by passing a large `days`.
    """
    today = datetime.utcnow()
    params: dict[str, Any] = {
        "beginDate": (today - timedelta(days=max(days, 1))).strftime("%Y-%m-%d"),
        "endDate": today.strftime("%Y-%m-%d"),
        "max": max_trades,
    }
    if ticker:
        params["nt_ticker"] = ticker
    return _call("get_trades_history", "trades_history", params)


# ===========================================================================
# Startup
# ===========================================================================
def _log_startup() -> None:
    """Log the configured auth mode, tool inventory, and a sample call to stderr."""
    if CONFIG.has_api_key:
        configured = "API key (HMAC-SHA256)"
    elif CONFIG.has_login:
        configured = "login/password"
    else:
        configured = "NONE -- set credentials in .env before calling tools"

    logger.info("Freedom24 MCP server starting")
    logger.info("API URL: %s | timeout: %ss | dry_run: %s", CONFIG.api_url, CONFIG.timeout, CONFIG.dry_run)
    logger.info("Auth configured: %s", configured)
    logger.info("Command map: %s", json.dumps(COMMANDS))
    logger.info(
        "Sample call shape -> get_quote('AAPL.US') => POST %s cmd=%s params={'tickers':'AAPL.US'}",
        CONFIG.api_url,
        COMMANDS["quote"],
    )
    logger.info(
        "Registered tools: login, login_api_key, get_session_info, get_portfolio, "
        "get_cashflows, get_quote, get_candles, search_ticker, get_ticker_info, get_news, "
        "get_top_securities, get_active_orders, get_orders_history, place_order, cancel_order, "
        "get_market_status, get_alerts, add_alert, delete_alert, get_broker_report, get_trades_history"
    )


if __name__ == "__main__":
    _log_startup()
    transport = CONFIG.mcp_transport
    if transport == "streamable-http":
        if not CONFIG.mcp_bearer_token:
            logger.error(
                "MCP_BEARER_TOKEN must be set when MCP_TRANSPORT=streamable-http. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
            sys.exit(1)
        import uvicorn
        from middleware import BearerAuthMiddleware
        http_app = BearerAuthMiddleware(mcp.streamable_http_app(), CONFIG.mcp_bearer_token)
        logger.info(
            "Starting MCP server (streamable-http) on %s:%s",
            CONFIG.mcp_host,
            CONFIG.mcp_port,
        )
        uvicorn.run(http_app, host=CONFIG.mcp_host, port=CONFIG.mcp_port, log_config=None)
    else:
        mcp.run()  # stdio transport

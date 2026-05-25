"""Pure render/extract helpers. No I/O — take parsed API payloads, return strings."""

from __future__ import annotations

import json
from typing import Any


def fmt_num(value: Any, decimals: int = 2) -> str:
    """Format a number tolerantly: handles None, and comma-decimal strings."""
    if value is None or value == "":
        return "-"
    try:
        num = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return str(value)
    return f"{num:.{decimals}f}"


def fmt_signed_pct(value: Any) -> str:
    """Format a percentage with an explicit sign, e.g. '+2.16%'."""
    if value is None or value == "":
        return "-"
    try:
        num = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return str(value)
    return f"{num:+.2f}%"


def extract_quote(payload: dict) -> dict:
    """Pull the first quote row from a getStockQuotesJson payload, or {}."""
    if not isinstance(payload, dict):
        return {}
    rows = (payload.get("result") or {}).get("q") if isinstance(payload.get("result"), dict) else None
    if rows is None:
        rows = payload.get("q")
    if isinstance(rows, list) and rows:
        return rows[0]
    return {}


def format_quote(payload: dict) -> str:
    """One-line quote summary: ticker, last, change %, bid/ask."""
    q = extract_quote(payload)
    if not q:
        return "No quote data."
    return (
        f"{q.get('c', '?')} {q.get('name', '')}\n"
        f"Last {fmt_num(q.get('ltp'))}  ({fmt_signed_pct(q.get('pcp'))})\n"
        f"Bid {fmt_num(q.get('bbp'))} / Ask {fmt_num(q.get('bap'))}  "
        f"Vol {fmt_num(q.get('vol'), 0)}"
    ).strip()


def extract_portfolio(payload: dict) -> tuple[list[dict], list[dict]]:
    """Return (positions, accounts) from a getPositionJson payload, tolerant of nesting."""
    if not isinstance(payload, dict):
        return [], []
    # Try result.ps, then top-level ps, then top-level acc/pos.
    container = None
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("ps"), dict):
        container = result["ps"]
    elif isinstance(payload.get("ps"), dict):
        container = payload["ps"]
    else:
        container = payload
    positions = container.get("pos") if isinstance(container.get("pos"), list) else []
    accounts = container.get("acc") if isinstance(container.get("acc"), list) else []
    return positions, accounts


def format_portfolio(payload: dict) -> str:
    """Multi-line portfolio summary: positions, P&L, and cash balances."""
    positions, accounts = extract_portfolio(payload)
    if not positions and not accounts:
        return "No open positions or cash data."
    lines: list[str] = []
    if positions:
        lines.append("📊 Positions:")
        total_value = 0.0
        for p in positions:
            try:
                total_value += float(str(p.get("market_value", 0)).replace(",", "."))
            except (TypeError, ValueError):
                pass
            lines.append(
                f"• {p.get('i', '?')}  x{fmt_num(p.get('q'), 0)} @ {fmt_num(p.get('mkt_price'))}  "
                f"val {fmt_num(p.get('market_value'))}  P&L {fmt_num(p.get('profit_price'))}"
            )
        lines.append(f"Total position value: {fmt_num(total_value)}")
    if accounts:
        lines.append("💵 Cash:")
        for a in accounts:
            lines.append(f"• {a.get('curr', '?')}: {fmt_num(a.get('s'))}")
    return "\n".join(lines)


def _alert_price(alert: dict) -> str:
    """Extract a display price from the alert's trigger_price (a JSON string)."""
    raw = alert.get("trigger_price")
    if isinstance(raw, dict):
        return fmt_num(raw.get("price"))
    if isinstance(raw, str):
        try:
            return fmt_num(json.loads(raw).get("price"))
        except (ValueError, AttributeError, TypeError):
            return raw
    return fmt_num(raw)


def format_alert_fire(alert: dict) -> str:
    """Telegram message for a single fired alert."""
    return (
        f"🔔 Alert: {alert.get('ticker', '?')} "
        f"{alert.get('trigger_type', '')} {_alert_price(alert)}".strip()
    )


def extract_alerts(payload: dict) -> list[dict]:
    """Pull the alerts array from a getAlertsList payload."""
    if isinstance(payload, dict) and isinstance(payload.get("alerts"), list):
        return payload["alerts"]
    return []


def format_alerts_list(payload: dict) -> str:
    """Human-readable list of currently armed alerts."""
    alerts = [a for a in extract_alerts(payload) if "error" not in a]
    if not alerts:
        return "No alerts armed."
    lines = ["⏰ Armed alerts:"]
    for a in alerts:
        fired = " (triggered)" if str(a.get("triggered") or "") not in ("", "0") else ""
        lines.append(f"• {a.get('ticker', '?')} {a.get('trigger_type', '')} {_alert_price(a)}{fired}")
    return "\n".join(lines)


def extract_orders(payload: dict) -> list[dict]:
    """Pull the orders array from an active-orders payload, tolerant of nesting.

    Real getNotifyOrderJson shape (confirmed by the live spike) is
    ``result.orders.order[]`` — i.e. ``result.orders`` is a dict whose ``order``
    key holds the list. Flatter variants are tolerated as fallbacks.
    """
    if not isinstance(payload, dict):
        return []

    def _from_orders_obj(obj):
        if isinstance(obj, dict) and isinstance(obj.get("order"), list):
            return obj["order"]
        if isinstance(obj, list):
            return obj
        return None

    result = payload.get("result")
    if isinstance(result, dict):
        found = _from_orders_obj(result.get("orders"))
        if found is not None:
            return found
    found = _from_orders_obj(payload.get("orders"))
    if found is not None:
        return found
    return []


def format_orders(payload: dict) -> str:
    """Human-readable list of active orders."""
    orders = extract_orders(payload)
    if not orders:
        return "No active orders."
    lines = ["📂 Active orders:"]
    for o in orders:
        lines.append(
            f"• {o.get('instr', o.get('ticker', '?'))} "
            f"{o.get('oper', o.get('side', ''))} qty {fmt_num(o.get('q', o.get('qty')), 0)} "
            f"@ {fmt_num(o.get('p', o.get('price')))}"
        )
    return "\n".join(lines)

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

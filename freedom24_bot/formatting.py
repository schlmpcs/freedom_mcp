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

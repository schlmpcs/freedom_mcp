"""Normalize Freedom24/Tradernet MCP output into Alpaca-style schemas.

This module is the Phase-1 data adapter between the Freedom24 MCP tools and the
global ``claude-trading-skills`` skills, which were written against the Alpaca
broker API and therefore expect Alpaca-shaped dicts.

Design constraints:
- **stdlib only** — no third-party imports, so it is trivially testable offline.
- **No dependency on ``freedom24_bot``** — ``freedom24_core`` must stay
  self-contained (core never depends on the bot package). The portfolio
  unwrapping logic mirrors ``freedom24_bot.formatting.extract_portfolio`` but is
  reimplemented here rather than imported.
- **Pure functions** — every public function takes already-parsed dict/JSON
  input and returns a plain dict/list. No network, no I/O.

All numeric coercion is tolerant of the comma-decimal strings the Tradernet API
sometimes returns (e.g. ``"147,42"``) as well as plain floats/ints.
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Numeric / coercion helpers
# ---------------------------------------------------------------------------
def _to_float(value: Any, default: float = 0.0) -> float:
    """Coerce a value to float, tolerating None, '' and comma-decimal strings."""
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Ticker normalization
# ---------------------------------------------------------------------------
def normalize_ticker(symbol: str) -> str:
    """Strip the exchange suffix, returning the base symbol the skills use.

    Examples:
        "AAPL.US" -> "AAPL"
        "SBER.RU" -> "SBER"
        "AAPL"    -> "AAPL"   (no dot -> unchanged)

    Option tickers (e.g. "+AAPL.26MAY2026.P287.5") and anything that is not a
    plain ``BASE.SUFFIX`` are returned unchanged.
    """
    if not isinstance(symbol, str) or "." not in symbol:
        return symbol
    base, _, suffix = symbol.rpartition(".")
    # Only treat as an exchange suffix when both halves look like a plain
    # ``BASE.SUFFIX`` pair (no extra dots, e.g. not an option ticker).
    if not base or "." in base:
        return symbol
    return base


def to_freedom24_ticker(symbol: str, default_suffix: str = "US") -> str:
    """Inverse of :func:`normalize_ticker`: add an exchange suffix.

    Examples:
        "AAPL"    -> "AAPL.US"
        "AAPL.US" -> "AAPL.US"   (already suffixed -> unchanged)
        "SBER"    -> "SBER.US"   (with default_suffix="US")
    """
    if not isinstance(symbol, str) or not symbol:
        return symbol
    if "." in symbol:
        return symbol
    return f"{symbol}.{default_suffix}"


# ---------------------------------------------------------------------------
# Portfolio / positions
# ---------------------------------------------------------------------------
def _extract_portfolio_container(portfolio_json: dict) -> tuple[list[dict], list[dict]]:
    """Return (positions, accounts) lists from a getPositionJson payload.

    Tolerant of nesting: tries ``result.ps``, then top-level ``ps``, then the
    payload itself. Reimplements ``freedom24_bot.formatting.extract_portfolio``
    so core stays free of any bot dependency.
    """
    if not isinstance(portfolio_json, dict):
        return [], []
    container: dict | None = None
    result = portfolio_json.get("result")
    if isinstance(result, dict) and isinstance(result.get("ps"), dict):
        container = result["ps"]
    elif isinstance(portfolio_json.get("ps"), dict):
        container = portfolio_json["ps"]
    else:
        container = portfolio_json
    positions = container.get("pos") if isinstance(container.get("pos"), list) else []
    accounts = container.get("acc") if isinstance(container.get("acc"), list) else []
    return positions, accounts


def positions_to_schema(portfolio_json: dict) -> dict:
    """Convert a Freedom24 ``get_portfolio`` response to an Alpaca-style dict.

    Returns::

        {
          "account": {"equity", "cash", "buying_power", "currency"},
          "positions": [
            {"symbol", "broker_symbol", "qty", "avg_entry_price",
             "current_price", "market_value", "unrealized_pl",
             "unrealized_pl_pct", "weight_pct", "currency"}
          ]
        }

    Assumptions (Freedom24's portfolio payload does not expose these directly):
    - ``equity`` = sum(position market_value) + USD cash. (Cash is summed across
      USD-denominated accounts only; the skills are USD-centric. Non-USD cash is
      ignored for equity to avoid mixing currencies without an FX rate.)
    - ``buying_power`` = USD cash. Freedom24 portfolio gives no margin/buying-power
      figure, so we conservatively report settled cash as buying power.
    - ``avg_entry_price`` = (market_value - profit_price) / qty when qty != 0.
      ``profit_price`` is the position's unrealized P&L in account currency, so
      ``market_value - profit_price`` recovers the cost basis. Guarded against
      qty == 0.
    - ``unrealized_pl`` = ``profit_price``.
    - ``unrealized_pl_pct`` = profit_price / cost_basis * 100 (guarded against a
      zero cost basis).
    - ``weight_pct`` = market_value / equity * 100 (guarded against equity == 0).

    Defensive: handles missing keys, empty portfolios, the already-unwrapped
    ``ps`` shape, and the ``{"error": ...}`` shape the MCP tools return on
    failure. On an error payload, returns empty account/positions and surfaces
    the original ``error`` under an ``"error"`` key.
    """
    empty_account = {"equity": 0.0, "cash": 0.0, "buying_power": 0.0, "currency": "USD"}

    # Error passthrough: MCP tools return {"error": "..."} on failure.
    if isinstance(portfolio_json, dict) and "error" in portfolio_json:
        return {"account": dict(empty_account), "positions": [], "error": portfolio_json["error"]}

    positions_raw, accounts_raw = _extract_portfolio_container(portfolio_json)

    # USD cash: sum of `s` across USD accounts (default currency for the skills).
    usd_cash = 0.0
    account_currency = "USD"
    for acc in accounts_raw:
        if not isinstance(acc, dict):
            continue
        curr = str(acc.get("curr") or "USD").upper()
        if curr == "USD":
            usd_cash += _to_float(acc.get("s"))

    total_market_value = 0.0
    parsed_positions: list[dict] = []
    for pos in positions_raw:
        if not isinstance(pos, dict):
            continue
        broker_symbol = str(pos.get("i") or "")
        qty = _to_float(pos.get("q"))
        market_value = _to_float(pos.get("market_value"))
        current_price = _to_float(pos.get("mkt_price"))
        unrealized_pl = _to_float(pos.get("profit_price"))
        currency = str(pos.get("curr") or "USD").upper()

        cost_basis = market_value - unrealized_pl
        avg_entry_price = (cost_basis / qty) if qty else 0.0
        unrealized_pl_pct = (unrealized_pl / cost_basis * 100.0) if cost_basis else 0.0

        total_market_value += market_value
        parsed_positions.append(
            {
                "symbol": normalize_ticker(broker_symbol),
                "broker_symbol": broker_symbol,
                "qty": qty,
                "avg_entry_price": avg_entry_price,
                "current_price": current_price,
                "market_value": market_value,
                "unrealized_pl": unrealized_pl,
                "unrealized_pl_pct": unrealized_pl_pct,
                # weight_pct filled in below once equity is known.
                "weight_pct": 0.0,
                "currency": currency,
            }
        )

    equity = total_market_value + usd_cash
    if equity:
        for p in parsed_positions:
            p["weight_pct"] = p["market_value"] / equity * 100.0

    account = {
        "equity": equity,
        "cash": usd_cash,
        "buying_power": usd_cash,
        "currency": account_currency,
    }
    return {"account": account, "positions": parsed_positions}


# ---------------------------------------------------------------------------
# Candles / OHLCV
# ---------------------------------------------------------------------------
def candles_to_ohlcv(candles_json: dict | list) -> list[dict]:
    """Normalize candle output to a list of OHLCV dicts, oldest -> newest.

    Each output row::

        {"date": <epoch-seconds int or original>, "t": <same>,
         "open": float, "high": float, "low": float, "close": float,
         "volume": float}

    Supported input shapes:
    - **Documented Tradernet HLOC shape** (``getHloc`` / ``getQuotesHistory``)::

          {"hloc": {"<ticker>": [[high, low, open, close], ...]},
           "vl":    {"<ticker>": [vol, ...]},
           "xSeries":{"<ticker>": [epoch_seconds, ...]}}

      Note the per-bar element order is **[high, low, open, close]** (not OHLC).
      May arrive wrapped under ``result``.
    - **Fallback list-of-dicts shape** (degrade gracefully): a bare list (or a
      ``candles``/``bars`` list) of dicts with any of the keys
      ``o/open``, ``h/high``, ``l/low``, ``c/close``, ``v/vol/volume`` and an
      optional ``t``/``date``/``ts`` timestamp.

    # verify against live: the COMMANDS map binds `candles` to `getQuotesHistory`,
    # for which the local docs have no response sample. The documented HLOC shape
    # (`getHloc`) is the closest authoritative reference and is handled first;
    # the list-of-dicts branch is a defensive fallback.
    """
    payload: Any = candles_json
    if isinstance(payload, dict) and isinstance(payload.get("result"), dict):
        # Unwrap a `result` envelope only if it looks like it carries candle data.
        inner = payload["result"]
        if any(k in inner for k in ("hloc", "vl", "xSeries", "candles", "bars")):
            payload = inner

    # ---- Documented HLOC shape -------------------------------------------
    if isinstance(payload, dict) and isinstance(payload.get("hloc"), dict):
        hloc_map = payload["hloc"]
        vl_map = payload.get("vl") if isinstance(payload.get("vl"), dict) else {}
        x_map = payload.get("xSeries") if isinstance(payload.get("xSeries"), dict) else {}
        if not hloc_map:
            return []
        # Use the first (typically only) ticker key.
        ticker = next(iter(hloc_map))
        bars = hloc_map.get(ticker) or []
        vols = (vl_map or {}).get(ticker) or []
        times = (x_map or {}).get(ticker) or []
        rows: list[dict] = []
        for idx, bar in enumerate(bars):
            if not isinstance(bar, (list, tuple)) or len(bar) < 4:
                continue
            high, low, open_, close = bar[0], bar[1], bar[2], bar[3]
            ts = times[idx] if idx < len(times) else None
            vol = vols[idx] if idx < len(vols) else 0
            rows.append(
                {
                    "date": ts,
                    "t": ts,
                    "open": _to_float(open_),
                    "high": _to_float(high),
                    "low": _to_float(low),
                    "close": _to_float(close),
                    "volume": _to_float(vol),
                }
            )
        rows.sort(key=lambda r: (r["t"] is None, r["t"] if r["t"] is not None else 0))
        return rows

    # ---- Fallback: list-of-dicts ----------------------------------------
    list_payload: list | None = None
    if isinstance(payload, list):
        list_payload = payload
    elif isinstance(payload, dict):
        for key in ("candles", "bars", "data"):
            if isinstance(payload.get(key), list):
                list_payload = payload[key]
                break
    if list_payload is None:
        return []

    rows = []
    for bar in list_payload:
        if not isinstance(bar, dict):
            continue
        ts = bar.get("t", bar.get("date", bar.get("ts")))
        rows.append(
            {
                "date": ts,
                "t": ts,
                "open": _to_float(bar.get("open", bar.get("o"))),
                "high": _to_float(bar.get("high", bar.get("h"))),
                "low": _to_float(bar.get("low", bar.get("l"))),
                "close": _to_float(bar.get("close", bar.get("c"))),
                "volume": _to_float(bar.get("volume", bar.get("vol", bar.get("v")))),
            }
        )
    rows.sort(key=lambda r: (r["t"] is None, r["t"] if r["t"] is not None else 0))
    return rows


# ---------------------------------------------------------------------------
# Quote
# ---------------------------------------------------------------------------
def quote_to_schema(quote_json: dict) -> dict:
    """Normalize a Freedom24 ``get_quote`` response to an Alpaca-style quote.

    Returns ``{"symbol", "last", "bid", "ask", "volume"}`` where ``symbol`` is
    the base (suffix-stripped) ticker.

    Input shape (``getStockQuotesJson``): the first row of ``result.q`` (or a
    top-level ``q`` list), with fields ``c`` (ticker), ``ltp`` (last traded
    price), ``bbp`` (best bid), ``bap`` (best ask), ``vol`` (volume). All
    numeric fields tolerate comma-decimal strings.

    Defensive: on the ``{"error": ...}`` MCP failure shape, returns an empty
    quote plus the surfaced ``error`` key. Missing data yields zeros / "".
    """
    if isinstance(quote_json, dict) and "error" in quote_json:
        return {"symbol": "", "last": 0.0, "bid": 0.0, "ask": 0.0, "volume": 0.0,
                "error": quote_json["error"]}

    row: dict = {}
    if isinstance(quote_json, dict):
        result = quote_json.get("result")
        rows = result.get("q") if isinstance(result, dict) else None
        if rows is None:
            rows = quote_json.get("q")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            row = rows[0]
        elif not rows and ("ltp" in quote_json or "c" in quote_json):
            # Already-unwrapped single quote dict.
            row = quote_json

    return {
        "symbol": normalize_ticker(str(row.get("c") or "")),
        "last": _to_float(row.get("ltp")),
        "bid": _to_float(row.get("bbp")),
        "ask": _to_float(row.get("bap")),
        "volume": _to_float(row.get("vol")),
    }


__all__ = [
    "normalize_ticker",
    "to_freedom24_ticker",
    "positions_to_schema",
    "candles_to_ohlcv",
    "quote_to_schema",
]

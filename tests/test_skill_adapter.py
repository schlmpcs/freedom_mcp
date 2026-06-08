"""Offline tests for the Freedom24 -> Alpaca-style skill adapter.

Placeholder data only (AAPL.US / TSLA.US / SBER.RU). No network, no secrets.
"""

from freedom24_core.skill_adapter import (
    candles_to_ohlcv,
    normalize_ticker,
    positions_to_schema,
    quote_to_schema,
    to_freedom24_ticker,
)

# Documented getPositionJson / portfolio shape (confirmed live):
# positions under `pos`, accounts/cash under `acc`, wrapped in `result.ps`.
# Numbers chosen for clean cost-basis math:
#   AAPL: cost basis = 25030 - 283 = 24747 -> avg = 247.47 over 100 shares
#   TSLA: cost basis = 2000 - (-50) = 2050 -> avg = 205.00 over 10 shares
SAMPLE = {"result": {"ps": {
    "acc": [{"curr": "USD", "s": 1500.0}],
    "pos": [
        {"i": "AAPL.US", "name": "Apple Inc.", "q": 100, "mkt_price": 250.3,
         "market_value": 25030.0, "profit_price": 283.0, "curr": "USD"},
        {"i": "TSLA.US", "name": "Tesla", "q": 10, "mkt_price": 200.0,
         "market_value": 2000.0, "profit_price": -50.0, "curr": "USD"},
    ],
}}}


# ---------------------------------------------------------------------------
# Ticker normalization
# ---------------------------------------------------------------------------
def test_normalize_ticker_strips_suffix():
    assert normalize_ticker("AAPL.US") == "AAPL"
    assert normalize_ticker("SBER.RU") == "SBER"


def test_normalize_ticker_no_dot_unchanged():
    assert normalize_ticker("AAPL") == "AAPL"


def test_normalize_ticker_option_unchanged():
    # Option tickers have extra dots -> must not be mangled.
    assert normalize_ticker("+AAPL.26MAY2026.P287.5") == "+AAPL.26MAY2026.P287.5"


def test_to_freedom24_ticker_adds_default_suffix():
    assert to_freedom24_ticker("AAPL") == "AAPL.US"
    assert to_freedom24_ticker("SBER", default_suffix="RU") == "SBER.RU"


def test_to_freedom24_ticker_already_suffixed_unchanged():
    assert to_freedom24_ticker("AAPL.US") == "AAPL.US"


def test_ticker_round_trip():
    assert normalize_ticker(to_freedom24_ticker("AAPL")) == "AAPL"
    assert to_freedom24_ticker(normalize_ticker("TSLA.US")) == "TSLA.US"


# ---------------------------------------------------------------------------
# positions_to_schema
# ---------------------------------------------------------------------------
def test_positions_to_schema_account_math():
    out = positions_to_schema(SAMPLE)
    acc = out["account"]
    # equity = market values (25030 + 2000) + USD cash (1500) = 28530
    assert acc["equity"] == 28530.0
    assert acc["cash"] == 1500.0
    # buying_power assumed == cash (Freedom24 exposes no buying power).
    assert acc["buying_power"] == 1500.0
    assert acc["currency"] == "USD"


def test_positions_to_schema_position_fields_and_avg_cost():
    out = positions_to_schema(SAMPLE)
    aapl = next(p for p in out["positions"] if p["broker_symbol"] == "AAPL.US")
    assert aapl["symbol"] == "AAPL"
    assert aapl["qty"] == 100
    # avg_entry = (market_value - profit_price) / qty = (25030 - 283)/100
    assert round(aapl["avg_entry_price"], 4) == 247.47
    assert aapl["unrealized_pl"] == 283.0
    # pct = profit / cost_basis * 100 = 283 / 24747 * 100
    assert round(aapl["unrealized_pl_pct"], 4) == round(283.0 / 24747.0 * 100.0, 4)
    # weight = market_value / equity * 100 = 25030 / 28530 * 100
    assert round(aapl["weight_pct"], 4) == round(25030.0 / 28530.0 * 100.0, 4)


def test_positions_to_schema_negative_pl_position():
    out = positions_to_schema(SAMPLE)
    tsla = next(p for p in out["positions"] if p["broker_symbol"] == "TSLA.US")
    assert tsla["unrealized_pl"] == -50.0
    assert round(tsla["avg_entry_price"], 4) == 205.0  # (2000 - (-50)) / 10


def test_positions_to_schema_weights_sum_close_to_position_share():
    out = positions_to_schema(SAMPLE)
    total_weight = sum(p["weight_pct"] for p in out["positions"])
    # positions are 27030 / 28530 of equity (cash is the remainder)
    assert round(total_weight, 4) == round(27030.0 / 28530.0 * 100.0, 4)


def test_positions_to_schema_handles_top_level_ps():
    out = positions_to_schema({"ps": SAMPLE["result"]["ps"]})
    assert len(out["positions"]) == 2
    assert out["account"]["equity"] == 28530.0


def test_positions_to_schema_empty_portfolio():
    out = positions_to_schema({"result": {"ps": {"pos": [], "acc": []}}})
    assert out["positions"] == []
    assert out["account"]["equity"] == 0.0
    assert out["account"]["cash"] == 0.0


def test_positions_to_schema_garbage_input():
    out = positions_to_schema({"x": 1})
    assert out["positions"] == []
    assert out["account"]["equity"] == 0.0
    out2 = positions_to_schema("not a dict")
    assert out2["positions"] == []


def test_positions_to_schema_error_passthrough():
    out = positions_to_schema({"error": "Invalid signature provided"})
    assert out["error"] == "Invalid signature provided"
    assert out["positions"] == []
    assert out["account"]["equity"] == 0.0


def test_positions_to_schema_comma_decimal_strings():
    payload = {"ps": {
        "acc": [{"curr": "USD", "s": "1.000,50"}],  # not realistic; just non-USD ignored test below
        "pos": [{"i": "AAPL.US", "q": "10", "mkt_price": "100,5",
                 "market_value": "1005,0", "profit_price": "5,0", "curr": "USD"}],
    }}
    out = positions_to_schema(payload)
    pos = out["positions"][0]
    assert pos["market_value"] == 1005.0
    assert pos["current_price"] == 100.5


def test_positions_to_schema_ignores_non_usd_cash_for_equity():
    payload = {"ps": {
        "acc": [{"curr": "EUR", "s": 9999.0}, {"curr": "USD", "s": 100.0}],
        "pos": [{"i": "AAPL.US", "q": 1, "mkt_price": 50.0,
                 "market_value": 50.0, "profit_price": 0.0, "curr": "USD"}],
    }}
    out = positions_to_schema(payload)
    # Only USD cash counts toward equity: 50 (mkt) + 100 (usd cash) = 150.
    assert out["account"]["cash"] == 100.0
    assert out["account"]["equity"] == 150.0


# ---------------------------------------------------------------------------
# candles_to_ohlcv
# ---------------------------------------------------------------------------
# Documented Tradernet HLOC shape: per-bar order is [high, low, open, close].
HLOC_SAMPLE = {
    "hloc": {"AAPL.US": [
        [107.25, 106.16, 106.96, 106.26],   # h, l, o, c
        [106.45, 104.62, 106.45, 104.75],
    ]},
    "vl": {"AAPL.US": [7588957, 8812260]},
    "xSeries": {"AAPL.US": [1451509200, 1451422800]},  # 2nd bar is OLDER -> must sort
}


def test_candles_hloc_shape_maps_fields():
    rows = candles_to_ohlcv(HLOC_SAMPLE)
    assert len(rows) == 2
    # Oldest first after sort (1451422800 < 1451509200).
    first = rows[0]
    assert first["t"] == 1451422800
    assert first["open"] == 106.45
    assert first["high"] == 106.45
    assert first["low"] == 104.62
    assert first["close"] == 104.75
    assert first["volume"] == 8812260


def test_candles_hloc_sorted_oldest_to_newest():
    rows = candles_to_ohlcv(HLOC_SAMPLE)
    times = [r["t"] for r in rows]
    assert times == sorted(times)


def test_candles_hloc_wrapped_in_result():
    rows = candles_to_ohlcv({"result": HLOC_SAMPLE})
    assert len(rows) == 2


def test_candles_fallback_list_of_dicts():
    payload = [
        {"t": 2, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        {"t": 1, "o": 8, "h": 9, "l": 7, "c": 8.5, "v": 50},
    ]
    rows = candles_to_ohlcv(payload)
    assert [r["t"] for r in rows] == [1, 2]  # sorted oldest first
    assert rows[0]["open"] == 8.0
    assert rows[1]["close"] == 10.5


def test_candles_empty_and_garbage():
    assert candles_to_ohlcv({}) == []
    assert candles_to_ohlcv({"hloc": {}}) == []
    assert candles_to_ohlcv("nope") == []
    assert candles_to_ohlcv({"x": 1}) == []


# ---------------------------------------------------------------------------
# quote_to_schema
# ---------------------------------------------------------------------------
QUOTE_SAMPLE = {"result": {"q": [{
    "c": "AAPL.US", "ltp": "147,42", "bbp": "147,39", "bap": "147,45",
    "vol": "129617290", "name": "Apple Inc.",
}]}}


def test_quote_to_schema_maps_fields_and_strips_suffix():
    out = quote_to_schema(QUOTE_SAMPLE)
    assert out["symbol"] == "AAPL"
    assert out["last"] == 147.42   # comma-decimal coerced
    assert out["bid"] == 147.39
    assert out["ask"] == 147.45
    assert out["volume"] == 129617290.0


def test_quote_to_schema_top_level_q():
    out = quote_to_schema({"q": [{"c": "TSLA.US", "ltp": 200.0, "bbp": 199.9,
                                  "bap": 200.1, "vol": 1000}]})
    assert out["symbol"] == "TSLA"
    assert out["last"] == 200.0


def test_quote_to_schema_empty_and_error():
    empty = quote_to_schema({"result": {"q": []}})
    assert empty["symbol"] == ""
    assert empty["last"] == 0.0
    err = quote_to_schema({"error": "Command disabled"})
    assert err["error"] == "Command disabled"
    assert err["last"] == 0.0

from freedom24_bot.reports import render_premarket

MARKET = {"markets": [{"name": "US", "open": True}]}
POSITIONS = {"result": {"ps": {"acc": [], "pos": [
    {"i": "AAPL.US", "q": 100, "mkt_price": 250.3, "market_value": 25030.0, "profit_price": 283.0},
]}}}
QUOTES = {"AAPL.US": {"result": {"q": [{"c": "AAPL.US", "ltp": 251.0, "pcp": 0.28}]}}}
ORDERS = {"orders": []}


def test_render_premarket_includes_holdings_and_orders_sections():
    out = render_premarket(MARKET, POSITIONS, QUOTES, ORDERS)
    assert "AAPL.US" in out
    assert "Pre-market" in out


def test_render_premarket_handles_no_holdings():
    out = render_premarket(MARKET, {"x": 1}, {}, {"orders": []})
    assert "Pre-market" in out

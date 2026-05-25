from freedom24_bot.formatting import extract_portfolio, format_portfolio

# Documented getPositionJson / portfolio shape (confirmed live):
# positions under `pos`, accounts/cash under `acc`, wrapped in `result.ps`.
SAMPLE = {"result": {"ps": {
    "acc": [{"curr": "USD", "s": 1500.0}],
    "pos": [
        {"i": "AAPL.US", "name": "Apple Inc.", "q": 100, "mkt_price": 250.3,
         "market_value": 25030.0, "profit_price": 283.0, "curr": "USD"},
        {"i": "TSLA.US", "name": "Tesla", "q": 10, "mkt_price": 200.0,
         "market_value": 2000.0, "profit_price": -50.0, "curr": "USD"},
    ],
}}}


def test_extract_portfolio_returns_positions_and_accounts():
    positions, accounts = extract_portfolio(SAMPLE)
    assert len(positions) == 2
    assert accounts[0]["curr"] == "USD"


def test_extract_portfolio_handles_top_level_ps():
    positions, accounts = extract_portfolio({"ps": SAMPLE["result"]["ps"]})
    assert len(positions) == 2


def test_extract_portfolio_empty_on_garbage():
    assert extract_portfolio({"x": 1}) == ([], [])


def test_format_portfolio_lists_tickers_and_cash():
    out = format_portfolio(SAMPLE)
    assert "AAPL.US" in out and "TSLA.US" in out
    assert "1500" in out  # cash
    assert "USD" in out


def test_format_portfolio_empty():
    assert "No open positions" in format_portfolio({"x": 1})

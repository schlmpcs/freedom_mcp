from freedom24_bot.formatting import fmt_num, extract_quote, format_quote

# Documented getStockQuotesJson shape: {"result": {"q": [ {...} ]}}
SAMPLE = {"result": {"q": [{
    "c": "AAPL.US", "name": "Apple Inc.", "ltp": 250.3, "pp": 245.0,
    "chg": 5.3, "pcp": 2.16, "bbp": 250.2, "bap": 250.4, "vol": 1000000,
}]}}


def test_fmt_num_handles_none():
    assert fmt_num(None) == "-"


def test_fmt_num_handles_comma_decimal_string():
    assert fmt_num("147,39") == "147.39"


def test_fmt_num_rounds():
    assert fmt_num(2.16666, 2) == "2.17"


def test_extract_quote_returns_first_entry():
    assert extract_quote(SAMPLE)["c"] == "AAPL.US"


def test_extract_quote_empty_on_garbage():
    assert extract_quote({"nonsense": 1}) == {}


def test_format_quote_contains_ticker_and_last_and_pct():
    out = format_quote(SAMPLE)
    assert "AAPL.US" in out
    assert "250.3" in out
    assert "2.16" in out

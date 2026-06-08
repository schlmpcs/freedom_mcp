"""Momentum signal extraction must parse Freedom24's real candle shapes.

The agent fetches candles via ``getQuotesHistory``, whose documented payload is
a *nested* HLOC map ({"hloc": {ticker: [[high,low,open,close],...]}, ...}). A
prior bug only handled flat shapes, so every ticker reported "insufficient
candle data" and the agent decided NOTHING forever.
"""

from __future__ import annotations

from agent.strategies.momentum import MomentumStrategy, _extract_series


def _nested_hloc(ticker: str, bars: list[list[float]], vols: list[float]) -> dict:
    """Build the documented getQuotesHistory shape: [high, low, open, close]."""
    return {
        "hloc": {ticker: bars},
        "vl": {ticker: vols},
        "xSeries": {ticker: list(range(len(bars)))},
    }


def test_extract_series_parses_nested_hloc():
    # 3 bars; per-bar order is [high, low, open, close].
    bars = [
        [101.0, 99.0, 100.0, 100.5],
        [103.0, 100.0, 100.5, 102.0],
        [105.0, 101.0, 102.0, 104.0],
    ]
    vols = [1000.0, 1500.0, 3000.0]
    closes, volumes, highs = _extract_series(_nested_hloc("AMZN.US", bars, vols))

    assert closes == [100.5, 102.0, 104.0]
    assert highs == [101.0, 103.0, 105.0]
    assert volumes[-1] == 3000.0


def test_extract_series_handles_result_envelope():
    bars = [[10.0, 9.0, 9.5, 9.8], [11.0, 9.8, 9.9, 10.5]]
    payload = {"result": _nested_hloc("AAPL.US", bars, [500.0, 800.0])}
    closes, _, highs = _extract_series(payload)
    assert closes == [9.8, 10.5]
    assert highs == [10.0, 11.0]


def test_signals_computed_from_nested_hloc():
    # 11 rising bars -> last close above the 10-day average, volume surge today.
    bars = [[c + 1, c - 1, c, c] for c in range(100, 111)]  # close == c
    vols = [1000.0] * 10 + [5000.0]
    signals = MomentumStrategy()._compute_signals(_nested_hloc("NVDA.US", bars, vols))

    assert signals.get("note") is None, signals
    assert signals["above_10d_avg"] is True
    assert signals["ma_10"] is not None
    assert signals["volume_ratio"] is not None and signals["volume_ratio"] > 1.2


def test_legacy_list_of_dicts_still_works():
    rows = [
        {"c": 100.0, "v": 1000.0, "h": 101.0},
        {"c": 102.0, "v": 1500.0, "h": 103.0},
    ]
    closes, volumes, highs = _extract_series(rows)
    assert closes == [100.0, 102.0]
    assert highs == [101.0, 103.0]
    assert volumes == [1000.0, 1500.0]


def test_empty_and_error_candles_yield_no_series():
    assert _extract_series(None) == ([], [], [])
    assert _extract_series({"error": "boom"}) == ([], [], [])
    assert _extract_series({}) == ([], [], [])


def test_insufficient_data_returns_explanatory_note():
    signals = MomentumStrategy()._compute_signals({"error": "boom"})
    assert signals["above_10d_avg"] is None
    assert "insufficient candle data" in signals["note"]

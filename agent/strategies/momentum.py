"""Momentum strategy: buy strength, sell weakness, confirmed by volume.

``filter_observations`` derives a small set of momentum signals from each
ticker's daily candles (10-day average, volume ratio, distance from the recent
high, and yesterday's move) and attaches them under ``momentum_signals`` so the
model reasons over distilled indicators rather than raw OHLCV arrays.

The signal extraction is defensive about candle shape: Tradernet's
``getQuotesHistory`` payload varies by account, so several common layouts (a
list of OHLCV dicts, a ``{"candles": [...]}`` wrapper, or parallel arrays) are
all handled. When data is missing or too short, signals are ``None`` with an
explanatory ``note`` rather than raising.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.strategies.base import BaseStrategy

_SYSTEM_PROMPT = """
Momentum strategy rules:
- Only buy if price is above the 10-day average (above_10d_avg == true)
- Only buy if today's volume > 1.2x average volume (volume_ratio > 1.2)
- Exit if a position drops 3% from entry (stop loss)
- Exit if a position gains 8% (take profit)
- Never hold more than 3 positions simultaneously
- Ignore stocks that moved >5% yesterday (no chasing)
- When momentum_signals are missing for a ticker, treat its setup as unconfirmed
  and prefer "nothing" over guessing.
""".strip()


class MomentumStrategy(BaseStrategy):
    """Volume-confirmed breakout strategy over a fixed US large-cap watchlist."""

    name = "momentum"
    description = "Buy strength, sell weakness. Focus on volume-confirmed breakouts."
    watchlist = ["AAPL.US", "TSLA.US", "NVDA.US", "MSFT.US", "AMZN.US"]

    def get_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def filter_observations(self, observations: dict) -> dict:
        """Return a copy of ``observations`` with ``momentum_signals`` added."""
        enriched: dict[str, Any] = {}
        for ticker, data in observations.items():
            entry = dict(data) if isinstance(data, dict) else {"raw": data}
            # Respect pre-computed signals (e.g. from eval scenarios).
            if "momentum_signals" not in entry:
                entry["momentum_signals"] = self._compute_signals(entry.get("candles"))
            enriched[ticker] = entry
        return enriched

    # -- signal computation -------------------------------------------------
    def _compute_signals(self, candles: Any) -> dict:
        closes, volumes, highs = _extract_series(candles)
        if len(closes) < 2:
            return {
                "above_10d_avg": None,
                "ma_10": None,
                "volume_ratio": None,
                "distance_from_high_pct": None,
                "moved_pct_yesterday": None,
                "note": "insufficient candle data for momentum signals",
            }

        last_close = closes[-1]
        window = closes[-10:] if len(closes) >= 10 else closes
        ma_10 = sum(window) / len(window)

        volume_ratio: Optional[float] = None
        if len(volumes) >= 2 and volumes[-1] is not None:
            prior = [v for v in volumes[:-1] if v]
            if prior:
                avg_vol = sum(prior) / len(prior)
                if avg_vol > 0:
                    volume_ratio = round(volumes[-1] / avg_vol, 4)

        distance_from_high_pct: Optional[float] = None
        if highs:
            recent_high = max(highs)
            if recent_high > 0:
                distance_from_high_pct = round((recent_high - last_close) / recent_high * 100, 4)

        moved_pct_yesterday: Optional[float] = None
        if len(closes) >= 3 and closes[-3]:
            moved_pct_yesterday = round((closes[-2] - closes[-3]) / closes[-3] * 100, 4)

        return {
            "above_10d_avg": bool(last_close > ma_10),
            "ma_10": round(ma_10, 4),
            "volume_ratio": volume_ratio,
            "distance_from_high_pct": distance_from_high_pct,
            "moved_pct_yesterday": moved_pct_yesterday,
        }


# Candidate field names for each series, in priority order.
_CLOSE_KEYS = ("c", "close", "Close", "cl")
_VOLUME_KEYS = ("v", "vol", "volume", "Volume", "vl")
_HIGH_KEYS = ("h", "high", "High")


def _extract_series(candles: Any) -> tuple[list[float], list[float], list[float]]:
    """Return (closes, volumes, highs) from a variety of candle payload shapes."""
    if not candles or isinstance(candles, dict) and candles.get("error"):
        return [], [], []

    rows = _as_row_list(candles)
    if rows:
        closes = [_pick(r, _CLOSE_KEYS) for r in rows]
        volumes = [_pick(r, _VOLUME_KEYS) for r in rows]
        highs = [_pick(r, _HIGH_KEYS) for r in rows]
        return _clean(closes), volumes, _clean(highs)

    # Parallel-array layout: {"c": [...], "v": [...], "h": [...]}.
    if isinstance(candles, dict):
        closes = _first_array(candles, _CLOSE_KEYS)
        volumes = _first_array(candles, _VOLUME_KEYS)
        highs = _first_array(candles, _HIGH_KEYS)
        return _clean(closes), volumes, _clean(highs)

    return [], [], []


def _as_row_list(candles: Any) -> list[dict]:
    """Extract a list of per-bar dicts, if the payload is shaped that way."""
    if isinstance(candles, list) and candles and isinstance(candles[0], dict):
        return candles
    if isinstance(candles, dict):
        for key in ("candles", "hloc", "data", "items"):
            value = candles.get(key)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def _pick(row: dict, keys: tuple[str, ...]) -> Optional[float]:
    for key in keys:
        if key in row:
            try:
                return float(row[key])
            except (TypeError, ValueError):
                return None
    return None


def _first_array(payload: dict, keys: tuple[str, ...]) -> list:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list) and value:
            out: list[float] = []
            for item in value:
                try:
                    out.append(float(item))
                except (TypeError, ValueError):
                    out.append(None)  # type: ignore[arg-type]
            return out
    return []


def _clean(values: list) -> list[float]:
    """Drop ``None`` entries, preserving order."""
    return [v for v in values if v is not None]

"""End-to-end (offline) integration test of the decision-support chain.

Ties together the three Phase pieces WITHOUT any network or live MCP call:

    1. Freedom24 get_portfolio JSON  --(skill_adapter.positions_to_schema)-->
       Alpaca-style account/positions  (extract `equity`).
    2. equity  --(GLOBAL position-sizer)-->  risk-based share count.
    3. shares + entry/stop/target  --(breakout-trade-planner order_builder)-->
       Freedom24 place_order proposals (entry + stop_loss + take_profit).
    4. guardrail: NO proposal in the chain may carry confirm=True.

Placeholder data ONLY (AAPL.US, synthetic prices). No secrets, no I/O.

The adapter and breakout-planner parts live IN this repo and must ALWAYS run.
The position-sizer is a GLOBAL skill (~/.claude/skills/...); if it is not
installed, that single step is ``pytest.skip``-ped (not failed) so the suite is
robust on machines without the global skills.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from freedom24_core.skill_adapter import positions_to_schema

# --- locate the project-local breakout-trade-planner scripts ------------------
# This test file: <repo>/tests/test_skill_integration.py
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BREAKOUT_SCRIPTS = os.path.join(
    _REPO_ROOT, ".claude", "skills", "breakout-trade-planner", "scripts"
)
if _BREAKOUT_SCRIPTS not in sys.path:
    sys.path.insert(0, _BREAKOUT_SCRIPTS)

from order_builder import build_pre_place_proposal  # noqa: E402

# --- locate the GLOBAL position-sizer skill (optional) ------------------------
_POSITION_SIZER_SCRIPTS = os.path.expanduser(
    "~/.claude/skills/position-sizer/scripts"
)
_POSITION_SIZER_PY = os.path.join(_POSITION_SIZER_SCRIPTS, "position_sizer.py")


# Synthetic Freedom24 get_portfolio payload (documented result.ps shape).
#   AAPL: market_value 25030, profit_price 283  -> cost basis 24747
#   TSLA: market_value  2000, profit_price -50  -> cost basis  2050
#   USD cash 1500.  equity = 25030 + 2000 + 1500 = 28530.
PORTFOLIO_JSON = {"result": {"ps": {
    "acc": [{"curr": "USD", "s": 1500.0}],
    "pos": [
        {"i": "AAPL.US", "name": "Apple Inc.", "q": 100, "mkt_price": 250.3,
         "market_value": 25030.0, "profit_price": 283.0, "curr": "USD"},
        {"i": "TSLA.US", "name": "Tesla", "q": 10, "mkt_price": 200.0,
         "market_value": 2000.0, "profit_price": -50.0, "curr": "USD"},
    ],
}}}

# Synthetic trade levels for the planned breakout (placeholder, AAPL.US).
SYMBOL = "AAPL"
SIGNAL_ENTRY = 260.0   # pivot / stop trigger
WORST_ENTRY = 262.0    # worst-case fill ceiling
STOP_LOSS = 250.0      # protective stop (below entry)
TAKE_PROFIT = 290.0    # profit target (above worst_entry)
RISK_PCT = 1.0         # 1% of account risked per trade


def _load_position_sizer():
    """Import the GLOBAL position_sizer module, or skip if not installed."""
    if not os.path.isfile(_POSITION_SIZER_PY):
        pytest.skip(
            "global position-sizer skill not installed at "
            f"{_POSITION_SIZER_PY!r}; skipping the sizing step "
            "(adapter + breakout-planner steps still run)."
        )
    if _POSITION_SIZER_SCRIPTS not in sys.path:
        sys.path.insert(0, _POSITION_SIZER_SCRIPTS)
    mod_name = "global_position_sizer"
    spec = importlib.util.spec_from_file_location(mod_name, _POSITION_SIZER_PY)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so the module's @dataclass can resolve cls.__module__
    # (dataclasses looks the owning module up in sys.modules).
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Step 1 — portfolio JSON -> Alpaca-style account/positions
# ---------------------------------------------------------------------------
def test_step1_portfolio_to_equity_and_positions():
    """positions_to_schema yields a numeric equity and normalized positions."""
    schema = positions_to_schema(PORTFOLIO_JSON)

    equity = schema["account"]["equity"]
    assert isinstance(equity, (int, float))
    assert equity == 28530.0  # 25030 + 2000 + 1500

    positions = schema["positions"]
    assert len(positions) == 2
    symbols = {p["symbol"] for p in positions}
    assert symbols == {"AAPL", "TSLA"}  # suffixes stripped (normalized)
    # broker_symbol retains the .US exchange suffix.
    assert all(p["broker_symbol"].endswith(".US") for p in positions)


# ---------------------------------------------------------------------------
# Step 2 — equity drives the GLOBAL position-sizer (optional / skippable)
# ---------------------------------------------------------------------------
def test_step2_position_sizer_returns_positive_int_within_risk():
    """equity -> position-sizer -> positive integer share count within risk."""
    ps = _load_position_sizer()  # may pytest.skip

    equity = positions_to_schema(PORTFOLIO_JSON)["account"]["equity"]

    params = ps.SizingParameters(
        account_size=equity,
        entry_price=SIGNAL_ENTRY,
        stop_price=STOP_LOSS,
        risk_pct=RISK_PCT,
    )
    result = ps.calculate_position(params)

    shares = result["final_recommended_shares"]
    assert isinstance(shares, int)
    assert shares > 0

    # Sanity-check against the documented fixed-fractional formula:
    #   dollar_risk = equity * risk_pct/100 = 28530 * 0.01 = 285.30
    #   risk_per_share = 260 - 250 = 10  ->  shares = int(285.30/10) = 28
    assert shares == 28

    # Realized risk must not exceed the requested risk budget.
    assert result["final_risk_pct"] <= RISK_PCT + 1e-9
    assert result["final_risk_dollars"] <= equity * RISK_PCT / 100 + 1e-9


# ---------------------------------------------------------------------------
# Step 3 — shares + levels -> breakout-planner Freedom24 proposals
# ---------------------------------------------------------------------------
def _sized_shares() -> int:
    """Compute the share count for the planner step.

    Uses the GLOBAL position-sizer when available, else falls back to the
    documented fixed-fractional formula so the in-repo planner step ALWAYS runs
    regardless of whether the optional global skill is installed.
    """
    equity = positions_to_schema(PORTFOLIO_JSON)["account"]["equity"]
    if os.path.isfile(_POSITION_SIZER_PY):
        ps = _load_position_sizer()
        params = ps.SizingParameters(
            account_size=equity,
            entry_price=SIGNAL_ENTRY,
            stop_price=STOP_LOSS,
            risk_pct=RISK_PCT,
        )
        return ps.calculate_position(params)["final_recommended_shares"]
    # Fallback: replicate fixed-fractional sizing (no global skill needed).
    dollar_risk = equity * RISK_PCT / 100
    risk_per_share = SIGNAL_ENTRY - STOP_LOSS
    return int(dollar_risk / risk_per_share)


def test_step3_proposals_bundle_entry_stop_target():
    """The planner emits a bundle of entry + stop_loss + take_profit proposals."""
    qty = _sized_shares()
    assert qty > 0

    bundle = build_pre_place_proposal(
        symbol=SYMBOL,
        qty=qty,
        signal_entry=SIGNAL_ENTRY,
        worst_entry=WORST_ENTRY,
        stop_loss=STOP_LOSS,
        take_profit=TAKE_PROFIT,
    )

    proposals = bundle["proposals"]
    assert isinstance(proposals, list)
    assert len(proposals) == 3

    by_type = {p["proposal_type"]: p for p in proposals}
    assert set(by_type) == {"entry", "stop_loss", "take_profit"}

    entry = by_type["entry"]
    stop = by_type["stop_loss"]
    target = by_type["take_profit"]

    # Every proposal targets the place_order tool and carries the sized qty.
    for p in proposals:
        assert p["tool"] == "mcp__freedom24__place_order"
        assert p["quantity"] == qty
        assert p["ticker"].endswith(".US")

    # Entry: buy, Freedom24 .US ticker.
    assert entry["action"] == "buy"
    assert entry["ticker"] == "AAPL.US"

    # Stop: a protective SELL STOP at the stop-loss level.
    assert stop["action"] == "sell"
    assert stop["order_type"] == "stop"
    assert stop["price"] == STOP_LOSS

    # Target: a SELL LIMIT at the take-profit level.
    assert target["action"] == "sell"
    assert target["order_type"] == "limit"
    assert target["price"] == TAKE_PROFIT


# ---------------------------------------------------------------------------
# Step 4 — guardrail: decision-support invariant (confirm must be False)
# ---------------------------------------------------------------------------
def test_step4_no_proposal_has_confirm_true():
    """Scan every proposal in the chain — NONE may carry confirm=True."""
    qty = _sized_shares()
    bundle = build_pre_place_proposal(
        symbol=SYMBOL,
        qty=qty,
        signal_entry=SIGNAL_ENTRY,
        worst_entry=WORST_ENTRY,
        stop_loss=STOP_LOSS,
        take_profit=TAKE_PROFIT,
    )

    for p in bundle["proposals"]:
        assert "confirm" in p
        assert p["confirm"] is False, (
            f"{p['proposal_type']} proposal must be confirm=False "
            "(decision-support only)"
        )
    # And explicitly: no proposal anywhere is confirm=True.
    assert not any(p.get("confirm") is True for p in bundle["proposals"])

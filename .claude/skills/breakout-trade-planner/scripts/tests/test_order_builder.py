"""Tests for the Freedom24 order_builder module (project-local override).

Asserts the Freedom24 place_order PROPOSAL shape: entry stop_limit + separate
stop + separate target, all confirm=False, tickers carry a .US suffix, and the
validation rules from the original Alpaca builder still hold. Placeholder data
only — no real account values.
"""

import pytest
from order_builder import (
    PLACE_ORDER_TOOL,
    build_entry_condition,
    build_entry_proposal,
    build_post_confirm_proposal,
    build_pre_place_proposal,
    build_revalidation_advisory,
    build_stop_loss_proposal,
    build_take_profit_proposal,
)


class TestBuildEntryProposal:
    def test_freedom24_stop_limit_proposal_structure(self):
        prop = build_entry_proposal(
            symbol="PWR",
            qty=10,
            signal_entry=583.32,
            worst_entry=595.40,
            stop_loss=516.81,
            take_profit=717.57,
        )
        assert prop["tool"] == PLACE_ORDER_TOOL
        assert prop["tool"] == "mcp__freedom24__place_order"
        assert prop["ticker"] == "PWR.US"  # .US suffix added by adapter
        assert prop["action"] == "buy"
        assert prop["quantity"] == 10
        assert prop["order_type"] == "stop_limit"
        # single price arg carries signal_entry (stop trigger AND limit on F24)
        assert prop["price"] == 583.32
        # worst_entry surfaced as a hint since one price arg can't carry both
        assert prop["limit_hint"] == 595.40
        assert prop["confirm"] is False
        assert prop["proposal_type"] == "entry"
        assert prop["execution_mode"] == "pre_place"
        assert "note" in prop and prop["note"]
        # no Alpaca bracket fields leak through
        assert "order_class" not in prop
        assert "take_profit" not in prop
        assert "stop_loss" not in prop

    def test_already_suffixed_ticker_unchanged(self):
        prop = build_entry_proposal(
            symbol="AAPL.US",
            qty=5,
            signal_entry=100.0,
            worst_entry=102.0,
            stop_loss=95.0,
            take_profit=110.0,
        )
        assert prop["ticker"] == "AAPL.US"

    def test_qty_zero_raises(self):
        with pytest.raises(ValueError, match="qty must be positive"):
            build_entry_proposal(
                symbol="X",
                qty=0,
                signal_entry=100.0,
                worst_entry=102.0,
                stop_loss=95.0,
                take_profit=110.0,
            )

    def test_stop_too_close_to_entry_raises(self):
        with pytest.raises(ValueError, match="stop_loss.*must be.*below"):
            build_entry_proposal(
                symbol="X",
                qty=10,
                signal_entry=100.0,
                worst_entry=102.0,
                stop_loss=100.0,
                take_profit=110.0,
            )

    def test_take_profit_below_worst_raises(self):
        with pytest.raises(ValueError, match="take_profit.*must be above"):
            build_entry_proposal(
                symbol="X",
                qty=10,
                signal_entry=100.0,
                worst_entry=102.0,
                stop_loss=95.0,
                take_profit=101.0,
            )


class TestBuildStopLossProposal:
    def test_separate_sell_stop(self):
        prop = build_stop_loss_proposal(symbol="PWR", qty=10, stop_loss=516.81)
        assert prop["tool"] == PLACE_ORDER_TOOL
        assert prop["ticker"] == "PWR.US"
        assert prop["action"] == "sell"
        assert prop["order_type"] == "stop"
        assert prop["price"] == 516.81
        assert prop["quantity"] == 10
        assert prop["confirm"] is False
        assert prop["proposal_type"] == "stop_loss"
        assert prop["place_after"] == "entry_fill"

    def test_stop_not_below_entry_raises(self):
        with pytest.raises(ValueError, match="must be below entry"):
            build_stop_loss_proposal(symbol="X", qty=10, stop_loss=100.0, signal_entry=100.0)

    def test_qty_zero_raises(self):
        with pytest.raises(ValueError, match="qty must be positive"):
            build_stop_loss_proposal(symbol="X", qty=0, stop_loss=95.0)


class TestBuildTakeProfitProposal:
    def test_separate_sell_limit(self):
        prop = build_take_profit_proposal(symbol="PWR", qty=10, take_profit=717.57)
        assert prop["tool"] == PLACE_ORDER_TOOL
        assert prop["ticker"] == "PWR.US"
        assert prop["action"] == "sell"
        assert prop["order_type"] == "limit"
        assert prop["price"] == 717.57
        assert prop["confirm"] is False
        assert prop["proposal_type"] == "take_profit"
        assert prop["place_after"] == "entry_fill"

    def test_target_not_above_entry_raises(self):
        with pytest.raises(ValueError, match="must be above worst_entry"):
            build_take_profit_proposal(symbol="X", qty=10, take_profit=100.0, worst_entry=102.0)

    def test_qty_zero_raises(self):
        with pytest.raises(ValueError, match="qty must be positive"):
            build_take_profit_proposal(symbol="X", qty=0, take_profit=110.0)


class TestBuildPrePlaceProposal:
    def test_three_separate_proposals_no_bracket(self):
        bundle = build_pre_place_proposal(
            symbol="PWR",
            qty=10,
            signal_entry=583.32,
            worst_entry=595.40,
            stop_loss=516.81,
            take_profit=717.57,
        )
        assert bundle["broker"] == "freedom24"
        assert bundle["has_native_bracket"] is False
        props = bundle["proposals"]
        assert len(props) == 3
        types = [p["proposal_type"] for p in props]
        assert types == ["entry", "stop_loss", "take_profit"]
        # all are place_order proposals, all confirm=False, all .US tickers
        for p in props:
            assert p["tool"] == "mcp__freedom24__place_order"
            assert p["confirm"] is False
            assert p["ticker"].endswith(".US")
        # entry buys, exits sell
        assert props[0]["action"] == "buy"
        assert props[1]["action"] == "sell"
        assert props[2]["action"] == "sell"
        # exits are follow-on
        assert props[1]["place_after"] == "entry_fill"
        assert props[2]["place_after"] == "entry_fill"

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            build_pre_place_proposal(
                symbol="X", qty=0, signal_entry=100.0, worst_entry=102.0,
                stop_loss=95.0, take_profit=110.0,
            )


class TestBuildPostConfirmProposal:
    def test_post_confirm_entry_is_plain_limit(self):
        cond = build_entry_condition(pivot=583.73)
        bundle = build_post_confirm_proposal(
            symbol="PWR",
            qty=10,
            worst_entry=595.40,
            stop_loss=516.81,
            take_profit=717.57,
            entry_condition=cond,
        )
        assert bundle["broker"] == "freedom24"
        assert bundle["has_native_bracket"] is False
        assert bundle["requires_monitor_confirmation"] is True
        props = bundle["proposals"]
        assert len(props) == 3
        entry = props[0]
        assert entry["order_type"] == "limit"  # no stop trigger after confirm
        assert entry["price"] == 595.40
        assert entry["execution_mode"] == "post_confirm"
        assert entry["entry_condition"]["bar_interval"] == "5min"
        for p in props:
            assert p["confirm"] is False
            assert p["execution_mode"] == "post_confirm"
            assert p["ticker"] == "PWR.US"

    def test_qty_zero_raises(self):
        with pytest.raises(ValueError, match="qty must be positive"):
            build_post_confirm_proposal(
                symbol="X", qty=0, worst_entry=102.0, stop_loss=95.0,
                take_profit=110.0, entry_condition={},
            )


class TestBuildRevalidationAdvisory:
    def test_advisory_structure(self):
        advisory = build_revalidation_advisory(
            symbol="ANET",
            pivot=141.77,
            current_price=145.07,
            worst_entry=144.60,
        )
        assert advisory["symbol"] == "ANET"
        assert advisory["ticker"] == "ANET.US"
        assert advisory["plan_type"] == "late_breakout_revalidation"
        assert advisory["next_action"].startswith("revalidate")
        assert advisory["pivot"] == 141.77
        assert advisory["current_price"] == 145.07
        assert advisory["max_entry_price"] == 144.60

    def test_no_order_fields(self):
        advisory = build_revalidation_advisory(
            symbol="X", pivot=100.0, current_price=103.0, worst_entry=102.0,
        )
        assert "quantity" not in advisory
        assert "order_type" not in advisory
        assert "confirm" not in advisory


class TestBuildEntryCondition:
    def test_machine_readable_format(self):
        cond = build_entry_condition(pivot=319.52)
        assert cond["bar_interval"] == "5min"
        assert cond["trigger"]["field"] == "close"
        assert cond["trigger"]["op"] == ">"
        assert cond["trigger"]["value"] == 319.52
        assert len(cond["checks"]) == 3

    def test_custom_thresholds(self):
        cond = build_entry_condition(
            pivot=100.0, close_loc_min=0.70, rvol_threshold=2.0, max_chase_pct=1.5,
        )
        assert cond["checks"][0]["value"] == 0.70
        assert cond["checks"][1]["value"] == 2.0
        assert cond["checks"][2]["value"] == 1.5

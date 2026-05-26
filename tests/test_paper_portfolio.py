"""Tests for the paper portfolio: fills, risk limits, and guard rails."""

from agent.portfolio_state import PaperPortfolio


def test_valid_buy_within_limit_fills():
    pf = PaperPortfolio(starting_cash=10_000.0)
    fill = pf.buy("AAPL.US", quantity=2, price=200.0)  # cost 400 <= 5% (500)
    assert fill["status"] == "filled"
    assert pf.cash == 9_600.0
    assert pf.get_position("AAPL.US")["quantity"] == 2


def test_buy_rejected_when_over_5pct_limit():
    pf = PaperPortfolio(starting_cash=10_000.0)
    fill = pf.buy("AAPL.US", quantity=1, price=600.0)  # 600 > 5% (500)
    assert fill["status"] == "rejected"
    assert "limit" in fill["reason"].lower()
    assert pf.cash == 10_000.0  # unchanged
    assert pf.get_position("AAPL.US") is None


def test_buy_rejected_when_insufficient_cash():
    pf = PaperPortfolio(starting_cash=100.0)
    fill = pf.buy("AAPL.US", quantity=1, price=200.0)  # need 200, have 100
    assert fill["status"] == "rejected"
    assert "insufficient cash" in fill["reason"].lower()


def test_cannot_sell_what_you_dont_have():
    pf = PaperPortfolio(starting_cash=10_000.0)
    fill = pf.sell("TSLA.US", quantity=1, price=100.0)
    assert fill["status"] == "rejected"
    assert "no open position" in fill["reason"].lower()


def test_cannot_oversell_a_position():
    pf = PaperPortfolio(starting_cash=10_000.0)
    pf.buy("AAPL.US", quantity=2, price=200.0)
    fill = pf.sell("AAPL.US", quantity=5, price=210.0)
    assert fill["status"] == "rejected"
    assert "only" in fill["reason"].lower()


def test_sell_realizes_pnl_and_returns_cash():
    pf = PaperPortfolio(starting_cash=10_000.0)
    pf.buy("AAPL.US", quantity=2, price=200.0)  # cash -> 9600
    fill = pf.sell("AAPL.US", quantity=2, price=250.0)  # +100 profit
    assert fill["status"] == "filled"
    assert fill["realized_pnl"] == 100.0
    assert pf.cash == 10_100.0
    assert pf.get_position("AAPL.US") is None  # fully closed


def test_update_prices_marks_to_market():
    pf = PaperPortfolio(starting_cash=10_000.0)
    pf.buy("AAPL.US", quantity=2, price=200.0)
    pf.update_prices({"AAPL.US": 250.0})
    state = pf.get_state()
    assert state["positions"]["AAPL.US"]["last_price"] == 250.0
    # cash 9600 + 2*250 = 10100
    assert state["total_value"] == 10_100.0
    assert state["pnl_pct"] == 1.0


def test_transactions_logged_to_sqlite(tmp_path):
    import sqlite3

    db = str(tmp_path / "agent.db")
    pf = PaperPortfolio(starting_cash=10_000.0, db_path=db)
    pf.buy("AAPL.US", quantity=2, price=200.0)
    pf.sell("AAPL.US", quantity=1, price=210.0)
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT action FROM transactions ORDER BY rowid").fetchall()
    assert [r[0] for r in rows] == ["buy", "sell"]


def test_equity_curve_grows_with_activity():
    pf = PaperPortfolio(starting_cash=10_000.0)
    start = len(pf.get_equity_curve())
    pf.buy("AAPL.US", quantity=1, price=100.0)
    assert len(pf.get_equity_curve()) > start

"""Tests for the read-only paper-trading dashboard/report."""

import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agent.dashboard import _handler_class, load_report, main, render_html


def _seed_db(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE decisions (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                cycle INTEGER NOT NULL,
                ticker TEXT,
                action TEXT NOT NULL,
                quantity REAL,
                price_at_decision REAL,
                thinking TEXT,
                reasoning_summary TEXT,
                confidence REAL,
                paper_order_id TEXT
            );
            CREATE TABLE transactions (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                cash_after REAL NOT NULL,
                reason TEXT,
                realized_pnl REAL
            );
            CREATE TABLE positions (
                timestamp TEXT NOT NULL,
                cycle INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                last_price REAL NOT NULL,
                market_value REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                unrealized_pnl_pct REAL NOT NULL
            );
            CREATE TABLE equity (
                timestamp TEXT NOT NULL,
                cycle INTEGER NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                realized_pnl_cum REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL
            );
            """
        )
        conn.executemany(
            """
            INSERT INTO equity (
                timestamp, cycle, cash, positions_value, total_value,
                realized_pnl_cum, unrealized_pnl, pnl_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-01-01T00:00:00+00:00", 0, 10000.0, 0.0, 10000.0, 0.0, 0.0, 0.0),
                ("2026-01-01T00:01:00+00:00", 1, 9600.0, 420.0, 10020.0, 0.0, 20.0, 0.2),
                ("2026-01-01T00:02:00+00:00", 2, 9600.0, 500.0, 10100.0, 0.0, 100.0, 1.0),
                ("2026-01-01T00:03:00+00:00", 3, 9860.0, 260.0, 10120.0, 60.0, 60.0, 1.2),
            ],
        )
        conn.executemany(
            """
            INSERT INTO positions (
                timestamp, cycle, ticker, quantity, avg_price, last_price,
                market_value, unrealized_pnl, unrealized_pnl_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-01-01T00:01:00+00:00", 1, "AAPL.US", 2.0, 200.0, 210.0, 420.0, 20.0, 5.0),
                ("2026-01-01T00:02:00+00:00", 2, "AAPL.US", 2.0, 200.0, 250.0, 500.0, 100.0, 25.0),
                ("2026-01-01T00:03:00+00:00", 3, "AAPL.US", 1.0, 200.0, 260.0, 260.0, 60.0, 30.0),
                ("2026-01-01T00:03:00+00:00", 3, "MSFT.US", 0.0, 100.0, 100.0, 0.0, 0.0, 0.0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO transactions (
                id, timestamp, ticker, action, quantity, price, cash_after,
                reason, realized_pnl
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "txn-buy",
                    "2026-01-01T00:01:00+00:00",
                    "AAPL.US",
                    "buy",
                    2.0,
                    200.0,
                    9600.0,
                    "opened momentum paper position",
                    None,
                ),
                (
                    "txn-sell",
                    "2026-01-01T00:03:00+00:00",
                    "AAPL.US",
                    "sell",
                    1.0,
                    260.0,
                    9860.0,
                    "took profit",
                    60.0,
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO decisions (
                id, timestamp, cycle, ticker, action, quantity, price_at_decision,
                thinking, reasoning_summary, confidence, paper_order_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "dec-buy",
                    "2026-01-01T00:01:00+00:00",
                    1,
                    "AAPL.US",
                    "buy",
                    2.0,
                    200.0,
                    "full buy reasoning",
                    "buy because momentum improved",
                    0.72,
                    "txn-buy",
                ),
                (
                    "dec-hold",
                    "2026-01-01T00:02:00+00:00",
                    2,
                    "AAPL.US",
                    "hold",
                    None,
                    250.0,
                    "full hold reasoning",
                    "hold while trend remains intact",
                    0.61,
                    None,
                ),
                (
                    "dec-sell",
                    "2026-01-01T00:03:00+00:00",
                    3,
                    "AAPL.US",
                    "sell",
                    1.0,
                    260.0,
                    "full sell reasoning",
                    "sell partial after extension",
                    0.83,
                    "txn-sell",
                ),
            ],
        )


def test_load_report_current_state(tmp_path):
    db = tmp_path / "agent.db"
    _seed_db(db)

    report = load_report(str(db))

    assert report.current.total_value == 10120.0
    assert report.total_pnl == 120.0
    assert report.current.realized_pnl_cum == 60.0
    assert report.current.unrealized_pnl == 60.0
    assert [p.ticker for p in report.positions] == ["AAPL.US"]
    assert report.positions[0].quantity == 1.0
    assert [t.action for t in report.trades] == ["sell", "buy"]
    assert report.summary.peak_value == 10120.0
    assert report.summary.max_drawdown_pct == 0.0


def test_as_of_reproduces_state_at_or_before_timestamp(tmp_path):
    db = tmp_path / "agent.db"
    _seed_db(db)

    report = load_report(str(db), as_of="2026-01-01T00:02:30+00:00")

    assert report.current.cycle == 2
    assert report.current.total_value == 10100.0
    assert report.positions[0].quantity == 2.0
    assert report.positions[0].last_price == 250.0
    assert [t.action for t in report.trades] == ["buy"]
    assert [d.action for d in report.decisions] == ["hold", "buy"]


def test_since_filters_recent_trades_and_decisions(tmp_path):
    db = tmp_path / "agent.db"
    _seed_db(db)

    report = load_report(str(db), since="2026-01-01T00:02:30+00:00")

    assert [t.action for t in report.trades] == ["sell"]
    assert [d.action for d in report.decisions] == ["sell"]
    assert report.current.cycle == 3


def test_cli_report_prints_expected_sections(tmp_path, capsys):
    db = tmp_path / "agent.db"
    _seed_db(db)

    assert main(["--db", str(db), "--as-of", "2026-01-01T00:02:30+00:00"]) == 0

    out = capsys.readouterr().out
    assert "Paper Trading Dashboard" in out
    assert "Current Equity" in out
    assert "Open Positions" in out
    assert "Recent Trades" in out
    assert "Recent Decisions" in out
    assert "Equity Summary" in out
    assert "$10,100.00" in out
    assert "AAPL.US" in out
    assert "hold while trend remains intact" in out
    assert "took profit" not in out


def test_render_html_includes_equity_chart(tmp_path):
    db = tmp_path / "agent.db"
    _seed_db(db)

    page = render_html(load_report(str(db)))

    assert "<svg" in page
    assert "<polyline" in page
    assert "AAPL.US" in page
    assert "sell partial after extension" in page


def test_missing_database_is_not_created(tmp_path):
    db = tmp_path / "missing.db"

    report = load_report(str(db))

    assert report.warning
    assert "database not found" in report.warning
    assert not db.exists()


def test_existing_transactions_without_realized_pnl_still_render(tmp_path):
    db = tmp_path / "old_agent.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE transactions (
                id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                cash_after REAL NOT NULL,
                reason TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO transactions (
                id, timestamp, ticker, action, quantity, price, cash_after, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "txn-old",
                "2026-01-01T00:01:00+00:00",
                "AAPL.US",
                "buy",
                1.0,
                200.0,
                9800.0,
                "legacy paper trade",
            ),
        )

    report = load_report(str(db))

    assert report.warning is None
    assert len(report.trades) == 1
    assert report.trades[0].realized_pnl is None


def test_position_snapshot_without_cycle_still_renders(tmp_path):
    db = tmp_path / "agent.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE positions (
                timestamp TEXT NOT NULL,
                cycle INTEGER,
                ticker TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                last_price REAL NOT NULL,
                market_value REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                unrealized_pnl_pct REAL NOT NULL
            );
            INSERT INTO positions (
                timestamp, cycle, ticker, quantity, avg_price, last_price,
                market_value, unrealized_pnl, unrealized_pnl_pct
            ) VALUES (
                '2026-01-01T00:01:00+00:00', NULL, 'AAPL.US', 1.0,
                200.0, 210.0, 210.0, 10.0, 5.0
            );
            """
        )

    report = load_report(str(db))

    assert report.positions[0].ticker == "AAPL.US"
    assert report.positions[0].cycle == 0


def test_web_handler_serves_get_and_rejects_post(tmp_path):
    db = tmp_path / "agent.db"
    _seed_db(db)
    handler = _handler_class(str(db), None, None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/"

    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "<svg" in body

        request = urllib.request.Request(url, method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=5)
        assert exc_info.value.code == 405
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

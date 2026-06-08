"""Offline tests for persisted paper-agent state and dashboard reporting."""

from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent import agent as agent_module
from agent.agent import TradingAgent
from agent.memory import AgentMemory, Decision
from agent.portfolio_state import PaperPortfolio


REQUIRED_TRANSACTION_COLUMNS = {
    "id",
    "timestamp",
    "ticker",
    "action",
    "quantity",
    "price",
    "cash_after",
    "reason",
    "realized_pnl",
}

REQUIRED_EQUITY_COLUMNS = {
    "timestamp",
    "cycle",
    "cash",
    "positions_value",
    "total_value",
    "realized_pnl_cum",
    "unrealized_pnl",
    "pnl_pct",
}

REQUIRED_POSITION_COLUMNS = {
    "timestamp",
    "cycle",
    "ticker",
    "quantity",
    "avg_price",
    "last_price",
    "market_value",
    "unrealized_pnl",
    "unrealized_pnl_pct",
}


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _rows(db_path: Path, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(query, params).fetchall()


def _snapshot_equity(portfolio: PaperPortfolio, cycle: int) -> None:
    snapshot = getattr(portfolio, "snapshot_equity", None) or getattr(
        portfolio, "record_equity_snapshot", None
    )
    assert callable(
        snapshot
    ), "PaperPortfolio needs an explicit per-cycle equity snapshot method"
    snapshot(cycle=cycle)


def test_fresh_paper_db_has_additive_persistence_schema(tmp_path):
    db = tmp_path / "agent.db"

    PaperPortfolio(starting_cash=10_000.0, db_path=str(db))

    assert {"transactions", "positions", "equity"}.issubset(_table_names(db))
    assert REQUIRED_TRANSACTION_COLUMNS.issubset(_columns(db, "transactions"))
    assert REQUIRED_EQUITY_COLUMNS.issubset(_columns(db, "equity"))
    assert REQUIRED_POSITION_COLUMNS.issubset(_columns(db, "positions"))


def test_preexisting_transaction_table_is_migrated_without_data_loss(tmp_path):
    db = tmp_path / "agent.db"
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
            INSERT INTO transactions
                (id, timestamp, ticker, action, quantity, price, cash_after, reason)
            VALUES
                ('legacy-1', '2026-01-01T09:30:00+00:00', 'AAPL.US',
                 'buy', 1.0, 100.0, 9900.0, 'legacy placeholder row')
            """
        )

    PaperPortfolio(starting_cash=10_000.0, db_path=str(db))

    assert {"transactions", "positions", "equity"}.issubset(_table_names(db))
    assert "realized_pnl" in _columns(db, "transactions")
    rows = _rows(db, "SELECT id, reason, realized_pnl FROM transactions")
    assert len(rows) == 1
    assert rows[0]["id"] == "legacy-1"
    assert rows[0]["reason"] == "legacy placeholder row"
    assert rows[0]["realized_pnl"] is None


def test_sell_persists_realized_pnl_to_transactions(tmp_path):
    db = tmp_path / "agent.db"
    portfolio = PaperPortfolio(starting_cash=10_000.0, db_path=str(db))

    portfolio.buy("AAPL.US", quantity=2, price=200.0, reason="placeholder entry")
    fill = portfolio.sell("AAPL.US", quantity=1, price=210.0, reason="placeholder trim")

    assert fill["status"] == "filled"
    rows = _rows(
        db,
        """
        SELECT action, quantity, price, cash_after, realized_pnl
        FROM transactions
        WHERE action = 'sell'
        """,
    )
    assert len(rows) == 1
    assert rows[0]["quantity"] == 1.0
    assert rows[0]["price"] == 210.0
    assert rows[0]["cash_after"] == pytest.approx(9_810.0)
    assert rows[0]["realized_pnl"] == pytest.approx(10.0)


class _FakeStrategy:
    name = "test"
    description = "offline placeholder strategy"
    watchlist = ["AAPL.US"]

    def get_system_prompt(self) -> str:
        return "Hold unless the offline fixture says otherwise."

    def filter_observations(self, observations: dict) -> dict:
        return observations


def test_run_cycle_writes_one_mark_to_market_equity_snapshot(tmp_path, monkeypatch):
    db = tmp_path / "agent.db"
    memory = AgentMemory(db_path=str(db))
    portfolio = PaperPortfolio(starting_cash=10_000.0, db_path=str(db))
    portfolio.buy("AAPL.US", quantity=2, price=200.0, reason="seed placeholder position")

    async def fake_market_status(client):
        return {"any_open": True}

    async def fake_observe_market(client, tickers):
        assert tickers == ["AAPL.US"]
        return {
            "AAPL.US": {
                "quote": {"last": 250.0},
                "candles": [],
                "news": [],
            }
        }

    async def fake_request_decision(*args, **kwargs):
        return """
        <thinking>
        The placeholder position is open and the observed price is higher.
        </thinking>

        <decision>
        action: hold
        ticker: AAPL.US
        quantity: null
        confidence: 0.8
        reasoning: keep the placeholder position open
        </decision>
        """

    monkeypatch.setattr(agent_module, "get_market_status", fake_market_status)
    monkeypatch.setattr(agent_module, "observe_market", fake_observe_market)
    monkeypatch.setattr(agent_module, "request_decision", fake_request_decision)

    trading_agent = TradingAgent(
        client=object(),
        strategy=_FakeStrategy(),
        memory=memory,
        portfolio=portfolio,
        dry_run=False,
        backend="codex",
    )

    decision = asyncio.run(trading_agent.run_cycle())

    assert decision is not None
    rows = _rows(
        db,
        """
        SELECT cycle, cash, positions_value, total_value,
               realized_pnl_cum, unrealized_pnl, pnl_pct
        FROM equity
        WHERE cycle = 1
        """,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["cash"] == pytest.approx(9_600.0)
    assert row["positions_value"] == pytest.approx(500.0)
    assert row["total_value"] == pytest.approx(10_100.0)
    assert row["realized_pnl_cum"] == pytest.approx(0.0)
    assert row["unrealized_pnl"] == pytest.approx(100.0)
    assert row["pnl_pct"] == pytest.approx(1.0)


def test_restart_reloads_cash_positions_and_starting_cash(tmp_path):
    db = tmp_path / "agent.db"
    original = PaperPortfolio(starting_cash=12_500.0, db_path=str(db))
    original.buy("AAPL.US", quantity=2, price=200.0, reason="placeholder entry")
    original.update_prices({"AAPL.US": 250.0})
    _snapshot_equity(original, cycle=1)

    restarted = PaperPortfolio(starting_cash=99_999.0, db_path=str(db))
    state = restarted.get_state()

    assert state["starting_cash"] == pytest.approx(12_500.0)
    assert state["cash"] == pytest.approx(12_100.0)
    assert state["total_value"] == pytest.approx(12_600.0)
    assert state["pnl_pct"] == pytest.approx(0.8)
    assert set(state["positions"]) == {"AAPL.US"}
    position = state["positions"]["AAPL.US"]
    assert position["quantity"] == pytest.approx(2.0)
    assert position["avg_price"] == pytest.approx(200.0)
    assert position["last_price"] == pytest.approx(250.0)
    assert position["market_value"] == pytest.approx(500.0)
    assert position["unrealized_pnl"] == pytest.approx(100.0)


def _seed_dashboard_db(db: Path) -> None:
    AgentMemory(db_path=str(db))
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE equity (
                timestamp TEXT NOT NULL,
                cycle INTEGER NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                total_value REAL NOT NULL,
                realized_pnl_cum REAL NOT NULL,
                unrealized_pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO equity
                (timestamp, cycle, cash, positions_value, total_value,
                 realized_pnl_cum, unrealized_pnl, pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("2026-01-01T09:45:00+00:00", 0, 10_000.0, 0.0, 10_000.0, 0.0, 0.0, 0.0),
                ("2026-01-01T10:15:00+00:00", 1, 9_600.0, 500.0, 10_100.0, 0.0, 100.0, 1.0),
                ("2026-01-01T11:00:00+00:00", 2, 8_900.0, 100.0, 9_000.0, -50.0, -50.0, -10.0),
            ],
        )
        conn.execute(
            """
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
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO positions
                (timestamp, cycle, ticker, quantity, avg_price, last_price,
                 market_value, unrealized_pnl, unrealized_pnl_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "2026-01-01T10:15:00+00:00",
                    1,
                    "AAPL.US",
                    2.0,
                    200.0,
                    250.0,
                    500.0,
                    100.0,
                    25.0,
                ),
                (
                    "2026-01-01T11:00:00+00:00",
                    2,
                    "MSFT.US",
                    1.0,
                    100.0,
                    100.0,
                    100.0,
                    0.0,
                    0.0,
                ),
            ],
        )
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
                reason TEXT,
                realized_pnl REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO transactions
                (id, timestamp, ticker, action, quantity, price,
                 cash_after, reason, realized_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "trade-before-since",
                    "2026-01-01T09:50:00+00:00",
                    "AAPL.US",
                    "buy",
                    2.0,
                    200.0,
                    9_600.0,
                    "before-since placeholder entry",
                    None,
                ),
                (
                    "trade-as-of",
                    "2026-01-01T10:10:00+00:00",
                    "TSLA.US",
                    "sell",
                    1.0,
                    120.0,
                    9_720.0,
                    "as-of placeholder trim",
                    20.0,
                ),
                (
                    "trade-after-as-of",
                    "2026-01-01T11:00:00+00:00",
                    "MSFT.US",
                    "buy",
                    1.0,
                    100.0,
                    8_900.0,
                    "late placeholder entry",
                    None,
                ),
            ],
        )

    memory = AgentMemory(db_path=str(db))
    memory.save(
        Decision(
            timestamp="2026-01-01T10:05:00+00:00",
            cycle=1,
            action="hold",
            ticker="AAPL.US",
            quantity=None,
            price_at_decision=250.0,
            thinking="placeholder thinking",
            reasoning_summary="as-of hold reasoning",
            confidence=0.7,
        )
    )
    memory.save(
        Decision(
            timestamp="2026-01-01T11:05:00+00:00",
            cycle=2,
            action="buy",
            ticker="MSFT.US",
            quantity=1.0,
            price_at_decision=100.0,
            thinking="late placeholder thinking",
            reasoning_summary="late decision reasoning",
            confidence=0.9,
        )
    )


def test_dashboard_cli_seeded_db_respects_as_of_and_since(tmp_path):
    if importlib.util.find_spec("agent.dashboard") is None:
        pytest.skip("agent.dashboard is not implemented yet")

    db = tmp_path / "agent.db"
    _seed_dashboard_db(db)
    repo_root = Path(__file__).resolve().parents[1]

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.dashboard",
            "--db",
            str(db),
            "--as-of",
            "2026-01-01T10:30:00+00:00",
            "--since",
            "2026-01-01T10:00:00+00:00",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    numeric_output = output.replace(",", "")
    assert "10100" in numeric_output
    assert "AAPL.US" in output
    assert "TSLA.US" in output
    assert "as-of hold reasoning" in output
    assert "before-since placeholder entry" not in output
    assert "MSFT.US" not in output
    assert "late decision reasoning" not in output

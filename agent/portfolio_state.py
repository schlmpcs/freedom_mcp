"""Paper-trading portfolio tracker.

Tracks a paper-only book (no real broker orders are ever placed) and enforces
basic risk discipline:

* a single new buy may not exceed 5% of total portfolio value,
* you cannot sell more than you hold,
* you cannot spend cash you do not have.

If a SQLite ``db_path`` is supplied, accepted transactions, position snapshots
and once-per-cycle equity snapshots are persisted alongside the agent's
decision log so the paper book survives restarts.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

MAX_POSITION_FRACTION = 0.05  # 5% of total portfolio value per new position


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperPortfolio:
    """Paper portfolio with risk-rule enforcement and optional persistence."""

    def __init__(self, starting_cash: float = 10_000.0, db_path: Optional[str] = None) -> None:
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        # ticker -> {"quantity": float, "avg_price": float, "last_price": float}
        self.positions: dict[str, dict[str, float]] = {}
        self._equity_curve: list[dict] = []
        self.realized_pnl_cum = 0.0
        self._last_cycle = 0
        self.db_path = db_path
        if db_path:
            self._ensure_schema()
            self._load_from_db(default_starting_cash=float(starting_cash))
        self._record_equity()  # initial in-memory point

    # -- persistence --------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        # SQLite won't create missing parent directories; ensure they exist.
        parent = os.path.dirname(self.db_path or "")
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transactions (
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
            self._add_column_if_missing(conn, "transactions", "realized_pnl", "REAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    cycle INTEGER,
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_positions_ticker_time "
                "ON positions (ticker, timestamp)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS equity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    cycle INTEGER NOT NULL,
                    cash REAL NOT NULL,
                    positions_value REAL NOT NULL,
                    total_value REAL NOT NULL,
                    realized_pnl_cum REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    starting_cash REAL
                )
                """
            )
            self._add_column_if_missing(conn, "equity", "starting_cash", "REAL")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_equity_time ON equity (timestamp, id)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portfolio_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _add_column_if_missing(
        conn: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _load_from_db(self, default_starting_cash: float) -> None:
        """Restore cash, open positions and cumulative realised P&L if present."""
        with self._connect() as conn:
            meta_starting_cash = self._get_meta_float(conn, "starting_cash")
            if meta_starting_cash is not None:
                self.starting_cash = meta_starting_cash

            latest_equity = conn.execute(
                "SELECT * FROM equity ORDER BY timestamp DESC, rowid DESC LIMIT 1"
            ).fetchone()
            latest_positions = self._latest_positions_from_rows(
                conn.execute("SELECT * FROM positions ORDER BY timestamp ASC, rowid ASC").fetchall()
            )
            transaction_rows = conn.execute(
                "SELECT * FROM transactions ORDER BY timestamp ASC, rowid ASC"
            ).fetchall()

            if latest_equity is not None:
                if latest_equity["starting_cash"] is not None:
                    self.starting_cash = float(latest_equity["starting_cash"])
                self.cash = float(latest_equity["cash"])
                self.realized_pnl_cum = float(latest_equity["realized_pnl_cum"])
                self._last_cycle = int(latest_equity["cycle"] or 0)
                self.positions = latest_positions
                self._save_meta(conn, "starting_cash", self.starting_cash)
                return

            if transaction_rows:
                if meta_starting_cash is None:
                    self.starting_cash = default_starting_cash
                self._replay_transactions(transaction_rows)
                self._last_cycle = self._latest_cycle_from_positions(conn)
                self._save_meta(conn, "starting_cash", self.starting_cash)
                return

            if latest_positions:
                self.positions = latest_positions
                self.cash = self.starting_cash
                self._last_cycle = self._latest_cycle_from_positions(conn)

            self._save_meta(conn, "starting_cash", self.starting_cash)

    @staticmethod
    def _get_meta_float(conn: sqlite3.Connection, key: str) -> Optional[float]:
        row = conn.execute("SELECT value FROM portfolio_meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            return float(row["value"])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _save_meta(conn: sqlite3.Connection, key: str, value: float) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO portfolio_meta (key, value) VALUES (?, ?)",
            (key, str(float(value))),
        )

    @staticmethod
    def _latest_cycle_from_positions(conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT MAX(cycle) AS cycle FROM positions").fetchone()
        return int(row["cycle"] or 0) if row is not None else 0

    @staticmethod
    def _latest_positions_from_rows(rows: list[sqlite3.Row]) -> dict[str, dict[str, float]]:
        latest: dict[str, sqlite3.Row] = {}
        for row in rows:
            latest[row["ticker"]] = row

        positions: dict[str, dict[str, float]] = {}
        for ticker, row in latest.items():
            quantity = float(row["quantity"] or 0.0)
            if quantity <= 1e-9:
                continue
            positions[ticker] = {
                "quantity": quantity,
                "avg_price": float(row["avg_price"] or 0.0),
                "last_price": float(row["last_price"] or row["avg_price"] or 0.0),
            }
        return positions

    def _replay_transactions(self, rows: list[sqlite3.Row]) -> None:
        """Rebuild state from older databases that only persisted transactions."""
        self.cash = self.starting_cash
        self.positions = {}
        self.realized_pnl_cum = 0.0

        for row in rows:
            ticker = row["ticker"]
            action = str(row["action"]).lower()
            quantity = float(row["quantity"] or 0.0)
            price = float(row["price"] or 0.0)
            if quantity <= 0 or price <= 0:
                continue

            if action == "buy":
                cost = quantity * price
                pos = self.positions.get(ticker)
                if pos:
                    new_qty = pos["quantity"] + quantity
                    pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + cost) / new_qty
                    pos["quantity"] = new_qty
                    pos["last_price"] = price
                else:
                    self.positions[ticker] = {
                        "quantity": quantity,
                        "avg_price": price,
                        "last_price": price,
                    }
                self.cash -= cost
            elif action == "sell":
                pos = self.positions.get(ticker)
                avg_price = pos["avg_price"] if pos else price
                realized_pnl = row["realized_pnl"]
                realized = (
                    float(realized_pnl)
                    if realized_pnl is not None
                    else (price - avg_price) * quantity
                )
                self.realized_pnl_cum += realized
                if pos:
                    pos["quantity"] -= quantity
                    pos["last_price"] = price
                    if pos["quantity"] <= 1e-9:
                        del self.positions[ticker]
                self.cash += quantity * price

            if row["cash_after"] is not None:
                self.cash = float(row["cash_after"])

    def _log_transaction(
        self,
        order_id: str,
        timestamp: str,
        ticker: str,
        action: str,
        quantity: float,
        price: float,
        reason: str,
        realized_pnl: Optional[float] = None,
    ) -> None:
        if not self.db_path:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO transactions "
                "(id, timestamp, ticker, action, quantity, price, cash_after, reason, realized_pnl) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    order_id,
                    timestamp,
                    ticker,
                    action,
                    quantity,
                    price,
                    self.cash,
                    reason,
                    realized_pnl,
                ),
            )

    def _persist_position_snapshot(
        self,
        ticker: str,
        timestamp: str,
        cycle: Optional[int] = None,
        position: Optional[dict[str, float]] = None,
    ) -> None:
        if not self.db_path:
            return
        pos = position if position is not None else self.positions.get(ticker)
        if pos is None:
            return
        quantity = float(pos.get("quantity", 0.0))
        avg_price = float(pos.get("avg_price", 0.0))
        last_price = float(pos.get("last_price") or avg_price or 0.0)
        market_value = quantity * last_price
        cost_basis = quantity * avg_price
        unrealized = market_value - cost_basis
        unrealized_pct = (unrealized / cost_basis * 100) if cost_basis else 0.0
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO positions "
                "(timestamp, cycle, ticker, quantity, avg_price, last_price, "
                "market_value, unrealized_pnl, unrealized_pnl_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    cycle,
                    ticker,
                    quantity,
                    avg_price,
                    last_price,
                    market_value,
                    unrealized,
                    unrealized_pct,
                ),
            )

    def _persist_all_positions(self, timestamp: str, cycle: int) -> None:
        for ticker in sorted(self.positions):
            self._persist_position_snapshot(ticker, timestamp, cycle=cycle)

    # -- valuation ----------------------------------------------------------
    def _position_value(self, pos: dict[str, float]) -> float:
        price = pos.get("last_price") or pos.get("avg_price") or 0.0
        return price * pos.get("quantity", 0.0)

    def total_value(self) -> float:
        """Cash plus the marked-to-market value of all positions."""
        return self.cash + sum(self._position_value(p) for p in self.positions.values())

    def _metrics(self) -> dict[str, float]:
        positions_value = sum(self._position_value(p) for p in self.positions.values())
        unrealized = 0.0
        for pos in self.positions.values():
            last_price = pos.get("last_price") or pos.get("avg_price") or 0.0
            unrealized += (last_price - pos.get("avg_price", 0.0)) * pos.get("quantity", 0.0)
        total = self.cash + positions_value
        pnl_pct = ((total - self.starting_cash) / self.starting_cash * 100) if self.starting_cash else 0.0
        return {
            "cash": self.cash,
            "positions_value": positions_value,
            "total_value": total,
            "realized_pnl_cum": self.realized_pnl_cum,
            "unrealized_pnl": unrealized,
            "pnl_pct": pnl_pct,
        }

    def _record_equity(self, timestamp: Optional[str] = None, cycle: Optional[int] = None) -> None:
        metrics = self._metrics()
        row = {
            "timestamp": timestamp or _now_iso(),
            "cycle": cycle,
            "cash": round(metrics["cash"], 4),
            "positions_value": round(metrics["positions_value"], 4),
            "total_value": round(metrics["total_value"], 4),
            "realized_pnl_cum": round(metrics["realized_pnl_cum"], 4),
            "unrealized_pnl": round(metrics["unrealized_pnl"], 4),
            "pnl_pct": round(metrics["pnl_pct"], 4),
        }
        self._equity_curve.append(row)

    def record_equity_snapshot(self, cycle: int) -> dict:
        """Persist a once-per-cycle mark-to-market equity snapshot."""
        timestamp = _now_iso()
        metrics = self._metrics()
        row = {
            "timestamp": timestamp,
            "cycle": int(cycle),
            "cash": round(metrics["cash"], 4),
            "positions_value": round(metrics["positions_value"], 4),
            "total_value": round(metrics["total_value"], 4),
            "realized_pnl_cum": round(metrics["realized_pnl_cum"], 4),
            "unrealized_pnl": round(metrics["unrealized_pnl"], 4),
            "pnl_pct": round(metrics["pnl_pct"], 4),
            "starting_cash": round(self.starting_cash, 4),
        }
        self._equity_curve.append(dict(row))
        self._last_cycle = max(self._last_cycle, int(cycle))
        if self.db_path:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO equity "
                    "(timestamp, cycle, cash, positions_value, total_value, "
                    "realized_pnl_cum, unrealized_pnl, pnl_pct, starting_cash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        row["timestamp"],
                        row["cycle"],
                        row["cash"],
                        row["positions_value"],
                        row["total_value"],
                        row["realized_pnl_cum"],
                        row["unrealized_pnl"],
                        row["pnl_pct"],
                        row["starting_cash"],
                    ),
                )
                self._save_meta(conn, "starting_cash", self.starting_cash)
            self._persist_all_positions(timestamp, int(cycle))
        return row

    def get_latest_cycle(self) -> int:
        """Return the latest persisted cycle number known to the portfolio."""
        return self._last_cycle

    # -- trading ------------------------------------------------------------
    def buy(self, ticker: str, quantity: float, price: float, reason: str = "") -> dict:
        """Buy ``quantity`` of ``ticker`` at ``price`` against cash.

        Rejected (``status="rejected"``) if it would overspend cash or exceed the
        5% single-position limit. On success returns a fill confirmation.
        """
        if quantity <= 0 or price <= 0:
            return {"status": "rejected", "reason": "quantity and price must be positive"}

        cost = quantity * price
        if cost > self.cash + 1e-9:
            return {
                "status": "rejected",
                "reason": f"insufficient cash: need {cost:.2f}, have {self.cash:.2f}",
            }

        limit = self.total_value() * MAX_POSITION_FRACTION
        if cost > limit + 1e-9:
            return {
                "status": "rejected",
                "reason": (
                    f"position size {cost:.2f} exceeds {MAX_POSITION_FRACTION:.0%} "
                    f"limit ({limit:.2f}) of total portfolio value"
                ),
            }

        pos = self.positions.get(ticker)
        if pos:
            new_qty = pos["quantity"] + quantity
            pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + cost) / new_qty
            pos["quantity"] = new_qty
            pos["last_price"] = price
        else:
            self.positions[ticker] = {
                "quantity": quantity,
                "avg_price": price,
                "last_price": price,
            }

        self.cash -= cost
        order_id = str(uuid.uuid4())
        timestamp = _now_iso()
        self._log_transaction(order_id, timestamp, ticker, "buy", quantity, price, reason)
        self._persist_position_snapshot(ticker, timestamp)
        self._record_equity()
        return {
            "status": "filled",
            "order_id": order_id,
            "ticker": ticker,
            "action": "buy",
            "quantity": quantity,
            "price": price,
            "cost": round(cost, 4),
            "cash": round(self.cash, 4),
        }

    def sell(self, ticker: str, quantity: float, price: float, reason: str = "") -> dict:
        """Sell ``quantity`` of ``ticker`` at ``price``.

        Rejected if the position does not exist or holds fewer shares than
        requested. Realised P&L vs the average cost is returned on success.
        """
        if quantity <= 0 or price <= 0:
            return {"status": "rejected", "reason": "quantity and price must be positive"}

        pos = self.positions.get(ticker)
        if not pos or pos["quantity"] <= 0:
            return {"status": "rejected", "reason": f"no open position in {ticker}"}
        if quantity > pos["quantity"] + 1e-9:
            return {
                "status": "rejected",
                "reason": f"cannot sell {quantity}; only {pos['quantity']} held",
            }

        proceeds = quantity * price
        avg_price = pos["avg_price"]
        realized_pnl = (price - avg_price) * quantity
        pos["quantity"] -= quantity
        pos["last_price"] = price
        snapshot_pos = {
            "quantity": max(pos["quantity"], 0.0),
            "avg_price": avg_price,
            "last_price": price,
        }
        if pos["quantity"] <= 1e-9:
            del self.positions[ticker]

        self.cash += proceeds
        self.realized_pnl_cum += realized_pnl
        order_id = str(uuid.uuid4())
        timestamp = _now_iso()
        self._log_transaction(
            order_id,
            timestamp,
            ticker,
            "sell",
            quantity,
            price,
            reason,
            realized_pnl=realized_pnl,
        )
        self._persist_position_snapshot(ticker, timestamp, position=snapshot_pos)
        self._record_equity()
        return {
            "status": "filled",
            "order_id": order_id,
            "ticker": ticker,
            "action": "sell",
            "quantity": quantity,
            "price": price,
            "proceeds": round(proceeds, 4),
            "realized_pnl": round(realized_pnl, 4),
            "cash": round(self.cash, 4),
        }

    # -- mark-to-market & queries ------------------------------------------
    def update_prices(self, price_map: dict[str, float]) -> None:
        """Update ``last_price`` for held tickers and record an in-memory equity point."""
        changed = False
        for ticker, price in price_map.items():
            pos = self.positions.get(ticker)
            if pos and price and price > 0:
                pos["last_price"] = float(price)
                changed = True
        if changed:
            self._record_equity()

    def get_position(self, ticker: str) -> Optional[dict]:
        """Return a detailed view of one position, or ``None`` if not held."""
        pos = self.positions.get(ticker)
        if not pos:
            return None
        return self._position_view(ticker, pos)

    def _position_view(self, ticker: str, pos: dict[str, float]) -> dict:
        last_price = pos.get("last_price") or pos.get("avg_price") or 0.0
        market_value = last_price * pos["quantity"]
        cost_basis = pos["avg_price"] * pos["quantity"]
        unrealized = market_value - cost_basis
        return {
            "ticker": ticker,
            "quantity": pos["quantity"],
            "avg_price": round(pos["avg_price"], 4),
            "last_price": round(last_price, 4),
            "market_value": round(market_value, 4),
            "unrealized_pnl": round(unrealized, 4),
            "unrealized_pnl_pct": round((unrealized / cost_basis * 100) if cost_basis else 0.0, 4),
        }

    def get_state(self) -> dict:
        """Return the full portfolio snapshot."""
        positions = {t: self._position_view(t, p) for t, p in self.positions.items()}
        metrics = self._metrics()
        return {
            "cash": round(metrics["cash"], 4),
            "starting_cash": round(self.starting_cash, 4),
            "positions": positions,
            "positions_value": round(metrics["positions_value"], 4),
            "total_value": round(metrics["total_value"], 4),
            "realized_pnl_cum": round(metrics["realized_pnl_cum"], 4),
            "unrealized_pnl": round(metrics["unrealized_pnl"], 4),
            "pnl_pct": round(metrics["pnl_pct"], 4),
        }

    def get_equity_curve(self) -> list[dict]:
        """Return the recorded {timestamp, total_value} points."""
        return list(self._equity_curve)

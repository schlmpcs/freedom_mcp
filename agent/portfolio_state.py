"""Paper-trading portfolio tracker.

Holds cash and positions entirely in memory (no real broker orders are ever
placed) and enforces basic risk discipline:

* a single new buy may not exceed 5% of total portfolio value,
* you cannot sell more than you hold,
* you cannot spend cash you do not have.

Every accepted transaction is appended to an equity curve and, if a SQLite
``db_path`` is supplied, logged to a ``transactions`` table alongside the
agent's decision log.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

MAX_POSITION_FRACTION = 0.05  # 5% of total portfolio value per new position


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperPortfolio:
    """In-memory paper portfolio with risk-rule enforcement."""

    def __init__(self, starting_cash: float = 10_000.0, db_path: Optional[str] = None) -> None:
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        # ticker -> {"quantity": float, "avg_price": float, "last_price": float}
        self.positions: dict[str, dict[str, float]] = {}
        self._equity_curve: list[dict] = []
        self.db_path = db_path
        if db_path:
            self._ensure_schema()
        self._record_equity()  # initial point

    # -- persistence --------------------------------------------------------
    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
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
                    reason TEXT
                )
                """
            )

    def _log_transaction(
        self, order_id: str, ticker: str, action: str, quantity: float, price: float, reason: str
    ) -> None:
        if not self.db_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO transactions "
                "(id, timestamp, ticker, action, quantity, price, cash_after, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (order_id, _now_iso(), ticker, action, quantity, price, self.cash, reason),
            )

    # -- valuation ----------------------------------------------------------
    def _position_value(self, pos: dict[str, float]) -> float:
        price = pos.get("last_price") or pos.get("avg_price") or 0.0
        return price * pos.get("quantity", 0.0)

    def total_value(self) -> float:
        """Cash plus the marked-to-market value of all positions."""
        return self.cash + sum(self._position_value(p) for p in self.positions.values())

    def _record_equity(self) -> None:
        self._equity_curve.append(
            {"timestamp": _now_iso(), "total_value": round(self.total_value(), 4)}
        )

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
        self._log_transaction(order_id, ticker, "buy", quantity, price, reason)
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
        realized_pnl = (price - pos["avg_price"]) * quantity
        pos["quantity"] -= quantity
        pos["last_price"] = price
        if pos["quantity"] <= 1e-9:
            del self.positions[ticker]

        self.cash += proceeds
        order_id = str(uuid.uuid4())
        self._log_transaction(order_id, ticker, "sell", quantity, price, reason)
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
        """Update ``last_price`` for held tickers and record an equity point."""
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
        total = self.total_value()
        return {
            "cash": round(self.cash, 4),
            "starting_cash": round(self.starting_cash, 4),
            "positions": positions,
            "total_value": round(total, 4),
            "pnl_pct": round((total - self.starting_cash) / self.starting_cash * 100, 4)
            if self.starting_cash
            else 0.0,
        }

    def get_equity_curve(self) -> list[dict]:
        """Return the recorded {timestamp, total_value} points."""
        return list(self._equity_curve)

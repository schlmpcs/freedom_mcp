"""Persistent decision log for the trading agent.

Every decision the agent makes is stored permanently in a SQLite database so the
agent can recall its recent history (and the eval harness can attach scores).
Outcomes (the price/return realised after a decision) are filled in later via
:meth:`AgentMemory.update_outcome`.

The store is deliberately simple: each method opens its own short-lived SQLite
connection, which keeps it safe to call from worker threads (e.g. via
``asyncio.to_thread``) without sharing a connection across threads.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

DEFAULT_DB_PATH = "logs/agent.db"


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Return a fresh decision id."""
    return str(uuid.uuid4())


@dataclass
class Decision:
    """A single observe→reason→act decision, plus outcome/judge fields.

    The outcome fields (``outcome_price``, ``outcome_return_pct``) and
    ``judge_scores`` start as ``None`` and are populated later once the result of
    the decision is known or it has been evaluated.
    """

    action: str  # buy | sell | hold | nothing
    thinking: str  # full <thinking> block from the model
    reasoning_summary: str  # one-sentence rationale
    confidence: float  # 0.0-1.0, self-reported by the agent
    cycle: int = 0  # which run-loop iteration produced this
    ticker: Optional[str] = None
    quantity: Optional[float] = None
    price_at_decision: Optional[float] = None
    paper_order_id: Optional[str] = None
    outcome_price: Optional[float] = None
    outcome_return_pct: Optional[float] = None
    judge_scores: Optional[dict] = None
    id: str = field(default_factory=_new_id)
    timestamp: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain-dict view (judge_scores kept as a dict)."""
        return asdict(self)


# Column order used for both INSERT and SELECT so row<->dataclass stays aligned.
_COLUMNS = [
    "id",
    "timestamp",
    "cycle",
    "ticker",
    "action",
    "quantity",
    "price_at_decision",
    "thinking",
    "reasoning_summary",
    "confidence",
    "paper_order_id",
    "outcome_price",
    "outcome_return_pct",
    "judge_scores",
]


class AgentMemory:
    """SQLite-backed permanent log of agent decisions."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        self._ensure_schema()

    # -- schema -------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS decisions (
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
                    paper_order_id TEXT,
                    outcome_price REAL,
                    outcome_return_pct REAL,
                    judge_scores TEXT
                )
                """
            )

    # -- (de)serialisation --------------------------------------------------
    @staticmethod
    def _row_to_decision(row: sqlite3.Row) -> Decision:
        data = dict(row)
        raw_scores = data.pop("judge_scores", None)
        judge_scores = json.loads(raw_scores) if raw_scores else None
        return Decision(judge_scores=judge_scores, **data)

    # -- writes -------------------------------------------------------------
    def save(self, decision: Decision) -> None:
        """Insert (or replace) a decision row."""
        values = [
            decision.id,
            decision.timestamp,
            decision.cycle,
            decision.ticker,
            decision.action,
            decision.quantity,
            decision.price_at_decision,
            decision.thinking,
            decision.reasoning_summary,
            decision.confidence,
            decision.paper_order_id,
            decision.outcome_price,
            decision.outcome_return_pct,
            json.dumps(decision.judge_scores) if decision.judge_scores is not None else None,
        ]
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        with self._connect() as conn:
            conn.execute(
                f"INSERT OR REPLACE INTO decisions ({', '.join(_COLUMNS)}) "
                f"VALUES ({placeholders})",
                values,
            )

    def update_outcome(
        self,
        decision_id: str,
        outcome_price: float,
        outcome_return_pct: float,
    ) -> None:
        """Backfill the realised price/return for a stored decision."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE decisions SET outcome_price = ?, outcome_return_pct = ? "
                "WHERE id = ?",
                (outcome_price, outcome_return_pct, decision_id),
            )

    def update_judge_scores(self, decision_id: str, judge_scores: dict) -> None:
        """Attach judge scores to a stored decision (used by the eval harness)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE decisions SET judge_scores = ? WHERE id = ?",
                (json.dumps(judge_scores), decision_id),
            )

    # -- reads --------------------------------------------------------------
    def get_recent(self, n: int) -> list[Decision]:
        """Return the ``n`` most recent decisions, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def get_all(self) -> list[Decision]:
        """Return every stored decision, oldest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY timestamp ASC, rowid ASC"
            ).fetchall()
        return [self._row_to_decision(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        """Return aggregate stats over all decisions.

        ``win_rate`` and ``avg_return`` are computed only over decisions that
        have a recorded ``outcome_return_pct``; ``avg_confidence`` is over all.
        """
        decisions = self.get_all()
        total = len(decisions)
        with_outcome = [d for d in decisions if d.outcome_return_pct is not None]
        wins = [d for d in with_outcome if (d.outcome_return_pct or 0) > 0]
        confidences = [d.confidence for d in decisions if d.confidence is not None]
        action_counts: dict[str, int] = {}
        for d in decisions:
            action_counts[d.action] = action_counts.get(d.action, 0) + 1
        return {
            "total_decisions": total,
            "decisions_with_outcome": len(with_outcome),
            "win_rate": (len(wins) / len(with_outcome)) if with_outcome else None,
            "avg_return_pct": (
                sum(d.outcome_return_pct for d in with_outcome) / len(with_outcome)  # type: ignore[misc]
                if with_outcome
                else None
            ),
            "avg_confidence": (sum(confidences) / len(confidences)) if confidences else None,
            "action_counts": action_counts,
        }

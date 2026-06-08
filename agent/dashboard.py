"""Read-only dashboard/report for the autonomous paper-trading agent."""

from __future__ import annotations

import argparse
import html
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable, Optional
from urllib.parse import parse_qs, quote, urlparse

from agent.memory import DEFAULT_DB_PATH


@dataclass
class EquityPoint:
    timestamp: str
    cycle: int
    cash: float
    positions_value: float
    total_value: float
    realized_pnl_cum: float
    unrealized_pnl: float
    pnl_pct: float


@dataclass
class PositionRow:
    timestamp: str
    cycle: int
    ticker: str
    quantity: float
    avg_price: float
    last_price: float
    market_value: float
    unrealized_pnl: float
    unrealized_pnl_pct: float


@dataclass
class TradeRow:
    timestamp: str
    ticker: str
    action: str
    quantity: float
    price: float
    cash_after: float
    reason: str = ""
    realized_pnl: Optional[float] = None


@dataclass
class DecisionRow:
    timestamp: str
    cycle: int
    ticker: str
    action: str
    quantity: Optional[float]
    price_at_decision: Optional[float]
    reasoning_summary: str
    confidence: Optional[float]
    paper_order_id: Optional[str] = None


@dataclass
class EquitySummary:
    start_value: Optional[float] = None
    current_value: Optional[float] = None
    peak_value: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    start_timestamp: Optional[str] = None
    current_timestamp: Optional[str] = None


@dataclass
class DashboardReport:
    db_path: str
    as_of: Optional[str]
    since: Optional[str]
    equity_curve: list[EquityPoint] = field(default_factory=list)
    positions: list[PositionRow] = field(default_factory=list)
    trades: list[TradeRow] = field(default_factory=list)
    decisions: list[DecisionRow] = field(default_factory=list)
    warning: Optional[str] = None

    @property
    def current(self) -> Optional[EquityPoint]:
        return self.equity_curve[-1] if self.equity_curve else None

    @property
    def summary(self) -> EquitySummary:
        return _summarize_equity(self.equity_curve)

    @property
    def total_pnl(self) -> Optional[float]:
        if not self.current:
            return None
        return self.current.realized_pnl_cum + self.current.unrealized_pnl


def _normalize_iso(value: Optional[str]) -> Optional[str]:
    """Return a comparable ISO timestamp, treating naive inputs as UTC."""
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    parseable = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(parseable)
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _arg_iso(value: str) -> str:
    try:
        return _normalize_iso(value) or value
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _readonly_uri(db_path: str) -> str:
    path = os.path.abspath(db_path)
    return f"file:{quote(path, safe='/:')}?mode=ro"


def _connect_readonly(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(_readonly_uri(db_path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def load_report(
    db_path: str = DEFAULT_DB_PATH,
    as_of: Optional[str] = None,
    since: Optional[str] = None,
    limit: int = 10,
) -> DashboardReport:
    """Load a read-only report from the agent SQLite database."""
    as_of = _normalize_iso(as_of)
    since = _normalize_iso(since)
    if not os.path.exists(db_path):
        return DashboardReport(
            db_path=db_path,
            as_of=as_of,
            since=since,
            warning=f"database not found: {db_path}",
        )

    try:
        with _connect_readonly(db_path) as conn:
            return DashboardReport(
                db_path=db_path,
                as_of=as_of,
                since=since,
                equity_curve=_fetch_equity(conn, as_of),
                positions=_fetch_positions(conn, as_of),
                trades=_fetch_trades(conn, as_of, since, limit),
                decisions=_fetch_decisions(conn, as_of, since, limit),
            )
    except sqlite3.Error as exc:
        return DashboardReport(
            db_path=db_path,
            as_of=as_of,
            since=since,
            warning=f"could not read database: {exc}",
        )


def _fetch_equity(conn: sqlite3.Connection, as_of: Optional[str]) -> list[EquityPoint]:
    if not _table_exists(conn, "equity"):
        return []
    where = "WHERE timestamp <= ?" if as_of else ""
    params: tuple[str, ...] = (as_of,) if as_of else ()
    rows = conn.execute(
        f"""
        SELECT timestamp, cycle, cash, positions_value, total_value,
               realized_pnl_cum, unrealized_pnl, pnl_pct
        FROM equity
        {where}
        ORDER BY timestamp ASC, cycle ASC, rowid ASC
        """,
        params,
    ).fetchall()
    return [
        EquityPoint(
            timestamp=str(row["timestamp"]),
            cycle=int(row["cycle"]),
            cash=float(row["cash"] or 0.0),
            positions_value=float(row["positions_value"] or 0.0),
            total_value=float(row["total_value"] or 0.0),
            realized_pnl_cum=float(row["realized_pnl_cum"] or 0.0),
            unrealized_pnl=float(row["unrealized_pnl"] or 0.0),
            pnl_pct=float(row["pnl_pct"] or 0.0),
        )
        for row in rows
    ]


def _fetch_positions(conn: sqlite3.Connection, as_of: Optional[str]) -> list[PositionRow]:
    if not _table_exists(conn, "positions"):
        return []
    where = "WHERE timestamp <= ?" if as_of else ""
    params: tuple[str, ...] = (as_of,) if as_of else ()
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT timestamp, cycle, ticker, quantity, avg_price, last_price,
                   market_value, unrealized_pnl, unrealized_pnl_pct,
                   ROW_NUMBER() OVER (
                       PARTITION BY ticker
                       ORDER BY timestamp DESC, cycle DESC, rowid DESC
                   ) AS rn
            FROM positions
            {where}
        )
        SELECT timestamp, cycle, ticker, quantity, avg_price, last_price,
               market_value, unrealized_pnl, unrealized_pnl_pct
        FROM ranked
        WHERE rn = 1 AND quantity > 0
        ORDER BY ticker ASC
        """,
        params,
    ).fetchall()
    return [
        PositionRow(
            timestamp=str(row["timestamp"]),
            cycle=int(row["cycle"] or 0),
            ticker=str(row["ticker"]),
            quantity=float(row["quantity"] or 0.0),
            avg_price=float(row["avg_price"] or 0.0),
            last_price=float(row["last_price"] or 0.0),
            market_value=float(row["market_value"] or 0.0),
            unrealized_pnl=float(row["unrealized_pnl"] or 0.0),
            unrealized_pnl_pct=float(row["unrealized_pnl_pct"] or 0.0),
        )
        for row in rows
    ]


def _time_filters(as_of: Optional[str], since: Optional[str]) -> tuple[str, list[str]]:
    clauses: list[str] = []
    params: list[str] = []
    if as_of:
        clauses.append("timestamp <= ?")
        params.append(as_of)
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), params


def _fetch_trades(
    conn: sqlite3.Connection,
    as_of: Optional[str],
    since: Optional[str],
    limit: int,
) -> list[TradeRow]:
    if not _table_exists(conn, "transactions"):
        return []
    where, params = _time_filters(as_of, since)
    columns = _table_columns(conn, "transactions")
    realized_expr = "realized_pnl" if "realized_pnl" in columns else "NULL AS realized_pnl"
    rows = conn.execute(
        f"""
        SELECT timestamp, ticker, action, quantity, price, cash_after, reason,
               {realized_expr}
        FROM transactions
        {where}
        ORDER BY timestamp DESC, rowid DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [
        TradeRow(
            timestamp=str(row["timestamp"]),
            ticker=str(row["ticker"]),
            action=str(row["action"]),
            quantity=float(row["quantity"] or 0.0),
            price=float(row["price"] or 0.0),
            cash_after=float(row["cash_after"] or 0.0),
            reason=str(row["reason"] or ""),
            realized_pnl=float(row["realized_pnl"]) if row["realized_pnl"] is not None else None,
        )
        for row in rows
    ]


def _fetch_decisions(
    conn: sqlite3.Connection,
    as_of: Optional[str],
    since: Optional[str],
    limit: int,
) -> list[DecisionRow]:
    if not _table_exists(conn, "decisions"):
        return []
    where, params = _time_filters(as_of, since)
    rows = conn.execute(
        f"""
        SELECT timestamp, cycle, ticker, action, quantity, price_at_decision,
               COALESCE(reasoning_summary, thinking, '') AS reasoning_summary,
               confidence, paper_order_id
        FROM decisions
        {where}
        ORDER BY timestamp DESC, rowid DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()
    return [
        DecisionRow(
            timestamp=str(row["timestamp"]),
            cycle=int(row["cycle"]),
            ticker=str(row["ticker"] or "-"),
            action=str(row["action"]),
            quantity=float(row["quantity"]) if row["quantity"] is not None else None,
            price_at_decision=(
                float(row["price_at_decision"]) if row["price_at_decision"] is not None else None
            ),
            reasoning_summary=str(row["reasoning_summary"] or ""),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            paper_order_id=str(row["paper_order_id"]) if row["paper_order_id"] else None,
        )
        for row in rows
    ]


def _summarize_equity(points: list[EquityPoint]) -> EquitySummary:
    if not points:
        return EquitySummary()
    peak = points[0].total_value
    max_drawdown = 0.0
    peak_value = peak
    for point in points:
        if point.total_value > peak:
            peak = point.total_value
            peak_value = point.total_value
        if peak:
            drawdown = (point.total_value - peak) / peak * 100
            max_drawdown = min(max_drawdown, drawdown)
    return EquitySummary(
        start_value=points[0].total_value,
        current_value=points[-1].total_value,
        peak_value=peak_value,
        max_drawdown_pct=max_drawdown,
        start_timestamp=points[0].timestamp,
        current_timestamp=points[-1].timestamp,
    )


def _money(value: Optional[float], signed: bool = False) -> str:
    if value is None:
        return "-"
    sign = ""
    if signed and value > 0:
        sign = "+"
    elif value < 0:
        sign = "-"
    return f"{sign}${abs(value):,.2f}"


def _pct(value: Optional[float], signed: bool = False) -> str:
    if value is None:
        return "-"
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value:,.2f}%"


def _qty(value: Optional[float]) -> str:
    if value is None:
        return "-"
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text or "0"


def _clip(value: str, length: int = 80) -> str:
    value = " ".join(value.split())
    if len(value) <= length:
        return value
    return value[: length - 3].rstrip() + "..."


def _format_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["  (none)"]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  " + "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  " + "  ".join("-" * width for width in widths))
    for row in rows:
        lines.append("  " + "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return lines


def render_text(report: DashboardReport) -> str:
    """Render the report as terminal-friendly text."""
    lines: list[str] = ["Paper Trading Dashboard", f"DB: {report.db_path}"]
    if report.as_of:
        lines.append(f"As of: {report.as_of}")
    if report.since:
        lines.append(f"Since: {report.since}")
    if report.warning:
        lines.extend(["", f"Warning: {report.warning}"])

    current = report.current
    total_pnl = report.total_pnl
    lines.extend(["", "Current Equity"])
    if current:
        lines.extend(
            [
                f"  Total Value:     {_money(current.total_value)}",
                f"  Cash:            {_money(current.cash)}",
                f"  Positions Value: {_money(current.positions_value)}",
                f"  Total P&L:       {_money(total_pnl, signed=True)} ({_pct(current.pnl_pct, signed=True)})",
                f"  Realized P&L:    {_money(current.realized_pnl_cum, signed=True)}",
                f"  Unrealized P&L:  {_money(current.unrealized_pnl, signed=True)}",
                f"  Cycle:           {current.cycle}",
                f"  Timestamp:       {current.timestamp}",
            ]
        )
    else:
        lines.append("  No equity rows found.")

    lines.extend(["", "Open Positions"])
    lines.extend(
        _format_table(
            ["Ticker", "Qty", "Avg Cost", "Last", "Market Value", "Unreal. P&L", "Unreal. %"],
            [
                [
                    p.ticker,
                    _qty(p.quantity),
                    _money(p.avg_price),
                    _money(p.last_price),
                    _money(p.market_value),
                    _money(p.unrealized_pnl, signed=True),
                    _pct(p.unrealized_pnl_pct, signed=True),
                ]
                for p in report.positions
            ],
        )
    )

    lines.extend(["", "Recent Trades"])
    lines.extend(
        _format_table(
            ["Timestamp", "Action", "Ticker", "Qty", "Price", "Cash After", "Realized", "Reason"],
            [
                [
                    t.timestamp,
                    t.action.upper(),
                    t.ticker,
                    _qty(t.quantity),
                    _money(t.price),
                    _money(t.cash_after),
                    _money(t.realized_pnl, signed=True),
                    _clip(t.reason, 48),
                ]
                for t in report.trades
            ],
        )
    )

    lines.extend(["", "Recent Decisions"])
    lines.extend(
        _format_table(
            ["Timestamp", "Cycle", "Action", "Ticker", "Qty", "Price", "Conf", "Reasoning"],
            [
                [
                    d.timestamp,
                    str(d.cycle),
                    d.action.upper(),
                    d.ticker,
                    _qty(d.quantity),
                    _money(d.price_at_decision),
                    "-" if d.confidence is None else f"{d.confidence:.2f}",
                    _clip(d.reasoning_summary, 64),
                ]
                for d in report.decisions
            ],
        )
    )

    summary = report.summary
    lines.extend(["", "Equity Summary"])
    if summary.current_value is None:
        lines.append("  No equity history found.")
    else:
        lines.extend(
            [
                f"  Start:        {_money(summary.start_value)} ({summary.start_timestamp})",
                f"  Current:      {_money(summary.current_value)} ({summary.current_timestamp})",
                f"  Peak:         {_money(summary.peak_value)}",
                f"  Max Drawdown: {_pct(summary.max_drawdown_pct)}",
            ]
        )
    return "\n".join(lines)


def _html_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="empty">(none)</p>'
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body_rows = []
    for row in rows:
        body_rows.append("".join(f"<td>{html.escape(cell)}</td>" for cell in row))
    body = "".join(f"<tr>{row}</tr>" for row in body_rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _equity_svg(points: list[EquityPoint]) -> str:
    if len(points) < 2:
        return '<p class="empty">Not enough equity points for a chart.</p>'
    width, height = 760, 220
    pad = 28
    values = [p.total_value for p in points]
    low, high = min(values), max(values)
    span = high - low or 1.0
    x_span = max(len(points) - 1, 1)
    coords = []
    for i, point in enumerate(points):
        x = pad + (width - 2 * pad) * i / x_span
        y = height - pad - (height - 2 * pad) * (point.total_value - low) / span
        coords.append(f"{x:.1f},{y:.1f}")
    return f"""
    <svg viewBox="0 0 {width} {height}" role="img" aria-label="Equity curve">
      <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" />
      <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" />
      <polyline points="{' '.join(coords)}" />
      <text x="{pad}" y="{pad - 8}">{html.escape(_money(high))}</text>
      <text x="{pad}" y="{height - 6}">{html.escape(_money(low))}</text>
    </svg>
    """


def render_html(report: DashboardReport) -> str:
    """Render the report as a single read-only HTML page."""
    current = report.current
    summary = report.summary
    total_pnl = report.total_pnl
    title = "Paper Trading Dashboard"
    as_of_value = html.escape(report.as_of or "")
    since_value = html.escape(report.since or "")
    warning = f'<p class="warning">{html.escape(report.warning)}</p>' if report.warning else ""
    current_cards = [
        ("Total Value", _money(current.total_value) if current else "-"),
        ("Total P&L", _money(total_pnl, signed=True)),
        ("P&L %", _pct(current.pnl_pct, signed=True) if current else "-"),
        ("Realized", _money(current.realized_pnl_cum, signed=True) if current else "-"),
        ("Unrealized", _money(current.unrealized_pnl, signed=True) if current else "-"),
        ("Cash", _money(current.cash) if current else "-"),
    ]
    cards = "".join(
        f"<div><span>{html.escape(label)}</span><strong>{html.escape(value)}</strong></div>"
        for label, value in current_cards
    )
    positions = _html_table(
        ["Ticker", "Qty", "Avg Cost", "Last", "Market Value", "Unreal. P&L", "Unreal. %"],
        [
            [
                p.ticker,
                _qty(p.quantity),
                _money(p.avg_price),
                _money(p.last_price),
                _money(p.market_value),
                _money(p.unrealized_pnl, signed=True),
                _pct(p.unrealized_pnl_pct, signed=True),
            ]
            for p in report.positions
        ],
    )
    trades = _html_table(
        ["Timestamp", "Action", "Ticker", "Qty", "Price", "Cash After", "Realized", "Reason"],
        [
            [
                t.timestamp,
                t.action.upper(),
                t.ticker,
                _qty(t.quantity),
                _money(t.price),
                _money(t.cash_after),
                _money(t.realized_pnl, signed=True),
                _clip(t.reason, 64),
            ]
            for t in report.trades
        ],
    )
    decisions = _html_table(
        ["Timestamp", "Cycle", "Action", "Ticker", "Qty", "Price", "Conf", "Reasoning"],
        [
            [
                d.timestamp,
                str(d.cycle),
                d.action.upper(),
                d.ticker,
                _qty(d.quantity),
                _money(d.price_at_decision),
                "-" if d.confidence is None else f"{d.confidence:.2f}",
                _clip(d.reasoning_summary, 96),
            ]
            for d in report.decisions
        ],
    )
    summary_rows = [
        ["Start", _money(summary.start_value), summary.start_timestamp or "-"],
        ["Current", _money(summary.current_value), summary.current_timestamp or "-"],
        ["Peak", _money(summary.peak_value), ""],
        ["Max Drawdown", _pct(summary.max_drawdown_pct), ""],
    ]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: light; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f6f7f9; color: #18202a; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 24px; }}
    header {{ display: flex; justify-content: space-between; gap: 16px; align-items: end; flex-wrap: wrap; margin-bottom: 18px; }}
    h1 {{ margin: 0; font-size: 28px; letter-spacing: 0; }}
    h2 {{ font-size: 18px; margin: 26px 0 10px; letter-spacing: 0; }}
    form {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: end; }}
    label {{ display: grid; gap: 4px; font-size: 12px; color: #5a6472; }}
    input {{ min-width: 220px; border: 1px solid #cfd6df; border-radius: 6px; padding: 8px 10px; font: inherit; background: white; }}
    button {{ border: 1px solid #1f6feb; background: #1f6feb; color: white; border-radius: 6px; padding: 9px 12px; font: inherit; }}
    .db {{ color: #5a6472; margin-top: 4px; font-size: 13px; }}
    .warning {{ border-left: 4px solid #b42318; background: #fff1f0; padding: 10px 12px; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; }}
    .cards div {{ background: white; border: 1px solid #d9e0e8; border-radius: 8px; padding: 12px; }}
    .cards span {{ display: block; color: #5a6472; font-size: 12px; margin-bottom: 6px; }}
    .cards strong {{ font-size: 20px; letter-spacing: 0; }}
    .panel {{ background: white; border: 1px solid #d9e0e8; border-radius: 8px; padding: 14px; overflow-x: auto; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ text-align: left; border-bottom: 1px solid #e7ebf0; padding: 8px 10px; vertical-align: top; }}
    th {{ color: #4c5664; font-weight: 600; background: #fafbfc; }}
    .empty {{ color: #6b7280; }}
    svg {{ width: 100%; height: auto; }}
    svg line {{ stroke: #cfd6df; stroke-width: 1; }}
    svg polyline {{ fill: none; stroke: #1f6feb; stroke-width: 3; }}
    svg text {{ fill: #5a6472; font-size: 12px; }}
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>{title}</h1>
      <div class="db">{html.escape(report.db_path)}</div>
    </div>
    <form method="get" action="/">
      <label>As of <input name="as_of" value="{as_of_value}" placeholder="2026-01-01T15:30:00+00:00"></label>
      <label>Since <input name="since" value="{since_value}" placeholder="2026-01-01T00:00:00+00:00"></label>
      <button type="submit">Apply</button>
    </form>
  </header>
  {warning}
  <section class="cards">{cards}</section>
  <h2>Equity Curve</h2>
  <section class="panel">{_equity_svg(report.equity_curve)}</section>
  <h2>Open Positions</h2>
  <section class="panel">{positions}</section>
  <h2>Recent Trades</h2>
  <section class="panel">{trades}</section>
  <h2>Recent Decisions</h2>
  <section class="panel">{decisions}</section>
  <h2>Equity Summary</h2>
  <section class="panel">{_html_table(["Metric", "Value", "Timestamp"], summary_rows)}</section>
</main>
</body>
</html>
"""


def _handler_class(db_path: str, default_as_of: Optional[str], default_since: Optional[str]):
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "AgentDashboard/1.0"

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            params = parse_qs(parsed.query)
            as_of = params.get("as_of", [default_as_of])[0] or default_as_of
            since = params.get("since", [default_since])[0] or default_since
            try:
                report = load_report(db_path=db_path, as_of=as_of, since=since)
            except ValueError as exc:
                self.send_error(400, str(exc))
                return
            body = render_html(report).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_HEAD(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def do_POST(self) -> None:
            self.send_error(405, "read-only dashboard")

        def log_message(self, format: str, *args: object) -> None:
            print(f"dashboard: {format % args}", file=sys.stderr)

    return DashboardHandler


def serve(
    db_path: str = DEFAULT_DB_PATH,
    port: int = 8787,
    as_of: Optional[str] = None,
    since: Optional[str] = None,
) -> None:
    """Serve the dashboard on localhost with no write endpoints."""
    host = "127.0.0.1"
    handler = _handler_class(db_path, _normalize_iso(as_of), _normalize_iso(since))
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Serving read-only dashboard on http://{host}:{port}/", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped.", file=sys.stderr)
    finally:
        httpd.server_close()


def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agent.dashboard",
        description="Read-only report/dashboard for the paper-trading agent.",
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite database path.")
    parser.add_argument("--as-of", dest="as_of", type=_arg_iso, help="ISO timestamp upper bound.")
    parser.add_argument("--since", type=_arg_iso, help="ISO timestamp lower bound for logs.")
    parser.add_argument("--serve", action="store_true", help="Serve a localhost web dashboard.")
    parser.add_argument("--port", type=int, default=8787, help="Dashboard port (default: 8787).")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)
    if args.serve:
        serve(db_path=args.db, port=args.port, as_of=args.as_of, since=args.since)
        return 0
    report = load_report(db_path=args.db, as_of=args.as_of, since=args.since)
    print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

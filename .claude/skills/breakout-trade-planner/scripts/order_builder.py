"""Freedom24 order-proposal builder for the breakout trade planner.

This is the **project-local** override of the global skill's Alpaca order
builder. The global skill emits a single Alpaca *bracket* order (entry + OCO
take-profit + stop-loss in one request). Freedom24's ``place_order`` MCP tool
has **no native bracket / OCO order**, so a breakout plan is expressed as THREE
SEPARATE order proposals:

    1. ``entry``       — a buy stop_limit to get into the position.
    2. ``stop_loss``   — a protective sell stop, placed AFTER the entry fills.
    3. ``take_profit`` — a profit-target sell limit, placed AFTER the entry fills.

DECISION-SUPPORT ONLY. Nothing here ever executes an order. Every proposal is a
``place_order`` *call spec* with ``confirm: false``; the user reviews it and
runs ``place_order`` manually (and only then flips ``confirm=true``). The
builder never imports the MCP client and never touches the network.

Two limitations are baked into the proposal shape and surfaced to the user:

* **stop_limit single-price limitation.** The Freedom24 ``place_order`` tool
  takes ONE ``price`` argument; for a ``stop_limit`` it is used as BOTH the stop
  trigger and the limit price (see ``freedom24_mcp.place_order``: for
  ``stop_limit`` it sets ``limit_price = price`` AND ``stop_price = price``).
  The Alpaca template carried two distinct levels (``stop_price=signal_entry``,
  ``limit_price=worst_entry``). We cannot express both in one call, so the entry
  proposal uses ``signal_entry`` as the single ``price`` and surfaces
  ``worst_entry`` as a ``limit_hint`` plus a human-readable note. The user who
  wants a hard ceiling can place a plain ``limit`` order at ``worst_entry``
  instead.

* **no-bracket limitation.** The stop_loss and take_profit are independent
  follow-on orders. They MUST be placed only after the entry fills (otherwise a
  naked sell would short the account). Each proposal records this in
  ``place_after`` and its note.
"""

from __future__ import annotations

import sys
from pathlib import Path

# --- locate the repo root so we can import the project's skill_adapter --------
# Layout: <repo>/.claude/skills/breakout-trade-planner/scripts/order_builder.py
# parents: [scripts, breakout-trade-planner, skills, .claude, <repo>]
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from freedom24_core.skill_adapter import to_freedom24_ticker  # noqa: E402

PLACE_ORDER_TOOL = "mcp__freedom24__place_order"


def build_entry_proposal(
    symbol: str,
    qty: int,
    signal_entry: float,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
) -> dict:
    """Build the Freedom24 *entry* proposal (buy stop_limit) for pre-placement.

    Mirrors the global skill's ``build_pre_place_template`` (pre_place mode) but
    emits a ``place_order`` call spec instead of an Alpaca bracket.

    The single ``price`` carries ``signal_entry`` (used by Freedom24 as both the
    stop trigger and the limit). ``worst_entry`` is surfaced as ``limit_hint``
    because the MCP tool's one ``price`` arg cannot carry a separate ceiling.

    Raises:
        ValueError: On invalid inputs.
    """
    _validate_order_params(qty, signal_entry, worst_entry, stop_loss, take_profit)
    ticker = to_freedom24_ticker(symbol)

    return {
        "execution_mode": "pre_place",
        "requires_monitor_confirmation": False,
        "proposal_type": "entry",
        "place_after": None,  # entry goes first
        "tool": PLACE_ORDER_TOOL,
        "ticker": ticker,
        "action": "buy",
        "quantity": qty,
        "order_type": "stop_limit",
        "price": signal_entry,
        "limit_hint": worst_entry,
        "confirm": False,
        "note": (
            f"PROPOSAL ONLY — review then run place_order manually. Buy {qty} "
            f"{ticker} as a stop_limit at {signal_entry} (Freedom24 uses this one "
            f"price as BOTH stop trigger and limit). Intended worst-case fill ceiling "
            f"is {worst_entry} (limit_hint) — the MCP tool's single price arg cannot "
            f"carry a separate ceiling, so if you want a hard cap place a plain limit "
            f"order at {worst_entry} instead. confirm stays False until you approve."
        ),
    }


def build_stop_loss_proposal(
    symbol: str,
    qty: int,
    stop_loss: float,
    signal_entry: float | None = None,
) -> dict:
    """Build the protective *stop_loss* proposal (sell stop) — a follow-on order.

    Freedom24 has no bracket, so this is a SEPARATE order to place only AFTER the
    entry fills.

    Raises:
        ValueError: On invalid inputs.
    """
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if stop_loss <= 0:
        raise ValueError(f"stop_loss must be positive, got {stop_loss}")
    if signal_entry is not None and stop_loss >= signal_entry:
        raise ValueError(
            f"stop_loss ({stop_loss}) must be below entry ({signal_entry})"
        )
    ticker = to_freedom24_ticker(symbol)

    return {
        "execution_mode": "pre_place",
        "requires_monitor_confirmation": False,
        "proposal_type": "stop_loss",
        "place_after": "entry_fill",
        "tool": PLACE_ORDER_TOOL,
        "ticker": ticker,
        "action": "sell",
        "quantity": qty,
        "order_type": "stop",
        "price": stop_loss,
        "confirm": False,
        "note": (
            f"PROPOSAL ONLY — protective stop. Place ONLY after the entry fills "
            f"(Freedom24 has no native bracket/OCO). Sell {qty} {ticker} as a stop "
            f"order triggered at {stop_loss}. confirm stays False until you approve."
        ),
    }


def build_take_profit_proposal(
    symbol: str,
    qty: int,
    take_profit: float,
    worst_entry: float | None = None,
) -> dict:
    """Build the *take_profit* proposal (sell limit) — a follow-on order.

    Freedom24 has no bracket, so this is a SEPARATE order to place only AFTER the
    entry fills.

    Raises:
        ValueError: On invalid inputs.
    """
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if take_profit <= 0:
        raise ValueError(f"take_profit must be positive, got {take_profit}")
    if worst_entry is not None and take_profit <= worst_entry:
        raise ValueError(
            f"take_profit ({take_profit}) must be above worst_entry ({worst_entry})"
        )
    ticker = to_freedom24_ticker(symbol)

    return {
        "execution_mode": "pre_place",
        "requires_monitor_confirmation": False,
        "proposal_type": "take_profit",
        "place_after": "entry_fill",
        "tool": PLACE_ORDER_TOOL,
        "ticker": ticker,
        "action": "sell",
        "quantity": qty,
        "order_type": "limit",
        "price": take_profit,
        "confirm": False,
        "note": (
            f"PROPOSAL ONLY — profit target. Place ONLY after the entry fills "
            f"(Freedom24 has no native bracket/OCO). Sell {qty} {ticker} as a limit "
            f"order at {take_profit}. confirm stays False until you approve."
        ),
    }


def build_pre_place_proposal(
    symbol: str,
    qty: int,
    signal_entry: float,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
) -> dict:
    """Bundle the three pre_place proposals (entry + separate stop + target).

    This replaces the global skill's single Alpaca ``build_pre_place_template``
    bracket. Because Freedom24 cannot place a bracket, the result is a group of
    three independent ``place_order`` proposals, all ``confirm=False``.

    Raises:
        ValueError: On invalid inputs (validated once via the entry builder).
    """
    entry = build_entry_proposal(
        symbol, qty, signal_entry, worst_entry, stop_loss, take_profit
    )
    stop = build_stop_loss_proposal(symbol, qty, stop_loss, signal_entry=signal_entry)
    target = build_take_profit_proposal(symbol, qty, take_profit, worst_entry=worst_entry)
    return {
        "execution_mode": "pre_place",
        "requires_monitor_confirmation": False,
        "broker": "freedom24",
        "has_native_bracket": False,
        "proposals": [entry, stop, target],
        "note": (
            "Freedom24 has NO native bracket/OCO order. Entry is a stop_limit; the "
            "stop_loss and take_profit are SEPARATE protective orders to place after "
            "the entry fills. All proposals are confirm=False (decision-support only)."
        ),
    }


def build_post_confirm_proposal(
    symbol: str,
    qty: int,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
    entry_condition: dict,
) -> dict:
    """Bundle the post_confirm proposals (entry as plain limit + separate exits).

    Mirrors the global skill's ``build_post_confirm_template``: sent after the
    breakout-monitor confirms 5-min candle conditions. The entry is a plain
    ``limit`` at ``worst_entry`` (no stop trigger needed — the breakout already
    confirmed), so the single-price limitation does not bite here.

    Raises:
        ValueError: On invalid inputs.
    """
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if (worst_entry - stop_loss) < 0.01:
        raise ValueError(
            f"stop_loss ({stop_loss}) must be >= $0.01 below worst_entry ({worst_entry})"
        )
    if take_profit <= worst_entry:
        raise ValueError(
            f"take_profit ({take_profit}) must be above worst_entry ({worst_entry})"
        )
    ticker = to_freedom24_ticker(symbol)

    entry = {
        "execution_mode": "post_confirm",
        "requires_monitor_confirmation": True,
        "proposal_type": "entry",
        "place_after": "monitor_confirmation",
        "entry_condition": entry_condition,
        "tool": PLACE_ORDER_TOOL,
        "ticker": ticker,
        "action": "buy",
        "quantity": qty,
        "order_type": "limit",
        "price": worst_entry,
        "confirm": False,
        "note": (
            f"PROPOSAL ONLY — post-confirmation entry. After the 5-min breakout "
            f"monitor confirms (close>pivot, close_loc>=0.60, RVOL>=1.5), buy {qty} "
            f"{ticker} as a limit at {worst_entry}. confirm stays False until you approve."
        ),
    }
    stop = build_stop_loss_proposal(symbol, qty, stop_loss, signal_entry=worst_entry)
    target = build_take_profit_proposal(symbol, qty, take_profit, worst_entry=worst_entry)
    stop["execution_mode"] = "post_confirm"
    target["execution_mode"] = "post_confirm"

    return {
        "execution_mode": "post_confirm",
        "requires_monitor_confirmation": True,
        "broker": "freedom24",
        "has_native_bracket": False,
        "entry_condition": entry_condition,
        "proposals": [entry, stop, target],
        "note": (
            "Freedom24 has NO native bracket/OCO order. Entry is a plain limit placed "
            "only after the breakout monitor confirms; the stop_loss and take_profit "
            "are SEPARATE orders to place after the entry fills. All confirm=False."
        ),
    }


def build_revalidation_advisory(
    symbol: str,
    pivot: float,
    current_price: float,
    worst_entry: float,
) -> dict:
    """Build an advisory for Breakout-state candidates (no order proposal).

    These candidates already crossed the pivot and need live revalidation before
    any order can be proposed. The ticker is surfaced in Freedom24 form for
    convenience.
    """
    return {
        "symbol": symbol,
        "ticker": to_freedom24_ticker(symbol),
        "plan_type": "late_breakout_revalidation",
        "next_action": "revalidate live price/5min confirmation before any order",
        "pivot": pivot,
        "current_price": current_price,
        "max_entry_price": worst_entry,
    }


def build_entry_condition(
    pivot: float,
    close_loc_min: float = 0.60,
    rvol_threshold: float = 1.5,
    max_chase_pct: float = 2.0,
) -> dict:
    """Build a machine-readable entry condition for the post_confirm proposal."""
    return {
        "bar_interval": "5min",
        "trigger": {"field": "close", "op": ">", "value": pivot},
        "checks": [
            {"field": "close_loc", "op": ">=", "value": close_loc_min},
            {"field": "tod_rvol", "op": ">=", "value": rvol_threshold},
            {"field": "price_vs_pivot_pct", "op": "<=", "value": max_chase_pct},
        ],
    }


def _validate_order_params(
    qty: int,
    signal_entry: float,
    worst_entry: float,
    stop_loss: float,
    take_profit: float,
) -> None:
    """Shared validation for entry proposals (matches the global skill's rules)."""
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")
    if (signal_entry - stop_loss) < 0.01:
        raise ValueError(
            f"stop_loss ({stop_loss}) must be >= $0.01 below signal_entry ({signal_entry})"
        )
    if take_profit <= worst_entry:
        raise ValueError(
            f"take_profit ({take_profit}) must be above worst_entry ({worst_entry})"
        )

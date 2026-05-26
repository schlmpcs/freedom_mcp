"""The autonomous trading agent: one class plus shared decision helpers.

:class:`TradingAgent` runs observe→reason→act→log cycles. The model call and the
``<thinking>``/``<decision>`` parsing are factored into module-level helpers
(:func:`request_decision`, :func:`parse_decision_blocks`) so the eval harness can
reuse the exact same reasoning path on offline scenario data without constructing
a broker client.

Model calls go through ``AsyncAnthropic`` (genuinely awaitable); blocking broker
calls inside the tools are already dispatched via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any, Optional

from agent.memory import AgentMemory, Decision
from agent.model_backend import complete_text, resolve_backend
from agent.portfolio_state import PaperPortfolio
from agent.prompts import build_cycle_prompt, build_system_prompt
from agent.strategies.base import BaseStrategy
from agent.tools import execute_paper_order, get_market_status, observe_market
from freedom24_core import TradernetClient

DEFAULT_AGENT_MODEL = "claude-opus-4-7"

# Quote keys that may hold a usable last price, in priority order.
_PRICE_KEYS = ("ltp", "last_price", "last", "price", "lt", "c", "close")


# ---------------------------------------------------------------------------
# Response parsing (plain string handling — no regex, per spec)
# ---------------------------------------------------------------------------
def _between(text: str, start: str, end: str) -> str:
    """Return the substring strictly between ``start`` and ``end`` markers."""
    lower = text.lower()
    s = lower.find(start.lower())
    if s == -1:
        return ""
    s += len(start)
    e = lower.find(end.lower(), s)
    if e == -1:
        return text[s:].strip()
    return text[s:e].strip()


def _to_float(value: str) -> Optional[float]:
    cleaned = value.strip().strip("$").replace(",", "")
    if cleaned.lower() in ("", "null", "none", "n/a", "na"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_decision_blocks(text: str) -> dict[str, Any]:
    """Parse the model's ``<thinking>``/``<decision>`` blocks into fields.

    Returns a dict with ``thinking``, ``action``, ``ticker``, ``quantity``,
    ``confidence``, and ``reasoning_summary``. Missing or malformed input
    degrades to a safe ``action="nothing"`` with ``confidence=0.0``.
    """
    thinking = _between(text, "<thinking>", "</thinking>")
    block = _between(text, "<decision>", "</decision>")

    fields: dict[str, str] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()

    action = fields.get("action", "nothing").lower().strip()
    if action not in ("buy", "sell", "hold", "nothing"):
        action = "nothing"

    ticker_raw = fields.get("ticker", "").strip()
    ticker = None if ticker_raw.lower() in ("", "null", "none", "n/a") else ticker_raw

    confidence = _to_float(fields.get("confidence", "")) or 0.0
    confidence = max(0.0, min(1.0, confidence))

    return {
        "thinking": thinking or text.strip(),
        "action": action,
        "ticker": ticker,
        "quantity": _to_float(fields.get("quantity", "")),
        "confidence": confidence,
        "reasoning_summary": fields.get("reasoning", "").strip() or "(no reasoning provided)",
    }


async def request_decision(
    anthropic_client: Any,
    model: str,
    system_prompt: str,
    cycle_prompt: str,
    max_tokens: int = 4096,
    backend: Optional[str] = None,
) -> str:
    """Ask the model for a decision and return its raw text response.

    Routes through :func:`agent.model_backend.complete_text`, so the same call
    works against the Anthropic API (``anthropic_client``) or the Claude Code
    subscription backend depending on ``backend`` / ``AGENT_BACKEND``.
    """
    return await complete_text(
        system_prompt,
        cycle_prompt,
        model=model,
        max_tokens=max_tokens,
        backend=backend,
        anthropic_client=anthropic_client,
    )


def extract_price(observations: dict, ticker: Optional[str]) -> Optional[float]:
    """Best-effort last price for ``ticker`` from its observation entry."""
    if not ticker or ticker not in observations:
        return None
    quote = observations[ticker].get("quote") if isinstance(observations[ticker], dict) else None
    if not isinstance(quote, (dict, list)):
        return None
    for key in _PRICE_KEYS:
        found = _deep_find_number(quote, key)
        if found is not None:
            return found
    return None


def _deep_find_number(node: Any, target_key: str) -> Optional[float]:
    """Return the first numeric value stored under ``target_key`` anywhere in ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() == target_key and isinstance(value, (int, float)):
                return float(value)
            if str(key).lower() == target_key and isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    pass
        for value in node.values():
            found = _deep_find_number(value, target_key)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _deep_find_number(item, target_key)
            if found is not None:
                return found
    return None


# ---------------------------------------------------------------------------
# The agent
# ---------------------------------------------------------------------------
class TradingAgent:
    """Runs the observe→reason→act→log loop for one strategy."""

    def __init__(
        self,
        client: TradernetClient,
        strategy: BaseStrategy,
        memory: AgentMemory,
        portfolio: PaperPortfolio,
        dry_run: bool = True,
        model: Optional[str] = None,
        anthropic_client: Any = None,
        backend: Optional[str] = None,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.memory = memory
        self.portfolio = portfolio
        self.dry_run = dry_run
        self.backend = resolve_backend(backend)
        self.model = model or os.getenv("AGENT_MODEL") or DEFAULT_AGENT_MODEL
        # Only build an API client for the api backend; the claude_code backend
        # uses the Claude Code login and needs no ANTHROPIC_API_KEY.
        if anthropic_client is not None:
            self.anthropic = anthropic_client
        elif self.backend == "api":
            from anthropic import AsyncAnthropic

            self.anthropic = AsyncAnthropic()
        else:
            self.anthropic = None
        self.cycle = 0

    async def run_cycle(self) -> Optional[Decision]:
        """One full observe→reason→act→log cycle. Returns ``None`` if market closed."""
        self.cycle += 1

        # 1. OBSERVE
        market_status = await get_market_status(self.client)
        if not market_status.get("any_open"):
            print("Market closed, skipping cycle.", file=sys.stderr)
            return None

        observations = await observe_market(self.client, self.strategy.watchlist)
        filtered = self.strategy.filter_observations(observations)
        portfolio_state = self.portfolio.get_state()
        recent_decisions = self.memory.get_recent(5)

        # 2. REASON
        system_prompt = build_system_prompt(self.strategy)
        cycle_prompt = build_cycle_prompt(
            observations=filtered,
            portfolio=portfolio_state,
            recent_decisions=recent_decisions,
            cycle=self.cycle,
        )
        text = await request_decision(
            self.anthropic, self.model, system_prompt, cycle_prompt, backend=self.backend
        )

        # 3. PARSE
        decision = self._build_decision(text, filtered)

        # 4. ACT (paper only; gated by dry_run)
        if decision.action in ("buy", "sell") and not self.dry_run:
            if decision.quantity and decision.price_at_decision:
                fill = await execute_paper_order(
                    self.portfolio,
                    decision.ticker or "",
                    decision.action,
                    decision.quantity,
                    decision.price_at_decision,
                    decision.reasoning_summary,
                )
                decision.paper_order_id = fill.get("order_id")
                if fill.get("status") != "filled":
                    print(f"  paper order rejected: {fill.get('reason')}", file=sys.stderr)
            else:
                print("  skipped paper order: missing quantity or price", file=sys.stderr)

        # 5. LOG
        self.memory.save(decision)
        self._print_decision(decision)
        return decision

    def _build_decision(self, text: str, observations: dict) -> Decision:
        parsed = parse_decision_blocks(text)
        return Decision(
            cycle=self.cycle,
            ticker=parsed["ticker"],
            action=parsed["action"],
            quantity=parsed["quantity"],
            price_at_decision=extract_price(observations, parsed["ticker"]),
            thinking=parsed["thinking"],
            reasoning_summary=parsed["reasoning_summary"],
            confidence=parsed["confidence"],
        )

    def _print_decision(self, decision: Decision) -> None:
        print(
            f"[cycle {decision.cycle}] {decision.action.upper():7s} "
            f"{decision.ticker or '-':10s} qty={decision.quantity} "
            f"conf={decision.confidence:.2f} :: {decision.reasoning_summary}"
        )

    async def run(self, cycles: Optional[int] = None, interval_seconds: int = 300) -> None:
        """Run indefinitely or for ``cycles`` iterations, sleeping between each."""
        count = 0
        while cycles is None or count < cycles:
            try:
                await self.run_cycle()
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                print(f"Cycle error: {exc}", file=sys.stderr)
            count += 1
            if cycles is None or count < cycles:
                await asyncio.sleep(interval_seconds)

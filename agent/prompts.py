"""System and cycle prompts for the trading agent.

The system prompt pins the required ``<thinking>``/``<decision>`` output
contract (parsed by ``agent.agent.parse_decision_blocks``) and the universal
risk rules; the per-cycle prompt packages the current portfolio, filtered
observations, and recent decision history as JSON.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:  # avoid import cycles at runtime
    from agent.memory import Decision
    from agent.strategies.base import BaseStrategy

BASE_SYSTEM_PROMPT = """
You are a disciplined quantitative trading agent managing a paper portfolio.
You run in cycles. Each cycle you receive market observations and must decide.

REQUIRED OUTPUT FORMAT — always structure your response exactly like this:

<thinking>
[Your full reasoning here. Think step by step.
- What does the data show?
- What are the risks?
- What have your recent decisions gotten right or wrong?
- What is the most disciplined action given your rules?]
</thinking>

<decision>
action: buy | sell | hold | nothing
ticker: TICKER.US or null
quantity: number or null
confidence: 0.0-1.0
reasoning: one sentence explaining the decision
</decision>

UNIVERSAL RULES (never violate these):
- Never risk more than 5% of total portfolio on one position
- Always check news before acting on a price move
- If market is volatile (VIX-equivalent signals): reduce position sizes by half
- Cash is a valid position — doing nothing requires justification too
- Your reasoning will be scored by a judge. Be explicit about what evidence you used.
- If you are not confident (< 0.6), action must be "nothing"
""".strip()


def build_system_prompt(strategy: "BaseStrategy") -> str:
    """Combine the universal prompt with the strategy's own rules."""
    return BASE_SYSTEM_PROMPT + "\n\nSTRATEGY RULES:\n" + strategy.get_system_prompt()


def build_cycle_prompt(
    observations: dict,
    portfolio: dict,
    recent_decisions: "Sequence[Decision]",
    cycle: int,
) -> str:
    """Render one cycle's context (portfolio, observations, history) as a prompt."""
    recent = [_decision_brief(d) for d in recent_decisions]
    return f"""
CYCLE {cycle}

=== PORTFOLIO STATE ===
{json.dumps(portfolio, indent=2, default=str)}

=== MARKET OBSERVATIONS ===
{json.dumps(observations, indent=2, default=str)}

=== YOUR LAST {len(recent)} DECISIONS ===
{json.dumps(recent, indent=2, default=str)}

Analyze the situation and output your decision in the required format.
""".strip()


def _decision_brief(decision: "Decision | dict") -> dict:
    """A compact, prompt-friendly view of a past decision.

    Accepts either a :class:`~agent.memory.Decision` (live loop) or a plain dict
    (eval scenarios provide recent decisions as JSON dicts).
    """
    if isinstance(decision, dict):
        get = decision.get
    else:
        get = lambda key: getattr(decision, key, None)  # noqa: E731
    return {
        "cycle": get("cycle"),
        "timestamp": get("timestamp"),
        "action": get("action"),
        "ticker": get("ticker"),
        "quantity": get("quantity"),
        "confidence": get("confidence"),
        "reasoning": get("reasoning_summary") or get("reasoning"),
        "outcome_return_pct": get("outcome_return_pct"),
    }

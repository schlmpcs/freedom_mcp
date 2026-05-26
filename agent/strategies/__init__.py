"""Trading strategies for the agent.

Each strategy supplies its own system-prompt rules and a pre-filter that
augments raw market observations with strategy-specific signals before the
data is handed to the model. Strategies are selected by name via
:data:`STRATEGIES`.
"""

from __future__ import annotations

from agent.strategies.base import BaseStrategy
from agent.strategies.momentum import MomentumStrategy

STRATEGIES: dict[str, type[BaseStrategy]] = {
    MomentumStrategy.name: MomentumStrategy,
}


def get_strategy(name: str) -> BaseStrategy:
    """Instantiate a strategy by name, raising ``KeyError`` if unknown."""
    try:
        return STRATEGIES[name]()
    except KeyError as exc:
        available = ", ".join(sorted(STRATEGIES))
        raise KeyError(f"unknown strategy {name!r}; available: {available}") from exc


__all__ = ["BaseStrategy", "MomentumStrategy", "STRATEGIES", "get_strategy"]

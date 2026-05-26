"""Autonomous paper-trading agent built on top of ``freedom24_core``.

Public surface: the :class:`~agent.agent.TradingAgent` loop, the
:class:`~agent.memory.AgentMemory` decision log, the
:class:`~agent.portfolio_state.PaperPortfolio` tracker, and the strategy
registry. The agent never places real broker orders — all execution is against
the in-memory paper portfolio.
"""

from __future__ import annotations

from agent.agent import TradingAgent, parse_decision_blocks, request_decision
from agent.memory import AgentMemory, Decision
from agent.portfolio_state import PaperPortfolio
from agent.strategies import STRATEGIES, BaseStrategy, MomentumStrategy, get_strategy

__all__ = [
    "TradingAgent",
    "parse_decision_blocks",
    "request_decision",
    "AgentMemory",
    "Decision",
    "PaperPortfolio",
    "BaseStrategy",
    "MomentumStrategy",
    "STRATEGIES",
    "get_strategy",
]

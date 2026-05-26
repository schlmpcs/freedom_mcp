"""Abstract base class for trading strategies.

A strategy contributes two things to the agent: a block of natural-language
rules injected into the system prompt, and a pre-filter that enriches raw market
observations with computed signals before they reach the model.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseStrategy(ABC):
    """Interface every concrete strategy implements."""

    name: str
    description: str
    watchlist: list[str]

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return strategy-specific rules to inject into the system prompt."""
        raise NotImplementedError

    @abstractmethod
    def filter_observations(self, observations: dict) -> dict:
        """Pre-process market data, adding strategy signals, before the model sees it."""
        raise NotImplementedError

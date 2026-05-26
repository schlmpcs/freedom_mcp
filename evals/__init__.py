"""Offline eval harness for the trading agent.

Scores the agent's decisions against hand-authored scenarios (optimal-action
hit rate) and grades reasoning quality with a Claude-as-judge model. Scenarios
carry pre-baked market context, so only the Anthropic API is exercised — the
broker is never called.
"""

from __future__ import annotations

from evals.judge import judge_decision
from evals.metrics import compute_metrics
from evals.run_evals import load_scenarios, run_all_scenarios

__all__ = ["judge_decision", "compute_metrics", "load_scenarios", "run_all_scenarios"]

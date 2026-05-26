"""Tests for the eval harness: scenario loading, metrics, decision parsing.

Fully offline — no Anthropic or broker calls.
"""

import pytest

from agent.agent import parse_decision_blocks
from evals.metrics import compute_metrics
from evals.run_evals import load_scenarios

_REQUIRED_SCENARIO_KEYS = {"scenario_id", "description", "context", "ground_truth", "scoring"}
_EXPECTED_SCENARIOS = {
    "earnings_miss_001",
    "bull_breakout_001",
    "high_volatility_001",
    "sideways_chop_001",
    "gap_down_open_001",
}


def test_all_scenarios_load_with_required_keys():
    scenarios = load_scenarios("all")
    assert len(scenarios) == 5
    ids = {s["scenario_id"] for s in scenarios}
    assert ids == _EXPECTED_SCENARIOS
    for s in scenarios:
        assert _REQUIRED_SCENARIO_KEYS <= set(s)
        # optimal_action must be a key in the scoring map
        assert s["ground_truth"]["optimal_action"] in s["scoring"]


def test_load_specific_scenarios_by_stem():
    scenarios = load_scenarios("bull_breakout,earnings_miss")
    ids = {s["scenario_id"] for s in scenarios}
    assert ids == {"bull_breakout_001", "earnings_miss_001"}


def test_load_missing_scenario_raises():
    with pytest.raises(FileNotFoundError):
        load_scenarios("does_not_exist")


def _result(scenario_id, action_score, is_optimal, overall):
    return {
        "scenario_id": scenario_id,
        "action_taken": "buy",
        "action_score": action_score,
        "is_optimal": is_optimal,
        "judge_scores": {
            "evidence_use": 4,
            "risk_awareness": 4,
            "consistency": 5,
            "rule_adherence": 4,
            "calibration": 3,
            "overall": overall,
        },
    }


def test_compute_metrics_scenario_score_and_dimensions():
    results = [
        _result("a", 1.0, True, 5),
        _result("b", 1.0, True, 4),
        _result("c", -1.0, False, 2),
        _result("d", 0.3, False, 3),
    ]
    metrics = compute_metrics(results)
    assert metrics["scenario_count"] == 4
    assert metrics["optimal_hits"] == 2
    assert metrics["scenario_score"] == 0.5
    assert metrics["avg_judge_score"] == 3.5
    assert metrics["score_by_dimension"]["consistency"] == 5.0
    assert metrics["score_by_dimension"]["calibration"] == 3.0
    # best are the optimal ones, ordered by score then judge; worst are suboptimal
    assert metrics["best_scenarios"] == ["a", "b"]
    assert set(metrics["worst_scenarios"]) == {"c", "d"}


def test_consistency_gap_sign():
    # Strong reasoning (overall 5 -> 1.0) but only half optimal (0.5) => +0.5 gap.
    results = [
        _result("a", 1.0, True, 5),
        _result("b", -1.0, False, 5),
    ]
    metrics = compute_metrics(results)
    assert metrics["scenario_score"] == 0.5
    assert metrics["consistency_gap"] == pytest.approx(0.5)


def test_compute_metrics_handles_missing_judge_scores():
    results = [
        {"scenario_id": "x", "action_taken": "error", "action_score": 0.0, "is_optimal": False, "judge_scores": None},
    ]
    metrics = compute_metrics(results)
    assert metrics["avg_judge_score"] is None
    assert metrics["consistency_gap"] is None
    assert metrics["scenario_score"] == 0.0


def test_compute_metrics_empty():
    metrics = compute_metrics([])
    assert metrics["scenario_count"] == 0
    assert metrics["scenario_score"] is None


# -- decision parsing (the shared reasoning path) --------------------------
def test_parse_decision_blocks_extracts_fields():
    text = """
<thinking>
The breakout is confirmed by volume and price is above the 10-day average.
</thinking>

<decision>
action: buy
ticker: NVDA.US
quantity: 5
confidence: 0.8
reasoning: Volume-confirmed breakout above the 10-day average.
</decision>
"""
    parsed = parse_decision_blocks(text)
    assert parsed["action"] == "buy"
    assert parsed["ticker"] == "NVDA.US"
    assert parsed["quantity"] == 5.0
    assert parsed["confidence"] == 0.8
    assert "breakout" in parsed["reasoning_summary"].lower()
    assert "10-day average" in parsed["thinking"]


def test_parse_decision_blocks_handles_null_ticker_and_clamps_confidence():
    text = """
<thinking>No edge here.</thinking>
<decision>
action: nothing
ticker: null
quantity: null
confidence: 1.7
reasoning: Standing aside.
</decision>
"""
    parsed = parse_decision_blocks(text)
    assert parsed["action"] == "nothing"
    assert parsed["ticker"] is None
    assert parsed["quantity"] is None
    assert parsed["confidence"] == 1.0  # clamped to [0,1]


def test_parse_decision_blocks_degrades_safely_on_garbage():
    parsed = parse_decision_blocks("the model said something unparseable")
    assert parsed["action"] == "nothing"
    assert parsed["confidence"] == 0.0

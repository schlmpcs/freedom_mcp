"""Aggregate metrics for an eval run.

Turns a list of per-scenario results into headline numbers: how often the agent
took the optimal action (``scenario_score``), the average judge score and its
per-dimension breakdown, the best/worst scenarios, and a ``consistency_gap`` that
flags when reasoning quality outruns actual outcomes ("sounds smart but acts
poorly").

Each input result is expected to look like::

    {
        "scenario_id": str,
        "action_taken": str,
        "action_score": float,      # from the scenario's scoring map
        "is_optimal": bool,         # action_taken == ground_truth optimal_action
        "judge_scores": dict | None # judge output (may contain None dimensions)
    }
"""

from __future__ import annotations

from typing import Any, Optional

_JUDGE_DIMENSIONS = ("evidence_use", "risk_awareness", "consistency", "rule_adherence", "calibration")


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _numeric(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def compute_metrics(results: list[dict]) -> dict:
    """Compute headline metrics over a list of per-scenario eval results."""
    total = len(results)
    if total == 0:
        return {
            "scenario_count": 0,
            "scenario_score": None,
            "avg_action_score": None,
            "avg_judge_score": None,
            "score_by_dimension": {},
            "best_scenarios": [],
            "worst_scenarios": [],
            "consistency_gap": None,
        }

    optimal_hits = sum(1 for r in results if r.get("is_optimal"))
    scenario_score = optimal_hits / total
    avg_action_score = _mean([float(r.get("action_score", 0.0)) for r in results])

    # Judge dimensions (ignore None / missing).
    by_dim: dict[str, Optional[float]] = {}
    for dim in _JUDGE_DIMENSIONS:
        vals = [
            v
            for r in results
            if (v := _numeric((r.get("judge_scores") or {}).get(dim))) is not None
        ]
        by_dim[dim] = _mean(vals)

    overall_vals = [
        v
        for r in results
        if (v := _numeric((r.get("judge_scores") or {}).get("overall"))) is not None
    ]
    avg_judge_score = _mean(overall_vals)

    # Best: optimal action with strong reasoning. Worst: suboptimal action.
    best = sorted(
        (r for r in results if r.get("is_optimal")),
        key=lambda r: (float(r.get("action_score", 0.0)), _overall_or_zero(r)),
        reverse=True,
    )
    worst = sorted(
        (r for r in results if not r.get("is_optimal")),
        key=lambda r: (float(r.get("action_score", 0.0)), _overall_or_zero(r)),
    )

    # Reasoning (0-1) minus outcome (0-1). Positive = reasoning ahead of outcomes.
    consistency_gap = None
    if avg_judge_score is not None:
        consistency_gap = round((avg_judge_score / 5.0) - scenario_score, 4)

    return {
        "scenario_count": total,
        "scenario_score": round(scenario_score, 4),
        "optimal_hits": optimal_hits,
        "avg_action_score": round(avg_action_score, 4) if avg_action_score is not None else None,
        "avg_judge_score": round(avg_judge_score, 4) if avg_judge_score is not None else None,
        "score_by_dimension": {
            k: (round(v, 4) if v is not None else None) for k, v in by_dim.items()
        },
        "best_scenarios": [r["scenario_id"] for r in best],
        "worst_scenarios": [r["scenario_id"] for r in worst],
        "consistency_gap": consistency_gap,
    }


def _overall_or_zero(result: dict) -> float:
    value = _numeric((result.get("judge_scores") or {}).get("overall"))
    return value if value is not None else 0.0

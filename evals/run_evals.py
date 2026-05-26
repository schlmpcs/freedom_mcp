"""Run agent-decision scenarios offline and print a scored report.

Each scenario carries a pre-baked market context (portfolio, observations with
momentum signals, recent decisions), so the runner never touches the broker —
only the Anthropic API is called, once for the agent's decision and once for the
judge. The agent's reasoning path is the *same* code the live loop uses
(``request_decision`` + ``parse_decision_blocks``), so evals measure the real thing.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from agent.agent import DEFAULT_AGENT_MODEL, extract_price, parse_decision_blocks, request_decision
from agent.memory import Decision
from agent.model_backend import resolve_backend
from agent.prompts import build_cycle_prompt, build_system_prompt
from agent.strategies import MomentumStrategy
from evals.judge import judge_decision
from evals.metrics import compute_metrics

SCENARIO_DIR = Path(__file__).parent / "scenarios"


def load_scenarios(names: str = "all") -> list[dict]:
    """Load scenario JSON files. ``names`` is "all" or a comma-separated list of stems."""
    if names == "all":
        paths = sorted(SCENARIO_DIR.glob("*.json"))
    else:
        wanted = [n.strip() for n in names.split(",") if n.strip()]
        paths = [SCENARIO_DIR / f"{stem}.json" for stem in wanted]

    scenarios: list[dict] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"scenario not found: {path}")
        with path.open(encoding="utf-8") as fh:
            scenarios.append(json.load(fh))
    return scenarios


async def run_one_scenario(
    scenario: dict,
    strategy: MomentumStrategy,
    anthropic_client: Any,
    model: str,
    verbose: bool = False,
    backend: Optional[str] = None,
) -> dict:
    """Feed one scenario to the agent, score it, and judge the reasoning."""
    ctx = scenario.get("context", {})
    observations = strategy.filter_observations(ctx.get("observations", {}))
    portfolio = ctx.get("portfolio", {})
    recent = ctx.get("recent_decisions", [])

    system_prompt = build_system_prompt(strategy)
    cycle_prompt = build_cycle_prompt(observations, portfolio, recent, cycle=1)
    text = await request_decision(anthropic_client, model, system_prompt, cycle_prompt, backend=backend)
    parsed = parse_decision_blocks(text)

    action = parsed["action"]
    scoring = scenario.get("scoring", {})
    action_score = float(scoring.get(action, 0.0))
    optimal_action = scenario.get("ground_truth", {}).get("optimal_action")
    is_optimal = action == optimal_action

    decision = Decision(
        cycle=1,
        ticker=parsed["ticker"],
        action=action,
        quantity=parsed["quantity"],
        price_at_decision=extract_price(observations, parsed["ticker"]),
        thinking=parsed["thinking"],
        reasoning_summary=parsed["reasoning_summary"],
        confidence=parsed["confidence"],
    )
    judge_scores = await judge_decision(decision, ctx, anthropic_client=anthropic_client, backend=backend)

    if verbose:
        mark = "✓" if is_optimal else "✗"
        print(
            f"  {mark} {scenario.get('scenario_id'):24s} "
            f"action={action:8s} score={action_score:+.2f} "
            f"judge={judge_scores.get('overall')}",
            file=sys.stderr,
        )

    return {
        "scenario_id": scenario.get("scenario_id", "unknown"),
        "description": scenario.get("description", ""),
        "action_taken": action,
        "optimal_action": optimal_action,
        "action_score": action_score,
        "is_optimal": is_optimal,
        "confidence": parsed["confidence"],
        "judge_scores": judge_scores,
        "decision": decision.to_dict(),
    }


async def run_all_scenarios(
    names: str = "all", verbose: bool = False, backend: Optional[str] = None
) -> Optional[dict]:
    """Run the selected scenarios, print the report, and return metrics+results."""
    resolved = resolve_backend(backend)

    client: Any = None
    if resolved == "api":
        if not os.getenv("ANTHROPIC_API_KEY"):
            print(
                "ANTHROPIC_API_KEY is not set. The 'api' backend calls the Anthropic "
                "API for the agent decision and the judge — set the key, or use the "
                "Claude Code (Max) backend with --backend claude_code.",
                file=sys.stderr,
            )
            return None
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()

    scenarios = load_scenarios(names)
    if not scenarios:
        print("No scenarios to run.", file=sys.stderr)
        return None

    strategy = MomentumStrategy()
    model = os.getenv("AGENT_MODEL") or DEFAULT_AGENT_MODEL

    if verbose:
        print(f"Running {len(scenarios)} scenarios (backend={resolved}, model={model})...", file=sys.stderr)

    results = []
    for scenario in scenarios:
        try:
            results.append(
                await run_one_scenario(scenario, strategy, client, model, verbose, backend=resolved)
            )
        except Exception as exc:  # noqa: BLE001 - record failure, keep going
            print(f"  scenario {scenario.get('scenario_id')} errored: {exc}", file=sys.stderr)
            results.append(
                {
                    "scenario_id": scenario.get("scenario_id", "unknown"),
                    "action_taken": "error",
                    "optimal_action": scenario.get("ground_truth", {}).get("optimal_action"),
                    "action_score": 0.0,
                    "is_optimal": False,
                    "confidence": 0.0,
                    "judge_scores": None,
                    "decision": {},
                }
            )

    metrics = compute_metrics(results)
    print_report(metrics, results)
    return {"metrics": metrics, "results": results}


def print_report(metrics: dict, results: list[dict]) -> None:
    """Print the formatted eval report to stdout."""
    bar = "═" * 40
    n = metrics["scenario_count"]
    score = metrics["scenario_score"]
    pct = f"({score * 100:.0f}%)" if score is not None else ""
    dims = metrics["score_by_dimension"]

    def fmt(value: object, suffix: str = "/5") -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:.1f}{suffix}"
        return f"{value}{suffix}"

    print(bar)
    print(f"EVAL RESULTS — {n} scenarios")
    print(bar)
    print(f"Scenario Score:     {metrics.get('optimal_hits', 0)}/{n}  {pct}")
    print(f"Avg Judge Score:    {fmt(metrics['avg_judge_score'])}")
    print()
    print("By dimension:")
    labels = {
        "evidence_use": "Evidence Use",
        "risk_awareness": "Risk Awareness",
        "consistency": "Consistency",
        "rule_adherence": "Rule Adherence",
        "calibration": "Calibration",
    }
    for key, label in labels.items():
        print(f"  {label + ':':18s}{fmt(dims.get(key))}")
    print()
    gap = metrics["consistency_gap"]
    if gap is not None:
        direction = "reasoning ahead of outcomes" if gap >= 0 else "outcomes ahead of reasoning"
        print(f"Consistency Gap:    {gap:+.2f}  ({direction})")
    print()
    print("Per scenario:")
    for r in results:
        mark = "✓" if r.get("is_optimal") else "✗"
        judge = (r.get("judge_scores") or {}).get("overall")
        judge_str = f"{judge}" if judge is not None else "n/a"
        print(
            f"  {mark} {r['scenario_id']:24s} action={r['action_taken']:8s} "
            f"score={float(r['action_score']):+.2f}  judge={judge_str}"
        )
    print(bar)

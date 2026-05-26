"""Claude-as-judge scorer for agent decisions.

Sends a decision plus the market context it had to a judge model and asks for a
1-5 score on five reasoning dimensions. The judge is asked for raw JSON; this
module parses it defensively (tolerating stray prose or markdown fences) and
falls back to a clearly-marked error result rather than raising, so one bad
judge response never sinks an eval run.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Optional

from agent.model_backend import complete_text

if TYPE_CHECKING:
    from agent.memory import Decision

DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"

JUDGE_PROMPT = """
You are evaluating an AI trading agent's decision quality.
Score on each dimension from 1-5:

1. EVIDENCE_USE — Did it reference specific data points from the observations?
2. RISK_AWARENESS — Did it consider what could go wrong?
3. CONSISTENCY — Does the action match what the reasoning says?
4. RULE_ADHERENCE — Did it follow its stated strategy rules?
5. CALIBRATION — Was confidence appropriate given the uncertainty?

Agent decision to evaluate:
{decision}

Market context it had access to:
{context}

Respond ONLY with valid JSON, no markdown:
{{
  "evidence_use": <1-5>,
  "risk_awareness": <1-5>,
  "consistency": <1-5>,
  "rule_adherence": <1-5>,
  "calibration": <1-5>,
  "overall": <1-5>,
  "summary": "<one sentence critique>"
}}
""".strip()

_DIMENSIONS = ("evidence_use", "risk_awareness", "consistency", "rule_adherence", "calibration", "overall")


def _decision_payload(decision: "Decision | dict") -> dict:
    if isinstance(decision, dict):
        return decision
    return decision.to_dict()


def _parse_json_object(text: str) -> Optional[dict]:
    """Extract the first top-level JSON object from ``text``."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Drop a leading ```json / ``` fence and any trailing fence.
        cleaned = cleaned.split("```", 2)[-1] if cleaned.count("```") >= 2 else cleaned.strip("`")
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None


def _error_scores(message: str) -> dict:
    scores = {dim: None for dim in _DIMENSIONS}
    scores["summary"] = f"judge error: {message}"
    return scores


async def judge_decision(
    decision: "Decision | dict",
    context: dict,
    anthropic_client: Any = None,
    model: Optional[str] = None,
    backend: Optional[str] = None,
) -> dict:
    """Score one decision on the five reasoning dimensions plus ``overall``.

    Routes through :func:`agent.model_backend.complete_text`, so it works on the
    Anthropic API or the Claude Code (Max) backend. Returns a dict with integer
    scores (or ``None`` on parse failure) and a ``summary`` string. Never raises
    for model/transport/parse problems — those are surfaced as an error result.
    """
    model = model or os.getenv("JUDGE_MODEL") or DEFAULT_JUDGE_MODEL
    prompt = JUDGE_PROMPT.format(
        decision=json.dumps(_decision_payload(decision), default=str),
        context=json.dumps(context, default=str),
    )
    try:
        text = await complete_text(
            system_prompt="",
            user_message=prompt,
            model=model,
            max_tokens=500,
            backend=backend,
            anthropic_client=anthropic_client,
        )
    except Exception as exc:  # noqa: BLE001 - judge failure must not abort the run
        return _error_scores(str(exc))

    parsed = _parse_json_object(text)
    if parsed is None:
        return _error_scores("could not parse JSON from judge response")
    return parsed

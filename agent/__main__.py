"""CLI entry point for the trading agent: ``python -m agent``.

Wires together config → broker client → strategy → memory → paper portfolio →
:class:`~agent.agent.TradingAgent`, then runs the loop. Paper-only and dry-run by
default; pass ``--no-dry-run`` to let the agent actually update the paper
portfolio (it still never places real broker orders).

Examples::

    python -m agent --strategy momentum --cycles 10 --interval 300
    python -m agent --strategy momentum --no-dry-run --cycles 20
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys

from agent.agent import TradingAgent
from agent.memory import DEFAULT_DB_PATH, AgentMemory
from agent.model_backend import resolve_backend
from agent.portfolio_state import PaperPortfolio
from agent.strategies import get_strategy
from freedom24_core import TradernetClient, load_config, setup_logging


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="agent", description="Autonomous paper-trading agent.")
    parser.add_argument("--strategy", default="momentum", help="Strategy name (default: momentum).")
    parser.add_argument(
        "--cycles", type=int, default=None, help="Number of cycles to run (default: forever)."
    )
    parser.add_argument(
        "--interval", type=int, default=300, help="Seconds to sleep between cycles (default: 300)."
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="If set (default), log decisions without updating the paper portfolio. "
        "Use --no-dry-run to enable paper execution.",
    )
    parser.add_argument(
        "--starting-cash", type=float, default=10_000.0, help="Paper portfolio starting cash."
    )
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="SQLite path for decisions/transactions.")
    parser.add_argument("--model", default=None, help="Override the decision model id.")
    parser.add_argument(
        "--backend",
        default=None,
        choices=["api", "claude_code", "codex"],
        help="Model backend: 'api' (ANTHROPIC_API_KEY), 'claude_code' (Claude Max "
        "subscription via the Agent SDK; run `claude login` first) or 'codex' "
        "(Codex CLI subscription; run `codex login` first). "
        "Defaults to $AGENT_BACKEND or 'api'.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    setup_logging()

    try:
        strategy = get_strategy(args.strategy)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    config = load_config()
    if not config.has_any_auth:
        print(
            "No broker credentials configured (.env). The agent needs read access "
            "to fetch market observations. Set FREEDOM24_PUB_KEY + FREEDOM24_PRIV_KEY.",
            file=sys.stderr,
        )
        return 2

    # Fail fast on a missing decision-model credential. The 'api' backend builds
    # an AsyncAnthropic client that only raises at request time, which otherwise
    # surfaces mid-cycle as a cryptic 'Could not resolve authentication method'
    # after broker calls have already run.
    backend = resolve_backend(args.backend)
    if backend == "api" and not os.getenv("ANTHROPIC_API_KEY"):
        print(
            "AGENT_BACKEND=api requires ANTHROPIC_API_KEY to be set (.env or env). "
            "Set it, or use a subscription backend instead: AGENT_BACKEND=claude_code "
            "(run `claude login` first) or AGENT_BACKEND=codex (run `codex login` first).",
            file=sys.stderr,
        )
        return 2
    if backend == "codex" and shutil.which("codex") is None:
        print(
            "AGENT_BACKEND=codex requires the `codex` CLI on PATH, logged in to your "
            "ChatGPT/Codex plan (run `codex login` first).",
            file=sys.stderr,
        )
        return 2

    client = TradernetClient(config)
    memory = AgentMemory(db_path=args.db)
    portfolio = PaperPortfolio(starting_cash=args.starting_cash, db_path=args.db)
    agent = TradingAgent(
        client=client,
        strategy=strategy,
        memory=memory,
        portfolio=portfolio,
        dry_run=args.dry_run,
        model=args.model,
        backend=args.backend,
    )

    print(
        f"Starting agent: strategy={strategy.name} backend={agent.backend} "
        f"dry_run={args.dry_run} cycles={args.cycles} interval={args.interval}s model={agent.model}",
        file=sys.stderr,
    )
    try:
        asyncio.run(agent.run(cycles=args.cycles, interval_seconds=args.interval))
    except KeyboardInterrupt:
        print("\nInterrupted; shutting down.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

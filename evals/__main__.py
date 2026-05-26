"""CLI entry point for the eval harness: ``python -m evals``.

Runs the agent's decision path over offline scenarios and prints a scored report.
Requires ``ANTHROPIC_API_KEY`` (the agent decision and judge call the API); the
broker is never contacted.

Examples::

    python -m evals --scenarios all --verbose
    python -m evals --scenarios bull_breakout,earnings_miss
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from evals.run_evals import run_all_scenarios


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="evals", description="Score the trading agent on scenarios.")
    parser.add_argument(
        "--scenarios",
        default="all",
        help='"all" (default) or a comma-separated list of scenario file stems.',
    )
    parser.add_argument("--verbose", action="store_true", help="Print per-scenario progress to stderr.")
    parser.add_argument(
        "--backend",
        default=None,
        choices=["api", "claude_code"],
        help="Model backend: 'api' (ANTHROPIC_API_KEY) or 'claude_code' (Max subscription). "
        "Defaults to $AGENT_BACKEND or 'api'.",
    )
    return parser.parse_args(argv)


def _force_utf8(stream: object) -> None:
    """Best-effort switch a text stream to UTF-8 so the report's box/✓ chars print.

    Windows consoles often default to cp1252, which would raise on the report's
    Unicode glyphs. ``reconfigure`` exists on Python 3.7+ text streams.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _force_utf8(sys.stdout)
    _force_utf8(sys.stderr)
    result = asyncio.run(
        run_all_scenarios(names=args.scenarios, verbose=args.verbose, backend=args.backend)
    )
    if result is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

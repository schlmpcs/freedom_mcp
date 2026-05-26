"""Pluggable model backend: Anthropic API key OR Claude Code (Max) subscription.

The agent and the eval judge each need exactly one capability — "given a system
prompt and a user message, return text" — so that is the entire surface here
(:func:`complete_text`). Two backends implement it:

* ``api`` (default) — the ``anthropic`` SDK, billed via an Anthropic Console
  ``ANTHROPIC_API_KEY`` (pay-as-you-go).
* ``claude_code`` — the ``claude-agent-sdk``, which runs on top of the Claude
  Code CLI and authenticates with your **Claude Max/Pro** login (run ``claude
  login`` once). No API key required; usage draws on the subscription.

Select with the ``AGENT_BACKEND`` env var or the ``backend`` argument. Both
third-party imports are deferred into their backend functions so importing this
module never requires either dependency — that keeps the offline tests and the
unused backend's dependency optional.
"""

from __future__ import annotations

import os
from typing import Any, Optional

DEFAULT_BACKEND = "api"
_API_ALIASES = {"api", "anthropic", "key"}
_CLAUDE_CODE_ALIASES = {"claude_code", "claude-code", "agent_sdk", "agent-sdk", "subscription", "max"}


def resolve_backend(backend: Optional[str] = None) -> str:
    """Normalise a backend selector to ``"api"`` or ``"claude_code"``."""
    raw = (backend or os.getenv("AGENT_BACKEND") or DEFAULT_BACKEND).strip().lower()
    if raw in _API_ALIASES:
        return "api"
    if raw in _CLAUDE_CODE_ALIASES:
        return "claude_code"
    raise ValueError(
        f"unknown backend {raw!r}; use 'api' (Anthropic API key) or "
        f"'claude_code' (Claude Max subscription via the Agent SDK)"
    )


async def complete_text(
    system_prompt: str,
    user_message: str,
    *,
    model: str,
    max_tokens: int = 4096,
    backend: Optional[str] = None,
    anthropic_client: Any = None,
) -> str:
    """Return the model's text response for ``system_prompt`` + ``user_message``."""
    resolved = resolve_backend(backend)
    if resolved == "claude_code":
        return await _complete_via_agent_sdk(system_prompt, user_message, model=model, max_tokens=max_tokens)
    return await _complete_via_api(
        system_prompt, user_message, model=model, max_tokens=max_tokens, anthropic_client=anthropic_client
    )


# ---------------------------------------------------------------------------
# Backend: Anthropic API key
# ---------------------------------------------------------------------------
async def _complete_via_api(
    system_prompt: str,
    user_message: str,
    *,
    model: str,
    max_tokens: int,
    anthropic_client: Any = None,
) -> str:
    from anthropic import AsyncAnthropic

    client = anthropic_client or AsyncAnthropic()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": user_message}],
    }
    if system_prompt:
        kwargs["system"] = system_prompt
    response = await client.messages.create(**kwargs)
    return _text_from_anthropic(response)


def _text_from_anthropic(response: Any) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts).strip()


# ---------------------------------------------------------------------------
# Backend: Claude Code / Agent SDK (Max subscription)
# ---------------------------------------------------------------------------
async def _complete_via_agent_sdk(
    system_prompt: str,
    user_message: str,
    *,
    model: str,
    max_tokens: int,  # noqa: ARG001 - Agent SDK manages output length itself
) -> str:
    try:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise RuntimeError(
            "claude_code backend requires the 'claude-agent-sdk' package and the "
            "`claude` CLI logged in to your Max/Pro plan (run `claude login`)."
        ) from exc

    options = ClaudeAgentOptions(
        system_prompt=system_prompt or None,
        model=model,
        allowed_tools=[],          # pure text generation — no tools
        max_turns=1,               # single request/response
        permission_mode="bypassPermissions",
        setting_sources=[],        # don't inherit project settings/CLAUDE.md
    )

    parts: list[str] = []
    fallback = ""
    async for message in query(prompt=user_message, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
        elif isinstance(message, ResultMessage):
            if getattr(message, "is_error", False):
                raise RuntimeError(f"Agent SDK error: {getattr(message, 'result', 'unknown')}")
            fallback = getattr(message, "result", "") or ""

    text = "\n".join(parts).strip()
    return text or fallback.strip()

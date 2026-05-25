"""Single-user access control: only the configured chat ID may use the bot."""

from __future__ import annotations

from typing import Optional

from telegram.ext import filters


def is_allowed(chat_id: Optional[int], allowed: Optional[int]) -> bool:
    """True only when an allowlist is configured and the chat matches it."""
    return allowed is not None and chat_id == allowed


def build_chat_filter(allowed: int) -> filters.BaseFilter:
    """PTB filter restricting handlers to the single allowed chat."""
    return filters.Chat(chat_id=allowed)

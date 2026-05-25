"""Persist the set of alert IDs already relayed to Telegram, across restarts."""

from __future__ import annotations

import json
import os


def load_seen(path: str) -> set[int]:
    """Return the persisted set of relayed alert IDs, or empty on any failure."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return {int(x) for x in data}
    except (FileNotFoundError, ValueError, TypeError):
        return set()


def save_seen(path: str, seen: set[int]) -> None:
    """Atomically write the relayed-IDs set (temp file + os.replace)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(sorted(seen), fh)
    os.replace(tmp, path)

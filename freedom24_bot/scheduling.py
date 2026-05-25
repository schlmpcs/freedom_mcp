"""Timezone-aware report scheduling helpers. DST is handled by zoneinfo."""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo


def parse_time_in_tz(hhmm: str, tz_name: str) -> datetime.time:
    """Parse 'HH:MM' into a tz-aware datetime.time for PTB JobQueue.run_daily."""
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.time(hour=hour, minute=minute, tzinfo=ZoneInfo(tz_name))


def is_market_weekday(now: datetime.datetime) -> bool:
    """True Monday–Friday (US market days). `now` must be tz-aware."""
    return now.weekday() < 5  # Mon=0 .. Fri=4

import datetime
from zoneinfo import ZoneInfo

from freedom24_bot.scheduling import parse_time_in_tz, is_market_weekday


def test_parse_time_in_tz_sets_hour_minute_and_tz():
    t = parse_time_in_tz("08:30", "America/New_York")
    assert (t.hour, t.minute) == (8, 30)
    assert t.tzinfo == ZoneInfo("America/New_York")


def test_parse_time_in_tz_snapshot_local():
    t = parse_time_in_tz("08:00", "Asia/Karachi")
    assert (t.hour, t.minute) == (8, 0)
    assert t.tzinfo == ZoneInfo("Asia/Karachi")


def test_is_market_weekday_true_on_wednesday():
    wed = datetime.datetime(2026, 5, 27, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_weekday(wed) is True


def test_is_market_weekday_false_on_saturday():
    sat = datetime.datetime(2026, 5, 30, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    assert is_market_weekday(sat) is False

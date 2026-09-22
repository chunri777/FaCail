from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SHANGHAI = ZoneInfo("Asia/Shanghai")


def load_closed_days() -> set[date]:
    payload = json.loads((ROOT / "config" / "trading_calendar.json").read_text(encoding="utf-8"))
    return {date.fromisoformat(value) for value in payload["closed_weekdays"]}


def is_trading_day(day: date, closed_days: set[date]) -> bool:
    return day.weekday() < 5 and day not in closed_days


def latest_trading_day(day: date, closed_days: set[date], include_today: bool = True) -> date:
    current = day if include_today else day - timedelta(days=1)
    for _ in range(20):
        if is_trading_day(current, closed_days):
            return current
        current -= timedelta(days=1)
    raise ValueError("No trading day found in calendar window")


def trade_stage(now: datetime, closed_days: set[date]) -> str:
    local = now.astimezone(SHANGHAI)
    if not is_trading_day(local.date(), closed_days):
        return "postmarket"
    minute = local.hour * 60 + local.minute
    if minute < 9 * 60 + 30:
        return "premarket"
    if minute <= 15 * 60:
        return "intraday"
    return "postmarket"


def quote_freshness(quote_time: str | None, now: datetime, closed_days: set[date],
                    stale_after_minutes: int = 20, close_minute: str = "14:55") -> str:
    if not quote_time:
        return "missing"
    try:
        timestamp = datetime.fromisoformat(quote_time)
    except ValueError:
        return "missing"
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI)
    local = now.astimezone(SHANGHAI)
    today = local.date()
    if not is_trading_day(today, closed_days):
        return "last_trading_day" if timestamp.date() == latest_trading_day(today, closed_days) else "stale"
    stage = trade_stage(local, closed_days)
    if stage == "premarket":
        return "last_trading_day" if timestamp.date() == latest_trading_day(today, closed_days, False) else "stale"
    if timestamp.date() != today:
        return "stale"
    if stage == "postmarket":
        hour, minute = (int(part) for part in close_minute.split(":"))
        return "current" if (timestamp.hour, timestamp.minute) >= (hour, minute) else "stale"
    current_minute = local.hour * 60 + local.minute
    if 11 * 60 + 30 < current_minute < 13 * 60:
        return "current" if (timestamp.hour, timestamp.minute) >= (11, 25) else "stale"
    lag = (local - timestamp).total_seconds() / 60
    return "current" if -2 <= lag <= stale_after_minutes else "stale"


def daily_freshness(source_day: str | None, now: datetime, closed_days: set[date]) -> str:
    if not source_day:
        return "missing"
    try:
        observed = date.fromisoformat(source_day[:10])
    except ValueError:
        return "missing"
    local = now.astimezone(SHANGHAI)
    today = local.date()
    if is_trading_day(today, closed_days) and trade_stage(local, closed_days) != "premarket" and observed == today:
        return "current"
    expected = latest_trading_day(today, closed_days, include_today=False if is_trading_day(today, closed_days) else True)
    return "last_trading_day" if observed == expected else "stale"

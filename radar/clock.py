"""日期工具。卖家精灵按美国太平洋时间结算月份，报告按北京时间显示。"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def now_in(tz: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz))
    except Exception:  # noqa: BLE001 — 未知时区字符串
        return datetime.now(timezone.utc)


def to_tz(moment: datetime, tz: str) -> datetime:
    try:
        return moment.astimezone(ZoneInfo(tz))
    except Exception:  # noqa: BLE001
        return moment.astimezone(timezone.utc)


def period_of(day: date) -> str:
    return f"{day.year}{day.month:02d}"


def step_period(period: str, months: int) -> str:
    year, month = int(period[:4]), int(period[4:6])
    index = year * 12 + (month - 1) + months
    return f"{index // 12}{index % 12 + 1:02d}"


def closed_period(today: date) -> str:
    """最近一个已经结束的自然月（yyyyMM）。月度数据只对已结束的月份有值。"""
    return step_period(period_of(today), -1)


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def ms_to_day(value) -> date | None:
    """卖家精灵的时间戳是毫秒；兼容字符串和秒。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return parse_day(value) if isinstance(value, str) else None
    if number <= 0:
        return None
    if number > 1e11:
        number /= 1000.0
    return datetime.fromtimestamp(number, tz=timezone.utc).date()


def days_between(start: date | None, end: date | None) -> int | None:
    if start is None or end is None:
        return None
    return (end - start).days


def month_key(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def days_in_month(key: str) -> int:
    year, month = int(key[:4]), int(key[5:7])
    first = date(year, month, 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    return (nxt - first).days


def date_range(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)

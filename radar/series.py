"""日序列与月序列的纯计算，不调接口，便于单测。

注意：卖家精灵的日销量是根据 BSR 估算的，所以
* 以 log-BSR / BSR 中位数作为主信号，销量只用作门槛和“倍数”的直观表达；
* 不比较“销量与 BSR 背离”，二者必然一致，没有信息量。
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from . import clock


@dataclass
class Daily:
    start: date
    bsr: list
    sales: list
    price: list

    @classmethod
    def from_record(cls, series: dict | None) -> "Daily | None":
        if not series or not series.get("start"):
            return None
        start = clock.parse_day(series["start"])
        if start is None:
            return None
        return cls(start, list(series.get("bsr") or []), list(series.get("sales") or []),
                   list(series.get("price") or []))

    def __len__(self) -> int:
        return len(self.bsr)

    def day(self, index: int) -> date:
        return self.start + timedelta(days=index)

    def index_of(self, day: date) -> int:
        return (day - self.start).days

    def as_of_index(self) -> int | None:
        for i in range(len(self.bsr) - 1, -1, -1):
            if self.bsr[i] is not None or (i < len(self.sales) and self.sales[i] is not None):
                return i
        return None

    def as_of(self) -> date | None:
        i = self.as_of_index()
        return None if i is None else self.day(i)

    def window(self, name: str, end: int, length: int) -> list:
        """[end-length+1, end] 区间内的非空值。"""
        values = getattr(self, name)
        lo = max(0, end - length + 1)
        return [v for v in values[lo:end + 1] if v is not None]

    def raw_window(self, name: str, end: int, length: int) -> list:
        values = getattr(self, name)
        lo = max(0, end - length + 1)
        return values[lo:end + 1]


def build_daily(items: list[dict], keep_days: int = 420) -> dict | None:
    """把 dailyItemList 整理成连续日期数组（缺失日为 None），只保留最近 keep_days 天。"""
    parsed = []
    for item in items or []:
        day = clock.parse_day(item.get("date"))
        if day is None:
            continue
        parsed.append((day, _num(item.get("bsr")), _num(item.get("sales")), _num(item.get("price"))))
    if not parsed:
        return None
    parsed.sort(key=lambda row: row[0])
    end = parsed[-1][0]
    start = max(parsed[0][0], end - timedelta(days=keep_days - 1))
    size = (end - start).days + 1
    bsr, sales, price = [None] * size, [None] * size, [None] * size
    for day, b, s, p in parsed:
        if day < start:
            continue
        i = (day - start).days
        bsr[i], sales[i], price[i] = b, s, p
    return {"start": start.isoformat(), "bsr": bsr, "sales": sales, "price": price}


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or number < 0:
        return None
    return int(number) if number.is_integer() else round(number, 2)


def mean(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def median(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def cv(values: list) -> float | None:
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    avg = sum(values) / len(values)
    if avg <= 0:
        return None
    return statistics.pstdev(values) / avg


def log_growth(values: list) -> float | None:
    """对 ln(v+1) 做最小二乘，返回“每步”的增长率 exp(slope)-1。"""
    points = [(i, math.log(v + 1)) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return None
    n = len(points)
    mx = sum(p[0] for p in points) / n
    my = sum(p[1] for p in points) / n
    sxx = sum((p[0] - mx) ** 2 for p in points)
    if sxx == 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in points) / sxx
    return math.exp(slope) - 1


# ---------------------------------------------------------------- 月度 ----

def closed_months(months: list, as_of: date | None) -> list[tuple[str, float]]:
    """去掉 as_of 所在的未完结月份，返回 [(yyyy-mm, 月销量)]，按时间升序。"""
    current = clock.month_key(as_of) if as_of else None
    out = []
    for row in months or []:
        key, sales = row[0], row[1]
        if key == current or sales is None:
            continue
        out.append((key, float(sales)))
    out.sort()
    return out


def daily_avg_of_month(key: str, sales: float) -> float:
    return sales / clock.days_in_month(key)


# ---------------------------------------------------------------- 暴涨 ----

def surge_metrics(d: Daily, window: int, base_days: int, as_of_idx: int) -> dict | None:
    """近 window 天对比之前 base_days 天。"""
    if as_of_idx is None or as_of_idx + 1 < window + 7:
        return None
    recent_end = as_of_idx
    base_end = as_of_idx - window
    recent_bsr = median(d.window("bsr", recent_end, window))
    base_bsr = median(d.window("bsr", base_end, base_days))
    recent_sales = d.window("sales", recent_end, window)
    base_sales = d.window("sales", base_end, base_days)
    recent_avg = mean(recent_sales)
    base_avg = mean(base_sales)
    if recent_avg is None:
        return None
    recent_price = median(d.window("price", recent_end, window))
    base_price = median(d.window("price", base_end, base_days))
    return {
        "window": window,
        "recent_avg": recent_avg,
        "base_avg": base_avg or 0.0,
        "sales_ratio": (recent_avg / base_avg) if base_avg and base_avg > 0 else None,
        "recent_bsr": recent_bsr,
        "base_bsr": base_bsr,
        "bsr_ratio": (recent_bsr / base_bsr) if recent_bsr and base_bsr else None,
        "recent_price": recent_price,
        "base_price": base_price,
        "price_change": ((recent_price - base_price) / base_price)
        if recent_price is not None and base_price else None,
    }


def surge_shape(d: Daily, as_of_idx: int, base_avg: float, mult: float) -> dict:
    """近 7 天的形态：
    days_elevated 达到基线 mult 倍的天数；top2_share 增量里最大两天的占比（脉冲）；
    last3_ratio 最近 3 天日均 ÷ 近 7 天日均（走平≈1，还在涨 >1，在掉 <1）；week_ratio 近 7 天 ÷ 前 7 天日均。"""
    recent = [v for v in d.raw_window("sales", as_of_idx, 7)]
    values = [v or 0 for v in recent]
    days_elevated = sum(1 for v in values if base_avg > 0 and v >= mult * base_avg) if base_avg > 0 \
        else sum(1 for v in values if v > 0)
    excess = sorted((max(0.0, v - base_avg) for v in values), reverse=True)
    total = sum(excess)
    top2_share = (sum(excess[:2]) / total) if total > 0 else 0.0
    avg7, avg3 = mean(d.window("sales", as_of_idx, 7)), mean(d.window("sales", as_of_idx, 3))
    prev7 = mean(d.window("sales", as_of_idx - 7, 7))
    return {"days_elevated": days_elevated, "top2_share": top2_share,
            "last3_ratio": (avg3 / avg7) if avg3 is not None and avg7 else None,
            "week_ratio": (avg7 / prev7) if avg7 is not None and prev7 else None}


def same_window_last_year(d: Daily, as_of_idx: int, window: int, base_days: int) -> float | None:
    """去年同期（往前 364 天，星期对齐）同样窗口的销量倍数。"""
    shifted = as_of_idx - 364
    if shifted - window - base_days < 0:
        return None
    recent = mean(d.window("sales", shifted, window))
    base = mean(d.window("sales", shifted - window, base_days))
    if recent is None or not base:
        return None
    return recent / base


def find_pulses(d: Daily, as_of_idx: int, lookback: int, mult: float, min_sales: float,
                revert: float) -> list[dict]:
    """近 lookback 天内 1~3 天的尖峰：冲到基线 mult 倍以上，随后 7 天内回落到 revert 倍以下。"""
    pulses = []
    start = max(28, as_of_idx - lookback + 1)
    i = start
    while i <= as_of_idx:
        base = median(d.window("sales", i - 1, 28))
        value = d.sales[i] if i < len(d.sales) else None
        if not base or value is None or value < max(mult * base, min_sales):
            i += 1
            continue
        run_end = i
        while run_end + 1 <= as_of_idx and (d.sales[run_end + 1] or 0) >= mult * base:
            run_end += 1
        length = run_end - i + 1
        days_after = min(7, as_of_idx - run_end)
        after_avg = mean(d.window("sales", run_end + days_after, days_after)) if days_after >= 3 else None
        if length <= 3 and after_avg is not None and after_avg < revert * base:
            pulses.append({"day": d.day(i).isoformat(), "length": length,
                           "peak": max(d.sales[i:run_end + 1]), "base": base})
        i = run_end + 1
    return pulses


def sum_sales_between(d: Daily, after: date, upto: date) -> float | None:
    """(after, upto] 区间内的销量合计。"""
    lo = d.index_of(after) + 1
    hi = d.index_of(upto)
    if hi < 0 or lo > hi:
        return None
    lo = max(lo, 0)
    values = [v for v in d.sales[lo:hi + 1] if v is not None]
    return float(sum(values)) if values else None

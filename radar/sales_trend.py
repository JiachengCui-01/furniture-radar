"""商品自己往年的月销量（季节性商品 detect/seasonality.item_season 的数据）。

日数据（asin_prediction）只有约 400 天，一个商品只看得到一个旺季，分不清“每年这时候都卖得好”和“那一年碰巧那段时间卖得好”。
卖家精灵 asin_sales_trend 返回约 27 个月的父体 / 子体月销量（实测 2026-10 查到 2024-08 起），父体月销量和日数据接口的月销量基本一致
（实测长凳 2025-09~2026-09：477/474、1141/1069、804/875…）。用父体的，和日数据、月度名单同一口径。

只查季节性品类里、上架满 item_min_age_days 天、最近一年就已经像季节性商品的追踪商品（candidates），每个商品每月查一次，
只用核心刷新做完后剩下的预算、每期最多 budget.trend_calls 次：
rec["sales_trend"] = {"month": 查的那个月（YYYY-MM，这个月还没结束，判定时不用）, "points": [[YYYY-MM, 父体月销量]]}
"""
from __future__ import annotations

from datetime import date

from . import clock, log
from .detect import seasonality
from .vendor import BudgetExhausted, Vendor


def candidates(state: dict, pool: list[str], profiles: dict, today: date, cfg: dict) -> list[str]:
    """要查（或这个月该重查）的商品：在追踪池里、所在子类目是季节性品类、上架满 item_min_age_days 天、这个月还没查过，
    而且最近一年就已经像季节性商品：日数据接口顺带给的近 14 个月月销量（rec["months"]，不另花调用）按同样的条件判、
    只要求 1 年的旺季也成立。最近一年都不成立的，两年一定不成立，不必花调用查往年
    （2026-10-23 数据：80 个上架满一年的候选里只剩 3 个要查）。"""
    sc = cfg["thresholds"]["seasonality"]
    min_age = int(sc.get("item_min_age_days", 365))
    month = clock.month_key(today)
    out = []
    for asin in pool:
        rec = state["asins"].get(asin) or {}
        prof = profiles.get(rec.get("node")) or {}
        age = clock.days_between(clock.parse_day(rec.get("available")), today)
        if not prof.get("seasonal") or age is None or age < min_age or (rec.get("sales_trend") or {}).get("month") == month:
            continue
        last_year = seasonality.trend_months({"month": month, "points": [[m[0], m[1]] for m in rec.get("months") or []
                                                                         if m[1] is not None]})
        if (seasonality.item_season(last_year, prof, {**sc, "item_min_cycles": 1}) or {}).get("seasonal"):
            out.append(asin)
    return out


def fetch(vendor: Vendor, cfg: dict, state: dict, asins: list[str], today: date, limit: int) -> int:
    """最多 limit 次调用。没拿到的不记（下期再查）。返回调用次数。"""
    month = clock.month_key(today)
    start = vendor.billable_calls
    for asin in asins:
        if vendor.billable_calls - start >= limit:
            break
        try:
            reply = vendor.call("asin_sales_trend", vendor.args("asin_sales_trend", nested=False,
                                                                marketplace=cfg["marketplace"], asin=asin),
                                purpose="往年月销量", prune=False)
        except BudgetExhausted:
            break
        rows = (reply.data or {}).get("salesTrendPoints") if isinstance(reply.data, dict) else None
        if not reply.ok or not isinstance(rows, list):
            continue
        points = sorted([str(r.get("month"))[:7], r.get("parentUnitSales")] for r in rows
                        if r.get("month") and r.get("parentUnitSales") is not None)
        state["asins"][asin]["sales_trend"] = {"month": month, "points": points}
    calls = vendor.billable_calls - start
    if calls:
        log.info(f"往年月销量：查了 {calls} 个季节性品类的商品")
    return calls

"""三类“正向”判定：突然爆火、真正持续热销、潜力。阈值全部来自 config.yaml。"""
from __future__ import annotations

import math
from datetime import date

from .. import clock
from ..series import (Daily, closed_months, cv, daily_avg_of_month, log_growth, mean,
                      same_window_last_year, surge_metrics, surge_shape)


def current_rating(rec: dict | None, row: dict | None) -> float | None:
    """当前评分：优先取最近一次日数据里的（asin_prediction，实时），没有才用月度榜单的（月末快照，可能过时）。"""
    rating = ((rec or {}).get("obs") or [{}])[-1].get("rating")
    return rating if rating is not None else (row or {}).get("rating")


def low_rating(rating: float | None, min_rating: float | None) -> bool:
    """评分低于门槛的商品不进入任何榜单。还没有评分（没有评论）的不算低分。"""
    return bool(min_rating) and bool(rating) and rating < min_rating


def deal_overlap(start: date, end: date, deal_windows: list[dict]) -> str | None:
    for deal in deal_windows or []:
        lo, hi = clock.parse_day(deal.get("start")), clock.parse_day(deal.get("end"))
        if lo and hi and start <= hi and end >= lo:
            return str(deal.get("name") or "大促")
    return None


def _restock(d: Daily, as_of_idx: int, best: dict, cfg: dict) -> dict | None:
    """断货恢复：对比窗口里大部分日子没有销量（BSR 飙到几百万），而更早之前卖得和现在差不多。
    这类“暴涨”只是恢复供货，不是新需求。"""
    base_end = as_of_idx - best["window"]
    base = d.raw_window("sales", base_end, cfg["base_days"])
    zero_days = sum(1 for v in base if not v)
    if zero_days < max(7, 0.25 * len(base)):
        return None
    earlier = [v for v in d.window("sales", base_end - cfg["base_days"], 90) if v]
    if len(earlier) < 14:
        return None  # 之前也没怎么卖过：真正的新品起量
    earlier_avg = sum(earlier) / len(earlier)
    if earlier_avg < cfg.get("restock_ratio", 0.5) * best["recent_avg"]:
        return None
    return {"zero_days": zero_days, "earlier_avg": round(earlier_avg, 1)}


def evaluate_surge(d: Daily, as_of_idx: int, cfg: dict, deal_windows: list[dict]) -> dict | None:
    best = None
    for window in cfg["windows"]:
        m = surge_metrics(d, window, cfg["base_days"], as_of_idx)
        if not m:
            continue
        from_zero = m["base_avg"] < 0.5
        volume_ok = m["recent_avg"] >= cfg["min_daily_sales"]
        bsr_ok = m["bsr_ratio"] is not None and m["bsr_ratio"] <= cfg["bsr_ratio"]
        if from_zero:
            bsr_ok = bsr_ok or m["base_bsr"] is None
            sales_ok = volume_ok
        else:
            sales_ok = m["sales_ratio"] is not None and m["sales_ratio"] >= cfg["sales_ratio"]
        if not (volume_ok and bsr_ok and sales_ok):
            continue
        ratio = m["sales_ratio"] if m["sales_ratio"] is not None else 5.0
        strength = min(ratio, 10.0) * math.log1p(m["recent_avg"])
        if best is None or strength > best["strength"]:
            best = {**m, "strength": round(strength, 3), "from_zero": from_zero}
    if best is None:
        return None

    restock = _restock(d, as_of_idx, best, cfg)
    if restock:
        return {**best, "kind": "断货恢复", "restock": True, **restock, "seasonal": False,
                "last_year_ratio": None, "deal": None, "price_driven": False,
                "days_elevated": 0, "top2_share": 0.0}

    shape = surge_shape(d, as_of_idx, best["base_avg"], cfg["sustained_mult"])
    if best["from_zero"]:
        kind = "新品起量"
    elif shape["days_elevated"] >= cfg["sustained_days"]:
        kind = "持续型"
    elif shape["top2_share"] >= cfg["pulse_share"]:
        kind = "脉冲型"
    else:
        kind = "爬升型"

    seasonal_ratio = same_window_last_year(d, as_of_idx, best["window"], cfg["base_days"])
    start = d.day(as_of_idx - best["window"] + 1)
    deal = deal_overlap(start, d.day(as_of_idx), deal_windows)
    price_driven = best["price_change"] is not None and best["price_change"] <= -cfg["price_drop"]
    return {
        **best,
        "restock": False,
        "kind": kind,
        "days_elevated": shape["days_elevated"],
        "top2_share": round(shape["top2_share"], 3),
        "seasonal": seasonal_ratio is not None and seasonal_ratio >= cfg["seasonal_ratio"],
        "last_year_ratio": seasonal_ratio,
        "deal": deal,
        "price_driven": price_driven,
    }


def evaluate_hot(months: list, d: Daily | None, as_of: date | None, node_threshold: float | None,
                 cfg: dict) -> dict | None:
    closed = closed_months(months, as_of)
    look = closed[-cfg["lookback_months"]:]
    if node_threshold is None or len(look) < cfg["months_required"]:
        return None
    hits = sum(1 for _, s in look if s >= node_threshold)
    six = [s for _, s in closed[-6:]]
    variation = cv(six)
    trend = log_growth([s for _, s in closed[-3:]])
    six_daily = mean([daily_avg_of_month(k, s) for k, s in closed[-6:]])
    recent = None
    if d is not None and as_of is not None:
        recent = mean(d.window("sales", d.index_of(as_of), 28))
    recent_ratio = (recent / six_daily) if recent is not None and six_daily else None

    ok = (
        hits >= cfg["months_required"]
        and variation is not None and variation <= cfg["max_cv"]
        and (trend is None or trend >= cfg["min_trend"])
        and (recent_ratio is None or recent_ratio >= cfg["min_recent_ratio"])
    )
    if not ok:
        return None
    return {
        "months_hit": hits,
        "lookback": len(look),
        "threshold": node_threshold,
        "cv": round(variation, 3),
        "trend": None if trend is None else round(trend, 3),
        "avg_monthly": round(mean(six) or 0, 1),
        "recent_daily": None if recent is None else round(recent, 2),
        "recent_ratio": None if recent_ratio is None else round(recent_ratio, 2),
    }


def evaluate_potential(rec: dict, d: Daily, as_of: date, cfg: dict) -> dict | None:
    available = clock.parse_day(rec.get("available"))
    age = clock.days_between(available, as_of)
    if age is None or not (cfg["min_age_days"] <= age <= cfg["max_age_days"]):
        return None
    obs = (rec.get("obs") or [{}])[-1]
    ratings, rating = obs.get("ratings"), obs.get("rating")
    if ratings is not None and ratings >= cfg["max_ratings"]:
        return None

    idx = d.index_of(as_of)
    avg28 = mean(d.window("sales", idx, 28))
    if avg28 is None or avg28 < cfg["min_daily_sales"]:
        return None

    # 近 28 天 vs 再之前 28 天（不重叠），外加完整月份的月环比
    prev28 = mean(d.window("sales", idx - 28, 28))
    recent_ratio = avg28 / prev28 if prev28 else None
    launch_month = clock.month_key(available)
    full = [(k, s) for k, s in closed_months(rec.get("months") or [], as_of) if k > launch_month]
    growth = log_growth([s for _, s in full[-3:]]) if len(full) >= 2 else None
    ok = (recent_ratio is not None and recent_ratio >= cfg["min_recent_ratio"]
          and (growth is None or growth >= cfg["min_monthly_growth"]))
    if not ok:
        return None
    return {
        "age_days": age,
        "growth": None if growth is None else round(growth, 3),
        "recent_ratio": None if recent_ratio is None else round(recent_ratio, 2),
        "avg28": round(avg28, 2),
        "ratings": ratings,
        "rating": rating,
    }

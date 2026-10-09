"""三类“正向”判定：突然爆火、持续热销、潜力，以及共用的评分门槛和“回落”。阈值全部来自 config.yaml。
报告里每个板块的文字说明（report/render.py 的 section_hints）与这里的条件一一对应，改条件时一起改。"""
from __future__ import annotations

import math
from datetime import date

from .. import clock
from ..series import (Daily, closed_months, cv, daily_avg_of_month, log_growth, mean,
                      same_window_last_year, surge_metrics, surge_shape)


def current_rating(rec: dict | None, row: dict | None) -> tuple[float | None, float | None]:
    """(评分, 评论数)：优先取最近一次日数据里的（asin_prediction），没有才用榜单行的（月度快照可能过时）。"""
    obs = ((rec or {}).get("obs") or [{}])[-1]
    if obs.get("rating") is not None:
        return obs["rating"], obs.get("ratings")
    return (row or {}).get("rating"), (row or {}).get("ratings")


def rating_floor(ratings: float | None, th: dict) -> float | None:
    """上榜需要的最低评分。评论少时几条差评就能把评分拉到 4.0 以下，卖家精灵的评分也比亚马逊晚几天，
    所以评论不到 few_ratings 条的商品要求 few_ratings_min_rating。"""
    floor = th.get("min_rating")
    if floor and ratings is not None and ratings < (th.get("few_ratings") or 0):
        return max(floor, th.get("few_ratings_min_rating") or floor)
    return floor


def low_rating(rating: float | None, ratings: float | None, th: dict) -> bool:
    """评分不达标的商品不进入任何榜单。还没有评分（没有评论）的不算低分。"""
    floor = rating_floor(ratings, th)
    return bool(floor) and bool(rating) and rating < floor


def momentum(d: Daily, as_of_idx: int, cfg: dict) -> dict:
    """回落 = 近 7 天日均比最近 4 周里最高的一周（滚动 7 天）低 20% 以上，或最近 3 天日均比近 7 天低 20% 以上。
    爆火、潜力、上升中都要求没有回落。只比“近 28 天 vs 之前 28 天”或月环比，会被刚开卖时的从零起量撑高
    （实测：9 月初开卖、9 月中旬到峰值、10 月第一周跌到峰值三分之一的商品，28 天对比仍是 ×5）；
    只看“近 7 天有几天达标”，冲高后正在往下掉的也会被当成持续（实测：峰值周日均 76~94，最近一周 76→32）。"""
    def ratio(a, b):
        return round(a / b, 3) if a is not None and b else None

    avg7 = mean(d.window("sales", as_of_idx, 7))
    weeks = [mean(d.window("sales", as_of_idx - k, 7)) for k in range(22)]  # 窗口都落在近 28 天内
    peak = max((w for w in weeks if w is not None), default=None)
    vs_peak = ratio(avg7, peak)
    last3 = ratio(mean(d.window("sales", as_of_idx, 3)), avg7)
    fading = ((vs_peak is not None and vs_peak < cfg.get("min_vs_peak_week", 0))
              or (last3 is not None and last3 < cfg.get("min_last3_vs_7d", 0)))
    return {"vs_peak": vs_peak, "last3": last3, "fading": fading}


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

    # 形态：依次判断，每种都有自己的条件；都不符合的 kind=None，不算爆火（“是否回落”在 analyze 里统一判断）
    shape = surge_shape(d, as_of_idx, best["base_avg"], cfg["sustained_mult"])
    last3, week = shape["last3_ratio"], shape["week_ratio"]
    if best["from_zero"]:
        kind = "新品爆发"   # 之前 28 天几乎没卖
    elif shape["top2_share"] >= cfg["pulse_share"]:
        kind = "脉冲型"     # 增量集中在 ≤2 天
    elif shape["days_elevated"] >= cfg["sustained_days"] and last3 is not None \
            and abs(last3 - 1) <= cfg["steady_band"]:
        kind = "持续型"     # 近 7 天大多数天在高位，且最近 3 天走平
    elif week is not None and week >= cfg["climb_ratio"] and last3 is not None and last3 > 1:
        kind = "爬升型"     # 比前一周高，且最近 3 天还在往上走
    else:
        kind = None

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
        "last3_ratio": None if last3 is None else round(last3, 3),
        "week_ratio": None if week is None else round(week, 3),
        "base_days": cfg["base_days"],
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

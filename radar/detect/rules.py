"""三类“正向”判定：突然爆火、持续热销、潜力，以及共用的评分门槛、“回落”和“老品换链接”。阈值全部来自 config.yaml。
报告里每个板块的文字说明（report/render.py 的 section_hints）与这里的条件一一对应，改条件时一起改。"""
from __future__ import annotations

import math
from datetime import date

from .. import clock
from ..series import (Daily, closed_months, cv, daily_avg_of_month, log_growth, mean, median,
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


def relisted(d: Daily | None, as_of_idx: int | None, ratings: float | None, age: int | None,
             cfg: dict) -> dict | None:
    """老品换链接：新子体挂进老父体（或老品换了新 ASIN），继承了评论和排名，上架天数不代表商品是新的。
    条件：上架 ≤ max_age_days 天，且 评论数 ≥ 这个 ASIN 自己累计销量的 review_share，
          或 评论数 ≥ 累计销量的 review_share_fast 且 上架第一周日均 ≥ 近 7 天日均的 fast_share（且 ≥ fast_min_daily 件）。
    累计销量 = 日数据里这个 ASIN 卖出的全部件数；第一周 = 从第一天卖出东西起的 7 天（之前 0 销量的日子不算）。
    正常留评率 1~3%、Vine 最多 30 条，只“第一周就卖得多”、评论正常的多是投广告推起来的真新品，不算。"""
    if d is None or as_of_idx is None or age is None or age > cfg["max_age_days"] or not ratings:
        return None
    values = [v for v in d.sales[: as_of_idx + 1] if v is not None]
    if not values:
        return None
    total = sum(values)
    share = ratings / max(total, 1.0)
    selling = [k for k, v in enumerate(values) if v > 0]
    first7 = mean(values[selling[0]: selling[0] + 7]) if selling else None
    last7 = mean(d.window("sales", as_of_idx, 7))
    fast = bool(first7 is not None and last7 and first7 >= cfg["fast_min_daily"]
                and first7 >= cfg["fast_share"] * last7 - 1e-9)
    if not (share >= cfg["review_share"] - 1e-9 or (fast and share >= cfg["review_share_fast"] - 1e-9)):
        return None
    return {"ratings": int(ratings), "sales": int(round(total)), "share": round(share, 3), "fast": fast,
            "first7": None if first7 is None else round(first7, 1), "last7": None if last7 is None else round(last7, 1),
            "age": age}


def relist_text(r: dict) -> str:
    text = f"老品换链接（上架 {r['age']} 天已有 {r['ratings']} 条评论，是自己累计销量的 {r['share']:.0%}"
    if r["fast"]:
        text += f"；开卖第一周日均 {r['first7']:g} 件，近 7 天 {r['last7']:g} 件"
    return text + "）"


def momentum(d: Daily, as_of_idx: int, cfg: dict) -> dict:
    """回落 = 下面任一（爆火、潜力、上升中都要求没有回落）：
      ① 冲高后正在往下掉：最近 3 天日均 < 近 4 周里最高的 3 天日均 × min_vs_peak_3d，并且 < 再之前 3 天日均 × min_vs_prev_3d；
      ② 这一周整体变低：近 7 天销量的中位数 < 再之前 7 天的中位数 × min_vs_prev_week（中位数不受一两天的尖峰影响）；
      ③ 冲高后连着往下掉：近 7 天里销量最高的那天之后，直到最近一天都没有回升过（至少 slide_days 天），
         且最近一天 ≤ 最高那天 × slide_max_vs_peak。
    为什么要有：只比“近 28 天 vs 之前 28 天”或月环比，会被刚开卖时的从零起量撑高（实测：9 月中旬到峰值、
    10 月第一周跌到峰值三分之一的商品，28 天对比仍是 ×5）；只看“近 7 天有几天达标”，冲高后正在往下掉的也会被当成持续。
    为什么是这个条件（回测：约 150 个追踪商品、400 天日数据里 3343 次“增长商品”判定，看之后两周的销量）：
      旧规则（近 7 天比最高一周低 20% 或最近 3 天比近 7 天低 20%）标出的只有 25% 之后真的跌了，53% 其实稳住或继续涨，
      主要是“冲高一两天后回到原来的高位”被当成回落；峰值在近 7 天里的（冲到 46 件后每天往下掉到 17 件）又抓不到。
      只用 ①：标出的 38% 之后跌了、40% 稳住，但两周里慢慢往下走的（60→35、34→20）抓不到，会被当成潜力 / 上升中。
      ① + ②（0.75 / 0.9 / 0.85）：标出的 30% 之后跌了、48% 稳住，真跌的漏掉 53%。但峰值只有几天前的冲高回落
      （405→338→232→164），“再之前 3 天”里还有冲高前的日子，①② 都抓不到，“仍在上涨”反而成立。
      ① + ② + ③：标出的 30% 之后跌了、47% 稳住，真跌的漏掉 49%（旧规则 25% / 53% / 59%），三项都比旧规则好；③ 单独 40% 之后真跌了。
      ① 要求“比再之前 3 天低 10% 以上”而不是“低一点就算”：冲高一两天后回到原来水平的，日常波动会让它看起来“又低了一点”。"""
    def ratio(a, b):
        return round(a / b, 3) if a is not None and b else None

    avg3, prev3 = mean(d.window("sales", as_of_idx, 3)), mean(d.window("sales", as_of_idx - 3, 3))
    triples = [mean(d.window("sales", as_of_idx - k, 3)) for k in range(26)]  # 窗口都落在近 28 天内
    peak3 = max((t for t in triples if t is not None), default=None)
    vs_peak = ratio(avg3, peak3)
    vs_prev = ratio(avg3, prev3)
    falling = (vs_peak is not None and vs_peak < cfg.get("min_vs_peak_3d", 0)
               and vs_prev is not None and vs_prev < cfg.get("min_vs_prev_3d", 1))
    med7, prev_med7 = median(d.window("sales", as_of_idx, 7)), median(d.window("sales", as_of_idx - 7, 7))
    vs_prev_week = ratio(med7, prev_med7)
    lower_week = vs_prev_week is not None and vs_prev_week < cfg.get("min_vs_prev_week", 0)
    week = d.window("sales", as_of_idx, 7)  # 近 7 天有数据的日子（大促日已去掉），按日期顺序
    slide_days, peak_day, last_day = 0, None, None
    if week:
        top = max(range(len(week)), key=lambda j: (week[j], j))  # 最高那天（并列取最近的）
        after = week[top:]
        if all(b <= a for a, b in zip(after, after[1:])):
            slide_days, peak_day, last_day = len(after) - 1, week[top], week[-1]
    sliding = (slide_days >= int(cfg.get("slide_days", 10 ** 6)) and peak_day
               and last_day <= cfg.get("slide_max_vs_peak", 0) * peak_day)
    return {"avg3": avg3, "prev3": prev3, "peak3": peak3, "vs_peak": vs_peak, "vs_prev": vs_prev,
            "med7": med7, "prev_med7": prev_med7, "vs_prev_week": vs_prev_week,
            "slide_days": slide_days, "peak_day": peak_day, "last_day": last_day,
            "falling": falling, "lower_week": lower_week, "sliding": bool(sliding),
            "fading": falling or lower_week or bool(sliding)}


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


def evaluate_surge(d: Daily, as_of_idx: int, cfg: dict, oos_bsr: float | None = None) -> dict | None:
    """d 已去掉大促日（analyze.evaluate 里做）。对比期里断货的日子（BSR ≥ oos_bsr）不算进之前的水平。"""
    best = None
    for window in cfg["windows"]:
        m = surge_metrics(d, window, cfg["base_days"], as_of_idx, oos_bsr)
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
                "last_year_ratio": None, "price_driven": False,
                "days_elevated": 0, "top2_share": 0.0}

    # 形态：依次判断，每种都有自己的条件。增量集中在 ≤2 天的是“短时脉冲”，不算爆火；
    # 其余都不符合的 kind=None，也不算爆火（“是否回落”在 analyze 里统一判断）
    shape = surge_shape(d, as_of_idx, best["base_avg"], cfg["sustained_mult"])
    last3, week = shape["last3_ratio"], shape["week_ratio"]
    pulse = shape["top2_share"] >= cfg["pulse_share"]
    if best["from_zero"]:
        kind = "新品爆发"   # 之前 28 天几乎没卖
    elif pulse:
        kind = None
    elif shape["days_elevated"] >= cfg["sustained_days"] and last3 is not None \
            and abs(last3 - 1) <= cfg["steady_band"]:
        kind = "稳在高位"   # 近 7 天大多数天在高位，且最近 3 天走平
    elif week is not None and week >= cfg["climb_ratio"] and last3 is not None and last3 > 1:
        kind = "仍在上涨"   # 比前一周高，且最近 3 天还在往上走
    else:
        kind = None

    seasonal_ratio = same_window_last_year(d, as_of_idx, best["window"], cfg["base_days"])
    price_driven = best["price_change"] is not None and best["price_change"] <= -cfg["price_drop"]
    return {
        **best,
        "restock": False,
        "kind": kind,
        "days_elevated": shape["days_elevated"],
        "top2_share": round(shape["top2_share"], 3),
        "pulse": pulse and not best["from_zero"],
        "last3_ratio": None if last3 is None else round(last3, 3),
        "week_ratio": None if week is None else round(week, 3),
        "base_days": cfg["base_days"],
        "seasonal": seasonal_ratio is not None and seasonal_ratio >= cfg["seasonal_ratio"],
        "last_year_ratio": seasonal_ratio,
        "price_driven": price_driven,
    }


def evaluate_hot(months: list, d: Daily | None, as_of: date | None, node_threshold: float | None,
                 cfg: dict, season: dict | None = None, threshold_month: str | None = None) -> dict | None:
    """season：同子类目老链接（不含这个商品自己）的销量水平 {"months": {"2026-04": 日均合计, …}, "recent": 近 28 天日均合计,
    "n": 个数}（detect/seasonality.py 的 month_levels / others）。有、且 cfg["deseasonalize"] 时先按品类季节调整：
    每个月的销量 × (这 6 个月品类平均水平 ÷ 当月品类水平)，近 28 天同样调整；门槛线（threshold_month 那个月的子类目第 N 名）
    也调到 6 个月平均水平。调整后再看下面四条。"""
    closed = closed_months(months, as_of)
    look = closed[-cfg["lookback_months"]:]
    if node_threshold is None or len(look) < cfg["months_required"]:
        return None
    keys = [k for k, _ in closed[-6:]]
    level = (season or {}).get("months") or {}
    adjusted = bool(season and cfg.get("deseasonalize") and season.get("recent")
                    and all(level.get(k) for k in keys))
    avg_level = mean([level[k] for k in keys]) if adjusted else None

    def adj(key: str, value: float) -> float:
        return value * avg_level / level[key] if adjusted else value

    threshold = node_threshold
    if adjusted and threshold_month and level.get(threshold_month):
        threshold = node_threshold * avg_level / level[threshold_month]
    hits = sum(1 for k, s in look if adj(k, s) >= threshold)
    six = [adj(k, s) for k, s in closed[-6:]]
    variation = cv(six)
    trend = log_growth([adj(k, s) for k, s in closed[-3:]])
    six_daily = mean([daily_avg_of_month(k, adj(k, s)) for k, s in closed[-6:]])
    recent = None
    if d is not None and as_of is not None:
        recent = mean(d.window("sales", d.index_of(as_of), 28))
        if recent is not None and adjusted:
            recent = recent * avg_level / season["recent"]
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
        "threshold": round(threshold, 1),
        "adjusted": adjusted,
        "season_n": (season or {}).get("n") if adjusted else None,
        "cv": round(variation, 3),
        "trend": None if trend is None else round(trend, 3),
        "avg_monthly": round(mean([s for _, s in closed[-6:]]) or 0, 1),  # 实际月销量（不调整），排序和展示用
        "recent_daily": None if recent is None else round(recent, 2),
        "recent_ratio": None if recent_ratio is None else round(recent_ratio, 2),
    }


def evaluate_potential(rec: dict, d: Daily, as_of: date, cfg: dict, oos_bsr: float | None = None) -> dict | None:
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

    # 近 28 天 vs 再之前 28 天（不重叠，断货 / 还没开卖的日子不算），外加完整月份的月环比
    prev28 = mean(d.in_stock_window("sales", idx - 28, 28, oos_bsr))
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

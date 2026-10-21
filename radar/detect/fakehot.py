"""假爆火 / 异常信号评分。≥suspect_score 为“疑似”，≥high_score 为“高度疑似”。

报告里统一表述为“异常信号”，给出依据，由人判断，不直接下结论。

| 分 | 信号 | 首期可用 |
| +2 | 留评率 ≥ max(子类目中位数×3, 6%) 且本月新增评论 ≥10 | ✓ |
| +2 | 新品（≤90 天）累计评论数 / 累计销量 ≥ 10% | ✓ |
| +2 | 两期之间新增评论 / 新增销量 ≥ 15%（或几乎无销量却涨评论） | 第 2 期起 |
| +1 | 45 天内评分上升 ≥0.2 且评论数 ≥50 | 第 2 期起 |
| +1 | 1~3 天脉冲后迅速回落（不在大促日，见 events.py） | ✓ |
| +2 | 评论核查：近 60 天评论 ≥30% 集中在 3 天 | ✓（需额外调用） |
| +1 | 评论核查：非验证购买占比 ≥50% | ✓（需额外调用） |
| 标签 | 评论数暴增 ≥30% 且父体/变体数变化 → 变体合并（不计分） | 第 2 期起 |

评论数是整个父体共享的，而销量是单个子体的，所以变体多（> max_variations_for_ratio）的商品不算留评率，
也不算两种“评论/销量”比（实测：留评率这一项曾经对 15、17 个变体的商品也计分，本期 8 个异常商品里 4 个靠它过线）。
老品换链接（rules.relisted）的评论是从老链接继承来的，不算“新品评论/销量比”。
"""
from __future__ import annotations

from datetime import date, timedelta

from .. import clock
from ..series import Daily, find_pulses, sum_sales_between


def _variations_ok(rec: dict, cfg: dict) -> bool | None:
    v = rec.get("variations")
    if v is None:
        return None
    return v <= cfg["max_variations_for_ratio"]


def previous_obs(rec: dict, as_of: date) -> dict | None:
    for entry in reversed(rec.get("obs") or []):
        day = clock.parse_day(entry.get("date"))
        if day and day < as_of and entry.get("ratings") is not None:
            return entry
    return None


def ratings_jump(rec: dict, as_of: date) -> float | None:
    obs = rec.get("obs") or []
    if not obs:
        return None
    current = obs[-1].get("ratings")
    prev = previous_obs(rec, as_of)
    if current is None or prev is None or not prev.get("ratings"):
        return None
    return (current - prev["ratings"]) / prev["ratings"]


def merge_detected(rec: dict, disc_row: dict | None) -> bool:
    """asin_detail 核查结果与之前记录相比，父体或变体数发生了变化。"""
    check = (rec.get("checks") or {}).get("detail")
    if not check:
        return False
    before_parent = (disc_row or {}).get("parent") or rec.get("parent")
    before_vars = (disc_row or {}).get("variations") or rec.get("variations")
    parent_changed = bool(check.get("parent") and before_parent and check["parent"] != before_parent)
    vars_changed = bool(check.get("variations") and before_vars and
                        abs(check["variations"] - before_vars) >= max(2, 0.3 * before_vars))
    return parent_changed or vars_changed


def score(rec: dict, d: Daily | None, as_of: date | None, disc_row: dict | None,
          node_median_rate: float | None, cfg: dict, event_days=(), *, relisted: bool = False) -> dict:
    """d 是完整的日数据（不去掉大促日）；event_days：大促日（events.detect），落在大促日的脉冲不计分；
    relisted：老品换链接，不算“新品评论/销量比”。"""
    signals: list[dict] = []
    tags: list[str] = []

    def add(code: str, points: int, text: str) -> None:
        signals.append({"code": code, "points": points, "text": text})

    var_ok = _variations_ok(rec, cfg)

    # 1. 留评率（来自月度数据）：评论是父体共享的，变体多的不算
    if disc_row and disc_row.get("ratings_rate") is not None and var_ok is not False:
        rate = disc_row["ratings_rate"]
        new_reviews = disc_row.get("ratings_cv") or 0
        bar = max(cfg["review_rate_floor"], cfg["review_rate_mult"] * (node_median_rate or 0))
        if rate >= bar and new_reviews >= cfg["min_new_reviews"]:
            add("review_rate", 2, f"留评率 {rate:.1f}%，远高于子类目中位数 {node_median_rate or 0:.1f}%"
                                  f"（当月新增评论 {int(new_reviews)} 条）")

    if d is None or as_of is None:
        return _finish(signals, tags, cfg)
    as_of_idx = d.index_of(as_of)
    current_obs = (rec.get("obs") or [{}])[-1]
    ratings = current_obs.get("ratings")

    # 2. 新品评论/销量比
    available = clock.parse_day(rec.get("available"))
    age = clock.days_between(available, as_of)
    if (age is not None and 0 <= age <= cfg["new_listing_days"] and ratings and ratings >= 20
            and var_ok is not False and not relisted):
        launch = clock.month_key(available)
        units = sum((m[1] or 0) for m in rec.get("months") or [] if m[0] >= launch)
        if units > 0:
            ratio = ratings / units
            bar = cfg["new_listing_review_ratio"] * (1 if var_ok is not False else 2.5)
            if ratio >= bar:
                add("new_listing_reviews", 2,
                    f"上架仅 {age} 天，累计评论 {int(ratings)} 条 / 累计销量约 {int(units)} 件（{ratio:.0%}）")

    # 3. 两期之间的评论增速
    prev = previous_obs(rec, as_of)
    merged = merge_detected(rec, disc_row)
    if merged:
        tags.append("变体合并")
    if prev and ratings is not None:
        delta_ratings = ratings - prev["ratings"]
        prev_day = clock.parse_day(prev["date"])
        delta_units = sum_sales_between(d, prev_day, as_of) if prev_day else None
        if delta_ratings >= cfg["min_new_reviews"] and not merged and var_ok is not False:
            bar = max(cfg["run_review_ratio"], cfg["review_rate_mult"] * (node_median_rate or 0) / 100)
            if not delta_units:
                add("reviews_without_sales", 2,
                    f"{prev['date']} 以来评论 +{int(delta_ratings)} 条，同期几乎没有销量")
            elif delta_ratings / delta_units >= bar:
                add("review_velocity", 2,
                    f"{prev['date']} 以来评论 +{int(delta_ratings)} 条，销量约 {int(delta_units)} 件"
                    f"（{delta_ratings / delta_units:.0%}）")

        # 4. 评分跳升
        prev_rating, rating = prev.get("rating"), current_obs.get("rating")
        if (prev_rating is not None and rating is not None and prev_day
                and (as_of - prev_day).days <= 45
                and rating - prev_rating >= cfg["rating_jump"] - 1e-9
                and (ratings or 0) >= cfg["rating_jump_min_ratings"]):
            add("rating_jump", 1, f"{(as_of - prev_day).days} 天内评分 {prev_rating} → {rating}")

    # 5. 短时脉冲
    for pulse in find_pulses(d, as_of_idx, 30, cfg["pulse_mult"], cfg["pulse_min_sales"], cfg["pulse_revert"]):
        day = clock.parse_day(pulse["day"])
        if any(day + timedelta(days=k) in event_days for k in range(pulse["length"])):
            if "大促脉冲" not in tags:
                tags.append("大促脉冲")
            continue
        add("pulse", 1, f"{pulse['day']} 起 {pulse['length']} 天冲到 {int(pulse['peak'])} 件/天"
                        f"（基线约 {pulse['base']:.0f}），随后迅速回落")
        break

    # 6. 评论核查
    check = (rec.get("checks") or {}).get("review")
    if check and check.get("n", 0) >= 10:
        if check.get("burst_share", 0) >= cfg["review_burst_share"]:
            add("review_burst", 2, f"近 60 天评论有 {check['burst_share']:.0%} 集中在 3 天内"
                                   f"（{', '.join(check.get('burst_days', [])[:3])}）")
        if check.get("unverified_share", 0) >= cfg["unverified_share"]:
            add("unverified", 1, f"抽查评论中非验证购买占 {check['unverified_share']:.0%}")
        if check.get("vine_share", 0) >= 0.3:
            tags.append("Vine 评论多")

    return _finish(signals, tags, cfg)


def _finish(signals: list[dict], tags: list[str], cfg: dict) -> dict:
    total = sum(s["points"] for s in signals)
    level = "high" if total >= cfg["high_score"] else "suspect" if total >= cfg["suspect_score"] else None
    return {"score": total, "level": level, "signals": signals, "tags": tags}

"""对可疑商品做额外核查（每期最多 review_checks 个，每个 1 次调用）：

* 评论核查（review）：近 60 天评论是否集中在少数几天、非验证购买占比、Vine 占比。
* 详情核查（asin_detail）：评论数暴增时看父体/变体是否变化，区分“变体合并”和“刷评”。
14 天内核查过的不再重复调用。
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from . import clock, log, materials
from .detect.fakehot import ratings_jump
from .vendor import BudgetExhausted, Vendor

RECHECK_DAYS = 14


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes", "y")


def summarize_reviews(rows: list[dict], as_of: date) -> dict:
    recent = []
    for row in rows:
        day = clock.ms_to_day(row.get("date"))
        if day and as_of - timedelta(days=60) <= day <= as_of:
            recent.append((day, row))
    n = len(recent)
    if n == 0:
        return {"n": 0}
    per_day = Counter(day for day, _ in recent)
    top = per_day.most_common(3)
    return {
        "n": n,
        "burst_share": round(sum(c for _, c in top) / n, 3),
        "burst_days": [d.isoformat() for d, _ in top],
        "unverified_share": round(sum(1 for _, r in recent if not _truthy(r.get("verified"))) / n, 3),
        "vine_share": round(sum(1 for _, r in recent if _truthy(r.get("vine"))) / n, 3),
        "five_star_share": round(sum(1 for _, r in recent if str(r.get("star")).strip() == "5") / n, 3),
    }


def _fresh(check: dict | None, today: date) -> bool:
    day = clock.parse_day((check or {}).get("checked"))
    return bool(day and (today - day).days < RECHECK_DAYS)


def select(items: list[dict], state: dict, cfg: dict, today: date) -> list[tuple[str, str]]:
    """返回 [(asin, 'review'|'detail')]，按优先级排序，长度不超过 review_checks。"""
    fcfg = cfg["thresholds"]["fake"]
    limit = int(cfg["budget"]["review_checks"])
    jobs: list[tuple[float, str, str]] = []
    for item in items:
        if item["label"] == "low":
            continue  # 评分不达标的不会上榜，不必花调用核查
        rec = state["asins"].get(item["asin"]) or {}
        checks = rec.get("checks") or {}
        as_of = clock.parse_day(item.get("as_of"))
        jump = ratings_jump(rec, as_of) if as_of else None
        if jump is not None and jump >= fcfg["merge_jump"] and not _fresh(checks.get("detail"), today):
            jobs.append((100 + jump, item["asin"], "detail"))
        interesting = item["label"] in ("surge", "potential", "fake") or item.get("hot")
        if item["fake"]["score"] >= fcfg["suspect_score"] - 1 and interesting \
                and not _fresh(checks.get("review"), today):
            jobs.append((item["fake"]["score"] + (item.get("surge") or {}).get("strength", 0) / 100,
                         item["asin"], "review"))
    jobs.sort(key=lambda j: -j[0])
    return [(asin, kind) for _, asin, kind in jobs[:limit]]


def run_checks(vendor: Vendor, jobs: list[tuple[str, str]], state: dict, cfg: dict, today: date) -> int:
    done = 0
    for asin, kind in jobs:
        rec = state["asins"].setdefault(asin, {"asin": asin})
        checks = rec.setdefault("checks", {})
        try:
            if kind == "review":
                as_of = clock.parse_day(((rec.get("obs") or [{}])[-1]).get("date")) or today
                start = datetime.combine(as_of - timedelta(days=60), time.min, tzinfo=timezone.utc)
                reply = vendor.call("review", vendor.args(
                    "review", nested=False, marketplace=cfg["marketplace"], asin=asin, size=50, page=1,
                    startTimestamp=int(start.timestamp() * 1000)), purpose="评论核查")
                if reply.ok:
                    checks["review"] = {**summarize_reviews(reply.rows, as_of), "checked": today.isoformat()}
                    done += 1
            else:
                reply = vendor.call("asin_detail", vendor.args("asin_detail", nested=False,
                                                               marketplace=cfg["marketplace"], asin=asin),
                                    purpose="详情核查")
                if reply.ok and isinstance(reply.data, dict):
                    data = reply.data
                    if not rec.get("amazon_material"):
                        materials.remember(rec, data, today)
                    checks["detail"] = {
                        "parent": data.get("parent"),
                        "variations": data.get("variations"),
                        "ratings": data.get("ratings"),
                        "checked": today.isoformat(),
                    }
                    done += 1
        except BudgetExhausted:
            log.warn("核查阶段预算用完")
            break
    return done

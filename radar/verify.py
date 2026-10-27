"""对可疑商品和报告推荐的爆火、潜力做额外核查（每期最多 review_checks 个，每个 1 次调用）：

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


REVIEW_WINDOW = 60
REVIEW_PAGE = 20  # 实测 review 接口一页最多 20 条（传 size=50 也是 20），新的在前


def summarize_reviews(rows: list[dict], as_of: date, total: int | None = None) -> dict:
    """近 60 天评论的概况。total：接口说这段时间一共有几条（一页只给最新的 20 条）。
    没拿全时这一页只覆盖最近一段日子：span = 这一页最早一条到 as_of 的天数，“集中在 3 天”按 span 天比，不按 60 天。"""
    recent = []
    for row in rows:
        day = clock.ms_to_day(row.get("date"))
        if day and as_of - timedelta(days=REVIEW_WINDOW) <= day <= as_of:
            recent.append((day, row))
    n = len(recent)
    if n == 0:
        return {"n": 0}
    per_day = Counter(day for day, _ in recent)
    top = per_day.most_common(3)
    truncated = total > len(rows) if total is not None else len(rows) >= REVIEW_PAGE
    span = max(7, (as_of - min(day for day, _ in recent)).days + 1) if truncated else REVIEW_WINDOW
    return {
        "n": n,
        "top3": sum(c for _, c in top),
        "span": span,
        "total": total,
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
            jobs.append((1000 + jump, item["asin"], "detail"))
        if _fresh(checks.get("review"), today):
            continue
        # 评论核查的先后（名额不够时先查前面的）：① 可疑的（异常分 ≥ 疑似线 − 1，且是爆火 / 潜力 / 异常 / 持续热销）；
        # ② 报告推荐的爆火、潜力（以前只查异常分 ≥2 的，10-23 那期 31 个爆火 / 潜力一个都没查过评论）；
        # ③ 不在榜单上、异常分 ≥ 疑似线 − 1 的（以前不查，只有一条信号的永远到不了疑似线：10-23 那期 7 个 2 分的）；
        # ④ 按“可能是 Vine”扣过才不够评论信号的新品（fakehot 的 check_reviews：查了按抽到的 Vine 比例重算）
        interesting = item["label"] in ("surge", "potential", "fake") or item.get("hot")
        strength = (item.get("surge") or {}).get("strength", 0) / 100
        suspect = item["fake"]["score"] >= fcfg["suspect_score"] - 1
        if suspect and interesting:
            jobs.append((300 + item["fake"]["score"] + strength, item["asin"], "review"))
        elif item["label"] in ("surge", "potential"):
            jobs.append((200 + item["fake"]["score"] + strength, item["asin"], "review"))
        elif suspect:
            jobs.append((150 + item["fake"]["score"], item["asin"], "review"))
        elif item["fake"].get("check_reviews"):
            jobs.append((100 + item["fake"]["score"], item["asin"], "review"))
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
                    total = reply.data.get("total") if isinstance(reply.data, dict) else None
                    checks["review"] = {**summarize_reviews(reply.rows, as_of, total), "checked": today.isoformat()}
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

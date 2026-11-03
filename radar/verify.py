"""对可疑商品、报告点名的商品和其余爆火、潜力做额外核查（每期最多 review_checks 个，每个 1 次调用，先后见 select）：

* 评论核查（review）：近 60 天评论是否集中在少数几天、非验证购买占比、Vine 占比。
* 详情核查（asin_detail）：评论数暴增时看父体/变体是否变化，区分“变体合并”和“刷评”。
14 天内核查过的不再重复调用。
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta, timezone

from . import analyze, clock, evidence, log, materials, narrative
from .detect import directions, showcase
from .detect.fakehot import ratings_jump
from .vendor import BudgetExhausted, Vendor

RECHECK_DAYS = evidence.RECHECK_DAYS


def _truthy(value) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "y"):
        return True
    if text in ("false", "0", "no", "n"):
        return False
    return None


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
    verified = [value for _, r in recent if (value := _truthy(r.get("verified"))) is not None]
    vine = [value for _, r in recent if (value := _truthy(r.get("vine"))) is not None]
    return {
        "n": n,
        "top3": sum(c for _, c in top),
        "span": span,
        "total": total,
        "burst_share": round(sum(c for _, c in top) / n, 3),
        "burst_days": [d.isoformat() for d, _ in top],
        "verified_n": len(verified),
        "vine_n": len(vine),
        "unverified_share": round(sum(v is False for v in verified) / len(verified), 3) if len(verified) >= 10 else None,
        "vine_share": round(sum(vine) / n, 3) if len(vine) == n else None,
        "five_star_share": round(sum(1 for _, r in recent if str(r.get("star")).strip() == "5") / n, 3),
    }


def _fresh(check: dict | None, today: date) -> bool:
    return evidence.fresh(check, today)


def named(items: list[dict], cfg: dict, spare: int = 2, state: dict | None = None) -> set[str]:
    """报告里点名、团队会照着去看的商品：本期要点里的突然爆火、潜力（各前 narrative.FACT_ITEMS 个，钉钉列的前几个也在里面），
    和增量最大的前 directions.summary_max 个开发方向（多看 spare 个：有的方向画不出画像会被跳过）的参考商品候选
    （势头最强、没有异常信号、不同品牌的前 3 个：抽查后有一个变成异常时，第 3 个顶上当参考）。
    给了 state 时和报告一样只算列出的（去掉简单款、合并相近款，detect.showcase；用已有的主图识别）。"""
    sec = analyze.sections([i for i in items if i["label"] in ("surge", "potential")], 0)
    products = list((((state or {}).get("discovery") or {}).get("products") or {}).values())
    common = showcase.common_features(items + products, state, cfg) if state is not None else None

    def listed(rows: list[dict]) -> list[dict]:
        return rows if state is None else showcase.curate(rows, state, cfg, common)["shown"]

    out = {i["asin"] for key in ("surge", "potential") for i in listed(sec[key])[:narrative.FACT_ITEMS]}
    top = narrative.summary_limit(cfg) + spare
    for _node, rows in directions.ranked(analyze.focus_items(items), cfg)[:top]:
        if state is not None:
            rows = [dict(i, plain=showcase.is_plain(i, state, common)) for i in rows]
        out |= {i["asin"] for i in directions.ref_candidates(rows, 3)}
    return out


def select(items: list[dict], state: dict, cfg: dict, today: date) -> list[tuple[str, str]]:
    """返回 [(asin, 'review'|'detail')]，按优先级排序，长度不超过 review_checks；
    其中至少 review_checks_unlisted 个留给 ③（不在榜单上、已有一条异常信号的），这类不够时名额还给其他。"""
    fcfg = cfg["thresholds"]["fake"]
    limit = max(0, int(cfg["budget"]["review_checks"]))
    reserve = min(limit, max(0, int(cfg["budget"].get("review_checks_unlisted") or 0)))
    jobs: list[tuple[float, float, str, str, bool]] = []  # (先后, 同分时按月销售额, asin, 核查种类, 是不是 ③)
    shown = named(items, cfg, state=state)
    for item in items:
        if item["label"] == "low":
            continue  # 评分不达标的不会上榜，不必花调用核查
        rec = state["asins"].get(item["asin"]) or {}
        checks = rec.get("checks") or {}
        as_of = clock.parse_day(item.get("as_of"))
        sales = analyze.monthly_sales(item)
        jump = ratings_jump(rec, as_of) if as_of else None
        if jump is not None and jump >= fcfg["merge_jump"] and not _fresh(checks.get("detail"), today):
            jobs.append((1000 + jump, sales, item["asin"], "detail", False))
        if evidence.usable_check(rec, "review", today):
            continue
        # 评论核查的先后（名额不够时先查前面的）：① 可疑的（异常分 ≥ 疑似线 − 1，且是爆火 / 潜力 / 异常 / 持续热销）；
        # ② 报告点名的（named：本期要点、钉钉里的爆火 / 潜力，前几个开发方向的参考商品候选）——团队照着去看的先查；
        # ③ 不在榜单上、异常分 ≥ 疑似线 − 1 的（只有一条信号的，查了才可能有第二条：10-23 那期 7 个 2 分的）；
        # ④ 其余爆火、潜力（以前只查异常分 ≥2 的，10-23 那期 31 个爆火 / 潜力一个都没查过评论）；
        # ⑤ 按“可能是 Vine”扣过才不够评论信号的新品（fakehot 的 check_reviews：查了按抽到的 Vine 比例重算）。
        # 以前 ② 和 ④ 不分、排在 ③ 前面：11-01 彩排（全市场名单）爆火 + 潜力 88 个把 40 个名额占满，③ 一个都查不到，
        # 开发方向的 42 个参考商品只有 21 个排进前 40。
        # ③ 在总名额内留出补查机会，避免只查上榜商品和详情，单条信号的观察商品长期得不到第二条证据。
        interesting = item["label"] in ("surge", "potential", "fake") or item.get("hot")
        strength = (item.get("surge") or {}).get("strength", 0) / 100
        suspect = item["fake"]["score"] >= fcfg["suspect_score"] - 1
        score = item["fake"]["score"]
        if suspect and interesting:
            jobs.append((400 + score + strength, sales, item["asin"], "review", False))
        elif item["asin"] in shown:
            jobs.append((300 + score + strength, sales, item["asin"], "review", False))
        elif suspect:
            jobs.append((200 + score, sales, item["asin"], "review", True))
        elif item["label"] in ("surge", "potential"):
            jobs.append((150 + score + strength, sales, item["asin"], "review", False))
        elif item["fake"].get("check_reviews"):
            jobs.append((100 + score, sales, item["asin"], "review", False))
    jobs.sort(key=lambda j: (-j[0], -j[1]))  # 同分时先查月销售额大的
    kept = [j for j in jobs if j[4]][:reserve]
    kept += [j for j in jobs if j not in kept][:limit - len(kept)]
    kept.sort(key=lambda j: (-j[0], -j[1]))
    return [(asin, kind) for _, _, asin, kind, _ in kept]


def run_checks(vendor: Vendor, jobs: list[tuple[str, str]], state: dict, cfg: dict, today: date, *,
               as_ofs: dict[str, date] | None = None) -> int:
    done = 0
    for asin, kind in jobs:
        rec = state["asins"].setdefault(asin, {"asin": asin})
        checks = rec.setdefault("checks", {})
        try:
            if kind == "review":
                as_of = (as_ofs or {}).get(asin) or today
                start = datetime.combine(as_of - timedelta(days=60), time.min, tzinfo=timezone.utc)
                end = datetime.combine(as_of, time.max, tzinfo=timezone.utc)
                reply = vendor.call("review", vendor.args(
                    "review", nested=False, marketplace=cfg["marketplace"], asin=asin, size=50, page=1,
                    startTimestamp=int(start.timestamp() * 1000), endTimestamp=int(end.timestamp() * 1000)), purpose="评论核查")
                if reply.ok:
                    total = reply.data.get("total") if isinstance(reply.data, dict) else None
                    summary = summarize_reviews(reply.rows, as_of, total)
                    if not summary["n"] and (reply.rows or total != 0):
                        continue  # 不能把窗口外返回值被全部过滤，当作完成了有效抽查。
                    checks["review"] = {**summary, "total": total, "as_of": as_of.isoformat(),
                                        "checked": today.isoformat(), **evidence.current_family(rec, today)}
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
                        **{"before_" + key: (evidence.latest_observation(rec, today - timedelta(days=1)).get(key)
                                             or rec.get(key)) for key in ("parent", "variations")},
                        "parent": data.get("parent"),
                        "variations": data.get("variations"),
                        "ratings": data.get("ratings"),
                        "checked": today.isoformat(),
                        "as_of": today.isoformat(),
                    }
                    done += 1
        except BudgetExhausted:
            log.warn("核查阶段预算用完")
            break
    return done

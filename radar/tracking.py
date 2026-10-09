"""追踪池与日数据刷新。

卖家精灵的数据有两种新鲜度：月度榜单（已结束月份的月末快照）和近 30 天榜单 / 单个商品日数据（截至当天）。
“新”只能靠日数据，每个 ASIN 1 次调用；所以每期约 150 个名额的分配决定了能发现多少机会。

追踪池（本地计算，不花调用），按顺序填满 max_size：
  1. 必看（≤ core_max）：往期被标为 爆火/潜力/异常 的 + 每个子类目月销量前 per_node_top（轮流取），
     名额还有剩就按月销量从全部子类目里补（持续热销板块按月销量排，床架这类大类目的头部不能漏）
  2. 相似款（≤ similar_max）：上期爆款的同类竞品（近 30 天数据）
  3. 机会候选（剩余名额）：近 30 天上升榜 / 新品榜 / 相似款，以及月度名单里增长快或上架半年内的商品，
     按机会分排序；分数最高的 opportunity_every_run 个每期都看，其余按“最久没查”轮流查。
同一父体只取一个；评分低于 min_rating 的不占名额，各类都由排在后面的商品依次补位。

刷新（每个 ASIN 1 次 asin_prediction 调用，返回约 400 天日数据）：
  A 档：新进池、上期被标记的 → 每期刷新
  B 档：其余稳定商品 → 每隔 stable_refresh_every 期刷新一次，轮换进行
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone

from . import clock, log
from .detect.rules import current_rating, low_rating
from .discovery import clean_text, keep_product
from .series import Daily, build_daily
from .vendor import Vendor

FLAG_LABELS = ("surge", "potential", "fake")
FRESH_SOURCES = ("riser", "fresh_new", "similar")  # 近 30 天数据（截至当天）
REASON_CN = {"watch": "往期上榜", "top": "子类目头部", "similar": "爆款相似款",
             "opportunity": "机会（每期）", "rotate": "机会（轮查）"}


def watchlist(state: dict, runs: int) -> list[str]:
    out: list[str] = []
    for run in (state.get("runs") or [])[-runs:][::-1]:
        for asin, (label, *_rest) in (run.get("labels") or {}).items():
            if label in FLAG_LABELS and asin not in out:
                out.append(asin)
    return out


def _pct_rank(values: list[float | None]) -> list[float]:
    present = sorted(v for v in values if v is not None)
    if not present:
        return [0.5] * len(values)
    out = []
    for v in values:
        if v is None:
            out.append(0.5)
            continue
        below = sum(1 for x in present if x < v)
        out.append(below / max(1, len(present) - 1) if len(present) > 1 else 0.5)
    return out


def opportunity_rank(cands: list[dict], today) -> list[dict]:
    """机会分 = 销量增长率 35% + 近 7 天 BSR 改善率 20% + 子类目内销量位次 20% + 上架半年内 15% + 近 30 天数据 10%。
    units_gr 在近 30 天榜单里是“近 30 天 vs 之前 30 天”，在月度名单里是月环比；bsr_cr 为正 = 排名上升。"""
    if not cands:
        return []
    growth = _pct_rank([None if p.get("units_gr") is None else min(p["units_gr"], 500) for p in cands])
    bsr = _pct_rank([p.get("bsr_cr") for p in cands])
    by_node: dict[str, list[dict]] = {}
    for p in cands:
        by_node.setdefault(p["node"], []).append(p)
    unit_pct: dict[str, float] = {}
    for items in by_node.values():
        for p, r in zip(items, _pct_rank([p["units"] for p in items])):
            unit_pct[p["asin"]] = r
    scored = []
    for p, g, b in zip(cands, growth, bsr):
        age = clock.days_between(clock.parse_day(p.get("available")), today)
        young = 1.0 if age is not None and age <= 180 else 0.0
        fresh = 1.0 if p["sources"][0] in FRESH_SOURCES else 0.0
        scored.append((0.35 * g + 0.2 * b + 0.2 * unit_pct.get(p["asin"], 0.5) + 0.15 * young + 0.1 * fresh, p))
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored]


def fresh_rows(state: dict) -> dict[str, dict]:
    """本期的近 30 天行：上升榜 / 新品榜优先，其次相似款。"""
    rows = {p["asin"]: p for p in (state.get("similar") or {}).get("items") or []}
    rows.update({p["asin"]: p for p in (state.get("risers") or {}).get("items") or []})
    return rows


def opportunity_candidates(disc: dict | None, state: dict, cfg: dict, today) -> list[dict]:
    """机会候选（按机会分排序）：近 30 天榜单和相似款全部纳入；月度名单里只取增长快或上架半年内的。
    同一 ASIN 用最新的一行；评分不达标、销量太小、标题不在范围内的剔除；同一父体只留销量最大的。"""
    pcfg = cfg["pool"]
    floor = cfg["thresholds"].get("min_rating")
    min_units = float(pcfg.get("opportunity_min_units", 0))
    min_growth = float(pcfg.get("opportunity_min_growth", 0))
    max_age = int(pcfg.get("opportunity_max_age_days", 180))
    nodes = {n["path"] for n in (disc or {}).get("nodes") or []}
    rows = {**((disc or {}).get("products") or {}), **fresh_rows(state)}
    best: dict[str, dict] = {}
    for p in rows.values():
        if not p.get("node") or (nodes and p["node"] not in nodes) or not keep_product(p, cfg):
            continue
        if (p.get("units") or 0) < min_units:
            continue
        if low_rating(current_rating(state["asins"].get(p["asin"]), p), floor):
            continue
        if p["sources"][0] not in FRESH_SOURCES:
            age = clock.days_between(clock.parse_day(p.get("available")), today)
            if (p.get("units_gr") or 0) < min_growth and not (age is not None and age <= max_age):
                continue
        key = p.get("parent") or p["asin"]
        if key not in best or (p["units"] or 0) > (best[key]["units"] or 0):
            best[key] = p
    return opportunity_rank(list(best.values()), today)


def select_pool(disc: dict | None, state: dict, cfg: dict, today,
                limit: int | None = None) -> tuple[list[str], dict[str, str]]:
    """返回 (ASIN 列表, 入选原因)。limit：本期还能刷新多少个（预算），池子不会超过它。"""
    pcfg = cfg["pool"]
    size = int(pcfg["max_size"]) if limit is None else max(0, min(int(pcfg["max_size"]), limit))
    core_max = min(size, int(pcfg.get("core_max", size)))
    products = list(((disc or {}).get("products") or {}).values())
    fresh = fresh_rows(state)
    by_asin = {**fresh, **{p["asin"]: p for p in products}}
    floor = cfg["thresholds"].get("min_rating")

    order: list[str] = []
    reason: dict[str, str] = {}
    parents: set[str] = set()

    def add(asin: str, why: str, cap: int) -> bool:
        rec = state["asins"].get(asin) or {}
        row = by_asin.get(asin)
        key = (row or {}).get("parent") or rec.get("parent") or asin
        if len(order) >= cap or asin in reason or key in parents:
            return False
        if low_rating(current_rating(rec, row), floor):
            return False
        reason[asin] = why
        order.append(asin)
        parents.add(key)
        return True

    # 1. 必看：往期上榜的 + 每个子类目月销量前几（轮流取，截断时每个子类目都有覆盖）+ 全部类目里销量最大的
    nodes = {n["path"] for n in (disc or {}).get("nodes") or []}
    for asin in watchlist(state, pcfg["watch_runs"]):
        rec = state["asins"].get(asin) or {}
        if nodes and rec.get("node") not in nodes:
            continue  # 范围调整后，旧范围里的商品（例如沙发、床垫）不再追踪
        if keep_product(rec, cfg):
            add(asin, "watch", core_max)
    by_node: dict[str, list[dict]] = {}
    for p in products:
        if "top" in p["sources"]:
            by_node.setdefault(p["node"], []).append(p)
    for items in by_node.values():
        items.sort(key=lambda p: -(p["units"] or 0))
    taken = {node: 0 for node in by_node}
    for _ in range(int(pcfg["per_node_top"])):
        for node, items in by_node.items():
            # 评分不达标 / 同父体的跳过，由本子类目下一名补位
            for i in range(taken[node], len(items)):
                taken[node] = i + 1
                if add(items[i]["asin"], "top", core_max):
                    break
    for p in sorted((p for items in by_node.values() for p in items), key=lambda p: -(p["units"] or 0)):
        if len(order) >= core_max:
            break
        add(p["asin"], "top", core_max)

    # 2. 相似款：上期爆款的同类竞品，最新的优先
    similar = sorted((state.get("similar") or {}).get("items") or [], key=lambda p: (-p.get("run", 0), p["rank"]))
    cap = min(size, len(order) + int(pcfg.get("similar_max", 0)))
    for p in similar:
        if (p.get("units") or 0) >= float(pcfg.get("opportunity_min_units", 0)) and keep_product(p, cfg):
            add(p["asin"], "similar", cap)

    # 3. 机会候选：分数最高的每期都看，其余按“最久没查”轮流
    cands = opportunity_candidates(disc, state, cfg, today)
    every = int(pcfg.get("opportunity_every_run", 0))
    for p in cands[:every]:
        add(p["asin"], "opportunity", size)
    rest = [(int((state["asins"].get(p["asin"]) or {}).get("last_pool_run", -10_000)), i, p)
            for i, p in enumerate(cands[every:])]
    for _, _, p in sorted(rest, key=lambda t: (t[0], t[1])):
        if len(order) >= size:
            break
        add(p["asin"], "rotate", size)
    return order, {a: reason[a] for a in order}


def plan_fetch(pool: list[str], state: dict, run_seq: int, cfg: dict, limit: int) -> list[str]:
    every = max(1, int(cfg["budget"]["stable_refresh_every"]))
    last_labels = (state.get("runs") or [{}])[-1].get("labels", {}) if state.get("runs") else {}
    flagged, fresh, stale = [], [], []
    for asin in pool:
        rec = state["asins"].get(asin)
        label = (last_labels.get(asin) or [None])[0]
        if rec and rec.get("series") and label in FLAG_LABELS:
            flagged.append(asin)
        elif not rec or not rec.get("series"):
            fresh.append(asin)
        else:
            fetched = int(rec.get("fetched_run", -10_000))
            # 轮换：每期大约刷新 1/every 的稳定商品，而不是每隔 every 期集中刷新一次
            if run_seq - fetched >= every or (fetched < run_seq and (zlib.crc32(asin.encode()) + run_seq) % every == 0):
                stale.append((fetched, asin))
    stale.sort()
    return (flagged + fresh + [a for _, a in stale])[: max(0, limit)]


def _meta_from_discovery(rec: dict, row: dict | None) -> None:
    if not row:
        return
    for key in ("title", "brand", "image", "node", "node_name", "node_cn", "parent", "variations",
                "fulfillment", "seller_nation", "available", "price"):
        if row.get(key) not in (None, ""):
            rec[key] = row[key]


def fetch_one(vendor: Vendor, asin: str, state: dict, cfg: dict, run_seq: int,
              disc_row: dict | None) -> bool:
    reply = vendor.call("asin_prediction",
                        vendor.args("asin_prediction", nested=False, marketplace=cfg["marketplace"], asin=asin),
                        purpose="日数据", prune=False)
    rec = state["asins"].setdefault(asin, {"asin": asin, "first_seen_run": run_seq, "obs": []})
    _meta_from_discovery(rec, disc_row)
    if not reply.ok or not isinstance(reply.data, dict):
        rec["fetch_error"] = reply.detail or reply.status
        return False

    data = reply.data
    detail = data.get("asinDetail") or {}
    series = build_daily(data.get("dailyItemList") or [])
    months = []
    for m in data.get("monthItemList") or []:
        key = str(m.get("date") or "")[:7]
        if len(key) == 7:
            months.append([key, m.get("sales"), m.get("amount"), m.get("price")])
    months.sort()

    if detail.get("title") and not rec.get("title"):
        rec["title"] = clean_text(detail["title"])
    if detail.get("brand") and not rec.get("brand"):
        rec["brand"] = clean_text(detail["brand"])
    if detail.get("imageUrl"):
        rec["image"] = detail["imageUrl"]
    available = clock.ms_to_day(detail.get("availableDate"))
    if available and not rec.get("available"):
        rec["available"] = available.isoformat()
    if detail.get("nodeLabelPath") and not rec.get("node_name"):
        rec["node_name"] = str(detail["nodeLabelPath"]).split(":")[-1]

    if series:
        rec["series"] = series
    if months:
        rec["months"] = months
    rec.pop("fetch_error", None)
    rec["fetched_run"] = run_seq
    rec["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    daily = Daily.from_record(series)
    as_of = daily.as_of() if daily else None
    as_of_text = as_of.isoformat() if as_of else None
    ratings, rating = detail.get("ratings"), detail.get("rating")
    if ratings is not None or rating is not None:
        obs = rec.setdefault("obs", [])
        entry = {"date": as_of_text, "run": run_seq, "ratings": ratings, "rating": rating}
        if not obs or obs[-1].get("date") != as_of_text or obs[-1].get("ratings") != ratings:
            obs.append(entry)
        del obs[:-24]
    return True


def refresh(vendor: Vendor, plan: list[str], state: dict, disc: dict | None, cfg: dict,
            run_seq: int) -> int:
    """按计划刷新；预算用完时由调用方捕获 BudgetExhausted。返回成功刷新的个数。"""
    products = {**fresh_rows(state), **((disc or {}).get("products") or {})}
    done = 0
    for i, asin in enumerate(plan, start=1):
        if fetch_one(vendor, asin, state, cfg, run_seq, products.get(asin)):
            done += 1
        if i % 20 == 0:
            log.info(f"追踪：已刷新 {i}/{len(plan)}")
    return done

"""追踪池与日数据刷新。

追踪池（本地计算，不花调用）：
  1. 往期被标为 爆火/潜力/异常/热销 的 ASIN（watchlist）
  2. 上升势头最强的（BSR 改善率、上架时间、子类目内销量位次）
  3. 每个子类目轮流取销量前几（同一父体只取一个，避免五个颜色挤满报告）
  4. BSR 上升榜（按近 7 天 BSR 增长率排序）里的前几名
  5. 探索位：从基线里轮换抽查（月度榜单发现不了本月才起量的商品）

刷新（每个 ASIN 1 次 asin_prediction 调用，返回约 400 天日数据）：
  A 档：新进池、上期被标记的 → 每期刷新
  B 档：其余稳定商品 → 每隔 stable_refresh_every 期刷新一次，轮换进行
"""
from __future__ import annotations

import zlib
from datetime import datetime, timezone

from . import clock, log
from .discovery import clean_text
from .series import Daily, build_daily
from .vendor import Vendor

FLAG_LABELS = ("surge", "potential", "fake")
WATCH_LABELS = ("surge", "potential", "fake", "hot")


def watchlist(state: dict, runs: int) -> list[str]:
    out: list[str] = []
    for run in (state.get("runs") or [])[-runs:][::-1]:
        for asin, (label, *_rest) in (run.get("labels") or {}).items():
            if label in WATCH_LABELS and asin not in out:
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


def momentum_rank(reps: list[dict], today) -> list[dict]:
    """bsrCr：BSR 改善率（%），正数=排名上升（例如 bsr 45557→32275，bsrCv=13282，bsrCr=29.15）。"""
    if not reps:
        return []
    bsr_pct = _pct_rank([p.get("bsr_cr") for p in reps])
    by_node: dict[str, list[dict]] = {}
    for p in reps:
        by_node.setdefault(p["node"], []).append(p)
    unit_pct: dict[str, float] = {}
    for items in by_node.values():
        ranks = _pct_rank([p["units"] for p in items])
        for p, r in zip(items, ranks):
            unit_pct[p["asin"]] = r
    scored = []
    for p, b in zip(reps, bsr_pct):
        age = clock.days_between(clock.parse_day(p.get("available")), today)
        young = 1.0 if age is not None and age <= 180 else 0.0
        scored.append((0.5 * b + 0.3 * unit_pct.get(p["asin"], 0.5) + 0.2 * young, p))
    scored.sort(key=lambda t: -t[0])
    return [p for _, p in scored]


def select_pool(disc: dict | None, state: dict, cfg: dict, today) -> tuple[list[str], dict[str, str]]:
    pcfg = cfg["pool"]
    products = list(((disc or {}).get("products") or {}).values())

    best: dict[str, dict] = {}
    for p in products:
        key = p.get("parent") or p["asin"]
        if key not in best or (p["units"] or 0) > (best[key]["units"] or 0):
            best[key] = p
    reps = list(best.values())

    order: list[str] = []
    reason: dict[str, str] = {}

    def add(asin: str, why: str) -> None:
        if asin not in reason:
            reason[asin] = why
            order.append(asin)

    for asin in watchlist(state, pcfg["watch_runs"]):
        add(asin, "watch")
    for p in momentum_rank(reps, today)[: pcfg["momentum_top"]]:
        add(p["asin"], "momentum")

    by_node: dict[str, list[dict]] = {}
    for p in reps:
        by_node.setdefault(p["node"], []).append(p)
    for items in by_node.values():
        items.sort(key=lambda p: -(p["units"] or 0))
    for rank in range(pcfg["per_node_top"]):  # 轮流取，截断时每个子类目都有覆盖
        for items in by_node.values():
            if rank < len(items):
                add(items[rank]["asin"], "top")

    order = order[: pcfg["max_size"]]

    # BSR 上升榜（头部 50 名以外刚起量的商品），同一父体只取一个
    by_asin = {p["asin"]: p for p in products}
    chosen_parents = {(by_asin.get(a) or {}).get("parent") or a for a in order}
    risers = sorted((state.get("risers") or {}).get("items") or [], key=lambda p: p.get("rank") or 999)
    added = 0
    for p in risers:
        if added >= int(pcfg.get("risers_top", 0)):
            break
        key = p.get("parent") or p["asin"]
        if p["asin"] in reason or key in chosen_parents:
            continue
        reason[p["asin"]] = "riser"
        order.append(p["asin"])
        chosen_parents.add(key)
        added += 1

    # 探索位：月度榜单看不到“本月才开始爆”的商品，所以每期再从基线里轮换抽查一批，
    # 按势头排序、跳过最近抽查过的；查出爆火/潜力/异常的会进入 watchlist 持续追踪。
    explore = int(pcfg.get("explore_per_run", 0))
    recent = int(pcfg.get("explore_cooldown_runs", 6))
    run_seq = int(state.get("run_seq") or 0) + 1
    picked = 0
    for p in momentum_rank(reps, today):
        if picked >= explore:
            break
        if p["asin"] in reason:
            continue
        rec = state["asins"].get(p["asin"]) or {}
        if run_seq - int(rec.get("explored_run", -10_000)) < recent:
            continue
        reason[p["asin"]] = "explore"
        order.append(p["asin"])
        picked += 1
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
    products = {**{p["asin"]: p for p in (state.get("risers") or {}).get("items") or []},
                **((disc or {}).get("products") or {})}
    done = 0
    for i, asin in enumerate(plan, start=1):
        if fetch_one(vendor, asin, state, cfg, run_seq, products.get(asin)):
            done += 1
        if i % 20 == 0:
            log.info(f"追踪：已刷新 {i}/{len(plan)}")
    return done

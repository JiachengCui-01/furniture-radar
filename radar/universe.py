"""全市场名单：机会候选（拉日数据去判突然爆火 / 潜力 / 上升中的商品）从这里挑。

以前机会候选只来自月度名单（每个子类目销售额前 50 + 12 个子类目各 30 个新品）、根类目的近 30 天上升榜和相似款，
2026-10-23 的全覆盖试跑（7,536 个商品拉了日数据）里，近 30 天 ≥300 件或 ≥4 万美元的增长商品约有 340 个，报告里只有 54 个。

现在每期把全部子类目里近 30 天销量 ≥ opportunity_min_units 件或销售额 ≥ opportunity_min_revenue 美元的商品翻一遍
（product_research 不传月份，按父体一行；每 50 个商品 1 次调用，约 130 次），存下每个商品的近 30 天销量（快照）；
名单里的“增长率”和日数据对不上（试跑：414 个里 267 个差 20 个百分点以上），所以增长用自己存的往期快照算：
  g   = 本期近 30 天销量 ÷ 基准 − 1。基准：自己存的、约 30 天前（29~36 天）的那份；头一个月没有时，
        用上个月的月度名单（同样的门槛翻全，按天数折成 30 天）或别的 21~45 天前的快照（screen_ladder 按基准的新旧定门槛）；
  r6 / r12 = 近 6 / 12 天的日均 ÷ 6 / 12 天前那份的日均（掉出窗口的那几天按那份的日均估）。
只给可能达标的商品拉日数据（screen）：稳定以后 = g ≥ screen_growth、半年内上架且没跌、r6 或 r12 ≥ screen_short 之一；
头一个月再加上名单的近 7 天 BSR 改善 ≥ screen_bsr_cr%。新进名单（没有基准）的都拉。
回测（试跑的 2,039 个门槛以上商品、195 个增长商品，名单快照按“最近 9 天会被改成 0.82”模拟）：
稳定以后不漏、拉 34%；头一个月基准 21~36 天前 99~100%、拉 40~53%，基准 41~45 天前 99~100%、拉 59~66%。

state["universe"] = {
    "date": 本期日期, "pages": 本期翻了几页,
    "rows": {asin: 本期名单的一行（discovery.normalize_product）},
    "snaps": [{"date": "2026-10-26", "units": {asin: 近 30 天销量}}],   最近 snap_keep_days 天
    "baseline": {"period": "202609", "end": "2026-09-30", "units": {asin: 月销量}},   头一个月的替代基准
}
"""
from __future__ import annotations

from datetime import date, timedelta

from . import clock, log
from .discovery import keep_product, normalize_product
from .vendor import Vendor, request

STATE_KEY = "universe"
PAGE_SIZE = 50
QUERY_CAP = 1990  # 一个查询最多翻出约 2000 个不同的商品（40 页），再往后是重复的行
SNAP_KEEP_DAYS = 48
STEADY = (29, 36)        # “约 30 天前”的快照
FALLBACK = (21, 45)      # 头一个月能用的基准（再新的比不出 30 天的增长，再旧的漏得多）


def _uni(state: dict) -> dict:
    uni = state.setdefault(STATE_KEY, {})
    uni.setdefault("snaps", [])
    return uni


def _page_all(vendor: Vendor, cfg: dict, node: dict, order: str, limits: dict, period: str | None) -> list[dict]:
    rows, seen = [], set()
    for page in range(1, 60):
        reply = vendor.call("product_research", request(
            marketplace=cfg["marketplace"], nodeIdPath=node["path"], nodeIdPathEqual="false", month=period,
            size=PAGE_SIZE, page=page, variation="Y" if cfg["discovery"].get("merge_variations", True) else None,
            order={"field": order, "desc": True}, **limits), purpose="全市场名单")
        new = [r for r in reply.rows if r.get("asin") and r["asin"] not in seen]
        seen.update(r["asin"] for r in new)
        rows += new
        if len(reply.rows) < PAGE_SIZE or not new:
            break
    return rows


def fetch(vendor: Vendor, cfg: dict, nodes: list[dict], period: str | None = None) -> dict[str, dict]:
    """全部子类目里量够的商品：近 30 天（period 为空）或某个已结束月份。量够 = 销量 ≥ opportunity_min_units，
    或销售额 ≥ opportunity_min_revenue（接口的条件是“且”，所以分两次查：按销量查一次，再查销量不够、销售额够的）。
    同一父体只留一个；标题不在范围内的剔除。"""
    pcfg = cfg["pool"]
    min_units, min_revenue = int(pcfg["opportunity_min_units"]), pcfg.get("opportunity_min_revenue")
    out: dict[str, dict] = {}
    parents: set[str] = set()
    for node in nodes:
        raw = _page_all(vendor, cfg, node, "total_units", {"minUnits": min_units}, period)
        while len(raw) >= QUERY_CAP:  # 到了一个查询的上限：按销量往下接着查
            low = min(int(r.get("units") or 0) for r in raw)
            have = {r["asin"] for r in raw}
            more = [r for r in _page_all(vendor, cfg, node, "total_units", {"minUnits": min_units, "maxUnits": low}, period)
                    if r["asin"] not in have]
            raw += more
            if len(more) < QUERY_CAP - PAGE_SIZE:
                break
        if min_revenue:
            raw += _page_all(vendor, cfg, node, "total_amount",
                             {"minRevenue": min_revenue, "maxUnits": max(0, min_units - 1)}, period)
        for rank, row in enumerate(raw, start=1):
            item = normalize_product(row, node, "universe", rank)
            if not keep_product(item, cfg):
                continue
            key = item.get("parent") or item["asin"]
            if item["asin"] in out or key in parents:
                continue
            out[item["asin"]] = item
            parents.add(key)
    return out


def baseline_period(state: dict, today: date) -> str | None:
    """头一个月要不要拉上个月的月度名单当基准：还没有一份 FALLBACK[0] 天以上的快照、也没有能用的基准时，
    返回结束于 21~45 天前的那个月（YYYYMM，最近的那个）。"""
    uni = _uni(state)
    ages = [(today - clock.parse_day(s["date"])).days for s in uni["snaps"] if clock.parse_day(s["date"])]
    if any(age >= FALLBACK[0] for age in ages):
        return None
    base = uni.get("baseline") or {}
    end = clock.parse_day(base.get("end"))
    if end and FALLBACK[0] <= (today - end).days <= FALLBACK[1]:
        return None
    first = date(today.year, today.month, 1)
    for _ in range(3):
        end = first - timedelta(days=1)
        if FALLBACK[0] <= (today - end).days <= FALLBACK[1]:
            return f"{end.year}{end.month:02d}"
        first = date(end.year, end.month, 1)
    return None


def record_baseline(state: dict, period: str, rows: dict[str, dict]) -> None:
    y, m = int(period[:4]), int(period[4:])
    end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    _uni(state)["baseline"] = {"period": period, "end": end.isoformat(), "days": end.day,
                               "units": {a: r.get("units") or 0 for a, r in rows.items()}}


def record(state: dict, rows: dict[str, dict], today: date, pages: int) -> None:
    """存本期的名单和快照（同一天跑两次只留后一次），只留最近 SNAP_KEEP_DAYS 天的快照。"""
    uni = _uni(state)
    snaps = [s for s in uni["snaps"] if s["date"] != today.isoformat()
             and (today - clock.parse_day(s["date"])).days <= SNAP_KEEP_DAYS]
    snaps.append({"date": today.isoformat(), "units": {a: r.get("units") or 0 for a, r in rows.items()}})
    uni.update(date=today.isoformat(), pages=pages, rows=rows, snaps=snaps)


def _nearest(snaps: list[tuple[int, dict]], asin: str, lo: int, hi: int, target: int) -> tuple[int, float] | None:
    best = None
    for age, units in snaps:
        if lo <= age <= hi and units.get(asin):
            if best is None or abs(age - target) < abs(best[0] - target):
                best = (age, float(units[asin]))
    return best


def _ladder(cfg: dict, age: int) -> float:
    for upto, growth in cfg["screen_ladder"]:
        if age <= upto:
            return float(growth)
    return float(cfg["screen_ladder"][-1][1])


def features(state: dict, asin: str, row: dict, today: date) -> dict:
    """g（比基准的增长）、基准是什么（own = 自己存的快照 / month = 上个月的月度名单）、基准有几天、r6、r12。"""
    uni = _uni(state)
    snaps = [((today - clock.parse_day(s["date"])).days, s["units"]) for s in uni["snaps"]
             if s["date"] != today.isoformat() and clock.parse_day(s["date"])]
    u = float(row.get("units") or 0)
    out = {"u": u, "g": None, "base": None, "age": None, "r6": None, "r12": None}
    # 基准的先后：约 30 天前自己存的 → 24~45 天前自己存的 → 上个月的月度名单（21~45 天前结束）→ 21~23 天前自己存的
    kind = "own"
    pick = _nearest(snaps, asin, *STEADY, 30) or _nearest(snaps, asin, 24, FALLBACK[1], 30)
    if pick is None:
        base = uni.get("baseline") or {}
        end = clock.parse_day(base.get("end"))
        units = (base.get("units") or {}).get(asin)
        if end and units and FALLBACK[0] <= (today - end).days <= FALLBACK[1]:
            pick, kind = ((today - end).days, float(units) / int(base.get("days") or 30) * 30), "month"
        else:
            pick = _nearest(snaps, asin, FALLBACK[0], 23, 30)
    if pick:
        out.update(g=u / pick[1] - 1, base=kind, age=pick[0])
    for w in (6, 12):
        s = _nearest(snaps, asin, w - 1, w + 1, w)
        if s:
            out[f"r{w}"] = ((u - s[1] + s[1] / 30 * w) / w) / (s[1] / 30)
    return out


def screen(state: dict, rows: dict[str, dict], today: date, cfg: dict) -> list[tuple[str, float, str]]:
    """可能达标、要拉日数据的商品：[(asin, 优先级, 依据)]，优先级从高到低（预算不够时先拉前面的）。规则见模块说明。"""
    pcfg = cfg["pool"]
    growth, short = float(pcfg["screen_growth"]), float(pcfg["screen_short"])
    young_days, bsr_cr = int(pcfg["opportunity_max_age_days"]), float(pcfg["screen_bsr_cr"])
    out = []
    for asin, row in rows.items():
        f = features(state, asin, row, today)
        age = clock.days_between(clock.parse_day(row.get("available")), today)
        young = age is not None and age <= young_days
        shorts = [r for r in (f["r6"], f["r12"]) if r is not None]
        if f["g"] is None:
            out.append((asin, 10.0, "新进名单（没有基准）"))
            continue
        steady = f["base"] == "own" and STEADY[0] <= f["age"] <= STEADY[1]
        need = growth if steady else _ladder(pcfg, f["age"])
        why = []
        if f["g"] >= need - 1e-9:
            why.append(f"比 {f['age']} 天前 +{f['g']:.0%}")
        if young and f["g"] >= -1e-9:
            why.append("半年内上架、没跌")
        if any(r >= short - 1e-9 for r in shorts):
            why.append(f"近 6/12 天 ×{max(shorts):.1f}")
        if not steady and (row.get("bsr_cr") or 0) >= bsr_cr:
            why.append(f"近 7 天排名改善 {row['bsr_cr']:.0f}%")
        if why:
            out.append((asin, max([f["g"]] + [r - 1 for r in shorts]), "；".join(why)))
    out.sort(key=lambda t: -t[1])
    return out


def run(vendor: Vendor, cfg: dict, state: dict, nodes: list[dict], today: date) -> dict[str, dict]:
    """翻本期的全市场名单（头一个月还要翻一次上个月的月度名单当基准），存快照。返回本期名单。"""
    start = vendor.billable_calls
    period = baseline_period(state, today)
    if period:
        rows = fetch(vendor, cfg, nodes, period)
        record_baseline(state, period, rows)
        log.info(f"全市场名单：头一个月还没有 30 天前的快照，拉了 {period[:4]}-{period[4:]} 的月度名单当基准（{len(rows)} 个商品，"
                 f"{vendor.billable_calls - start} 次调用）")
    mid = vendor.billable_calls
    rows = fetch(vendor, cfg, nodes)
    record(state, rows, today, vendor.billable_calls - mid)
    log.info(f"全市场名单：近 30 天量够的商品 {len(rows)} 个（{vendor.billable_calls - mid} 次调用）")
    return rows

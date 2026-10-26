"""全市场名单：机会候选（拉日数据去判突然爆火 / 潜力 / 上升中的商品）从这里挑。

以前机会候选只来自月度名单（每个子类目销售额前 50 + 12 个子类目各 30 个新品）、根类目的近 30 天上升榜和相似款，
2026-10-23 的全覆盖试跑（7,536 个商品拉了日数据）里，近 30 天 ≥300 件或 ≥4 万美元的增长商品约有 340 个，报告里只有 54 个。

现在每期把全部子类目里近 30 天销量 ≥ opportunity_min_units 件或销售额 ≥ opportunity_min_revenue 美元的商品翻一遍
（product_research 不传月份，按父体一行；每 50 个商品 1 次调用，约 130 次），存下每个商品的近 30 天销量（快照）；
名单里的“增长率”和日数据对不上（试跑：414 个里 267 个差 20 个百分点以上），所以增长用自己存的往期快照算：
  g   = 本期近 30 天销量 ÷ 基准 − 1。基准：自己存的、约 pool.screen_base_days 天前（27~36 天）的那份；头一个月没有时，
        用上个月的月度名单（同样的门槛翻全，按天数折成 30 天）或别的 21~47 天前的快照（screen_ladder 按基准的新旧定门槛）；
  短期倍数 rN（N = pool.screen_short_days，每周一期是 7 / 14）= 近 a 天的日均 ÷ a 天前那份的日均，a 是那份快照实际隔了几天
        （N±2 天里最接近的一份；掉出窗口的那几天按那份的日均估）。
只给可能达标的商品拉日数据（screen）：稳定以后 = g ≥ screen_growth、半年内上架且没跌、任一短期倍数 ≥ screen_short 之一；
基准是上个月的月度名单时（头三周）再加上名单的近 7 天 BSR 改善 ≥ screen_bsr_cr%。新进名单（没有基准）的都拉。
回测（2026-10-23 全覆盖试跑：门槛以上 2,051 个商品、195 个增长商品，自己存的快照按“最近 9 天会被改成 0.82”模拟，按每周一期）：
稳定以后（28 天前的快照 + 7 / 14 天）不漏、拉 42%（和每 3 天一期的 30 天 + 6 / 12 天一样）；头一个月：
9 月名单 32 / 39 / 46 天前 + 门槛 15% / 10% / 10% + 排名改善，不漏、拉 52% / 57% / 59%，预算只够拉 900 个时按优先级拉，召回 98~99.5%；
自己 21 天前的快照 + 门槛 10%，不漏、拉 45%。

state["universe"] = {
    "date": 本期日期, "pages": 本期翻了几页,
    "rows": {asin: 本期名单的一行（discovery.normalize_product）},
    "snaps": [{"date": "2026-10-26", "units": {asin: 近 30 天销量}}],   最近 snap_keep_days 天
    "baseline": {"period": "202609", "end": "2026-09-30", "units": {asin: 月销量}},   头一个月的替代基准
}
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from . import clock, log
from .discovery import keep_product, normalize_product
from .vendor import Vendor, request

STATE_KEY = "universe"
PAGE_SIZE = 50
QUERY_CAP = 1990  # 一个查询最多翻出约 2000 个不同的商品（40 页），再往后是重复的行
SNAP_KEEP_DAYS = 50
STEADY = (27, 36)        # “约 30 天前”的快照（每周一期时是 28 或 35 天前那份）
FALLBACK = (21, 47)      # 头一个月能用的基准（再新的比不出 30 天的增长；每周一期时 9 月名单第三周已是 46 天前）


def _uni(state: dict) -> dict:
    uni = state.setdefault(STATE_KEY, {})
    uni.setdefault("snaps", [])
    return uni


def _page_all(vendor: Vendor, cfg: dict, node: dict, order: str, limits: dict, period: str | None,
              failed: list[str] | None = None) -> list[dict]:
    """一个查询翻到底。某一页查询失败（被拒 / 连不上）时重试一次，还不行就停在这里，子类目记进 failed：
    这一期这个子类目的名单不全（没翻到的商品这期不初筛，下期快照里没它们的基准就当新进名单拉日数据）。"""
    rows, seen = [], set()
    for page in range(1, 60):
        for attempt in range(2):
            reply = vendor.call("product_research", request(
                marketplace=cfg["marketplace"], nodeIdPath=node["path"], nodeIdPathEqual="false", month=period,
                size=PAGE_SIZE, page=page, variation="Y" if cfg["discovery"].get("merge_variations", True) else None,
                order={"field": order, "desc": True}, **limits), purpose="全市场名单")
            if reply.ok or reply.status == "empty":
                break
        if not reply.ok and reply.status != "empty":
            log.warn(f"全市场名单：{node.get('name') or node['path']} 第 {page} 页查询失败（{reply.status} {reply.detail[:80]}），这个子类目这期没翻全")
            if failed is not None and node["path"] not in failed:
                failed.append(node["path"])
            break
        new = [r for r in reply.rows if r.get("asin") and r["asin"] not in seen]
        seen.update(r["asin"] for r in new)
        rows += new
        if len(reply.rows) < PAGE_SIZE or not new:
            break
    return rows


def fetch(vendor: Vendor, cfg: dict, nodes: list[dict], period: str | None = None,
          failed: list[str] | None = None) -> dict[str, dict]:
    """全部子类目里量够的商品：近 30 天（period 为空）或某个已结束月份。量够 = 销量 ≥ opportunity_min_units，
    或销售额 ≥ opportunity_min_revenue（接口的条件是“且”，所以分两次查：按销量查一次，再查销量不够、销售额够的）。
    同一父体只留一个；标题不在范围内的剔除。failed：有页查询失败、没翻全的子类目（nodeIdPath）记进这里。"""
    pcfg = cfg["pool"]
    min_units, min_revenue = int(pcfg["opportunity_min_units"]), pcfg.get("opportunity_min_revenue")
    out: dict[str, dict] = {}
    parents: set[str] = set()
    for node in nodes:
        raw = _page_all(vendor, cfg, node, "total_units", {"minUnits": min_units}, period, failed)
        while len(raw) >= QUERY_CAP:  # 到了一个查询的上限：按销量往下接着查
            low = min(int(r.get("units") or 0) for r in raw)
            have = {r["asin"] for r in raw}
            more = [r for r in _page_all(vendor, cfg, node, "total_units", {"minUnits": min_units, "maxUnits": low}, period,
                                         failed)
                    if r["asin"] not in have]
            raw += more
            if len(more) < QUERY_CAP - PAGE_SIZE:
                break
        if min_revenue:
            raw += _page_all(vendor, cfg, node, "total_amount",
                             {"minRevenue": min_revenue, "maxUnits": max(0, min_units - 1)}, period, failed)
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


def fetch_young(vendor: Vendor, cfg: dict, nodes: list[dict], exclude: set[str], failed: list[str] | None = None) -> dict[str, dict]:
    """早期名单（pool.young）：全市场名单的门槛（近 30 天 ≥300 件或 ≥4 万美元）是 30 天合计，刚爆起来的新品要攒够才进得来——
    回测（2025-11~2026-09 每周回放）：约三分之一的突然爆火开始时还不够门槛，中位数晚 7 天（2~3 期）才进全市场名单，其中八成上架不到半年。
    所以每个子类目再翻一份：上架 ≤ max_age_months 个月、近 30 天 ≥ min_units 件或 ≥ min_revenue 美元、但不够全市场门槛的商品。
    不在全市场名单（exclude）里的才留；同一父体只留一个；标题不在范围内的剔除。"""
    ycfg, pcfg = cfg["pool"].get("young") or {}, cfg["pool"]
    floor_units = int(pcfg["opportunity_min_units"])
    out: dict[str, dict] = {}
    parents: set[str] = set()
    for node in nodes:
        common = {"availableMonth": int(ycfg.get("max_age_months", 6))}
        raw = _page_all(vendor, cfg, node, "total_units",
                        {**common, "minUnits": int(ycfg.get("min_units", 100)), "maxUnits": floor_units - 1}, None, failed)
        if ycfg.get("min_revenue"):
            raw += _page_all(vendor, cfg, node, "total_amount",
                             {**common, "minRevenue": ycfg["min_revenue"], "maxUnits": int(ycfg.get("min_units", 100)) - 1},
                             None, failed)
        for rank, row in enumerate(raw, start=1):
            item = normalize_product(row, node, "young", rank)
            if not keep_product(item, cfg) or item["asin"] in exclude:
                continue
            if (item.get("revenue") or 0) >= float(pcfg.get("opportunity_min_revenue") or float("inf")):
                continue  # 销售额已经够全市场门槛的在全市场名单里
            key = item.get("parent") or item["asin"]
            if item["asin"] in out or key in parents:
                continue
            out[item["asin"]] = item
            parents.add(key)
    return out


def screen_young(rows: dict[str, dict], cfg: dict) -> list[tuple[str, float, str]]:
    """早期名单里要拉日数据的：近 7 天 BSR 改善 ≥ pool.young.bsr_cr%（名单行自带，不花调用），改善多的在前。
    回测（同上，按日数据模拟近 7 天排名变化）：不够全市场门槛时就开始的增长里，这一条能提前约 6 天抓到 46 个突然爆火，
    早期名单里每期约 12% 的商品会被拉日数据。"""
    need = float((cfg["pool"].get("young") or {}).get("bsr_cr", 50))
    out = [(asin, float(row["bsr_cr"]), f"早期名单：上架半年内，近 7 天排名改善 {row['bsr_cr']:.0f}%")
           for asin, row in rows.items() if (row.get("bsr_cr") or 0) >= need - 1e-9]
    out.sort(key=lambda t: -t[1])
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


def record(state: dict, rows: dict[str, dict], today: date, pages: int, failed: list[str] | None = None) -> None:
    """存本期的名单和快照（同一天跑两次只留后一次），只留最近 SNAP_KEEP_DAYS 天的快照。failed：没翻全的子类目。"""
    uni = _uni(state)
    snaps = [s for s in uni["snaps"] if s["date"] != today.isoformat()
             and (today - clock.parse_day(s["date"])).days <= SNAP_KEEP_DAYS]
    snaps.append({"date": today.isoformat(), "units": {a: r.get("units") or 0 for a, r in rows.items()}})
    uni.update(date=today.isoformat(), pages=pages, rows=rows, snaps=snaps, failed=list(failed or []))


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


def features(state: dict, asin: str, row: dict, today: date, base_days: int = 28,
             short_days: tuple = (7, 14)) -> dict:
    """g（比基准的增长）、基准是什么（own = 自己存的快照 / month = 上个月的月度名单）、基准有几天、
    short = {N: 近 a 天日均 ÷ a 天前那份的日均}（N = short_days，a = 那份快照实际隔的天数，N±2 天里最接近的）。"""
    uni = _uni(state)
    snaps = [((today - clock.parse_day(s["date"])).days, s["units"]) for s in uni["snaps"]
             if s["date"] != today.isoformat() and clock.parse_day(s["date"])]
    u = float(row.get("units") or 0)
    out = {"u": u, "g": None, "base": None, "age": None, "short": {}}
    # 基准的先后：约 base_days 天前自己存的 → 24~47 天前自己存的 → 上个月的月度名单（21~47 天前结束）→ 21~23 天前自己存的
    kind = "own"
    pick = _nearest(snaps, asin, *STEADY, base_days) or _nearest(snaps, asin, 24, FALLBACK[1], base_days)
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
    for w in short_days:
        s = _nearest(snaps, asin, w - 2, w + 2, w)
        if s:
            a, base = s  # 按那份快照实际隔的天数算（每 3 天一期和每周一期都对）
            out["short"][w] = ((u - base + base / 30 * a) / a) / (base / 30)
    return out


def screen(state: dict, rows: dict[str, dict], today: date, cfg: dict) -> list[tuple[str, float, str]]:
    """可能达标、要拉日数据的商品：[(asin, 优先级, 依据)]，优先级从高到低（预算不够时先拉前面的）。规则见模块说明。"""
    pcfg = cfg["pool"]
    growth, short = float(pcfg["screen_growth"]), float(pcfg["screen_short"])
    young_days, bsr_cr = int(pcfg["opportunity_max_age_days"]), float(pcfg["screen_bsr_cr"])
    base_days, short_days = int(pcfg.get("screen_base_days", 28)), tuple(pcfg.get("screen_short_days") or (7, 14))
    out = []
    for asin, row in rows.items():
        f = features(state, asin, row, today, base_days, short_days)
        age = clock.days_between(clock.parse_day(row.get("available")), today)
        young = age is not None and age <= young_days
        shorts = list(f["short"].values())
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
            why.append(f"近 {' / '.join(map(str, f['short']))} 天 ×{max(shorts):.1f}")
        if f["base"] == "month" and (row.get("bsr_cr") or 0) >= bsr_cr:  # 基准是上个月的名单时（头三周）
            why.append(f"近 7 天排名改善 {row['bsr_cr']:.0f}%")
        if why:
            out.append((asin, max([f["g"]] + [r - 1 for r in shorts]), "；".join(why)))
    out.sort(key=lambda t: -t[1])
    return out


def node_tide(state: dict, today: date, cfg: dict, deal: dict | None = None, revision: dict | None = None) -> dict[str, float]:
    """品类大盘：每个子类目里量够的商品（本期的全市场名单），近 30 天销量 ÷ 约 30 天前那份的中位数（基准同 features：
    自己存的约 30 天前的快照，头一个月用上个月的月度名单）。潜力、上升中要先除以它再比门槛（“跑赢大盘”，analyze）。
    名单的近 30 天含大促日，按 deal（analyze.deal_factors）扣掉；基准是上个月的月度名单（已修订完）时，
    近 30 天里最后几天还会被卖家精灵下调，按 revision（本期测到的修订幅度）扣掉（两边都是自己存的快照时都含没修订的几天，不用扣）。
    商品数不到 thresholds.tide.min_items 的子类目不算；最多算到 thresholds.tide.max 倍。本期没翻全市场名单时返回 {}。"""
    tcfg = cfg["thresholds"].get("tide") or {}
    uni = _uni(state)
    if not tcfg.get("enabled", True) or uni.get("date") != today.isoformat() or not uni.get("rows"):
        return {}
    shrink = 1.0
    if revision and revision.get("window"):
        shrink = 1 - min(int(revision["window"]), 30) / 30 * (1 - float(revision.get("median") or 1))
    by_node: dict[str, list[float]] = {}
    for asin, row in uni["rows"].items():
        f = features(state, asin, row, today)
        if f["g"] is None or not row.get("node"):
            continue
        by_node.setdefault(row["node"], []).append((1 + f["g"]) * (shrink if f["base"] == "month" else 1.0))
    deal = deal or {}
    out = {}
    for node, ratios in by_node.items():
        if len(ratios) >= int(tcfg.get("min_items", 10)):
            tide = statistics.median(ratios) / float(deal.get(node, deal.get("*", 1.0)) or 1.0)
            out[node] = round(min(float(tcfg.get("max", 2.0)), tide), 3)
    return out


def run(vendor: Vendor, cfg: dict, state: dict, nodes: list[dict], today: date) -> dict[str, dict]:
    """翻本期的全市场名单（头一个月还要翻一次上个月的月度名单当基准），存快照。返回本期名单。"""
    start = vendor.billable_calls
    period = baseline_period(state, today)
    if period:
        rows = fetch(vendor, cfg, nodes, period)
        record_baseline(state, period, rows)  # 有子类目没翻全时，没基准的商品当新进名单（都拉日数据），不会漏
        log.info(f"全市场名单：头一个月还没有 30 天前的快照，拉了 {period[:4]}-{period[4:]} 的月度名单当基准（{len(rows)} 个商品，"
                 f"{vendor.billable_calls - start} 次调用）")
    mid = vendor.billable_calls
    failed: list[str] = []
    rows = fetch(vendor, cfg, nodes, failed=failed)
    record(state, rows, today, vendor.billable_calls - mid, failed)
    log.info(f"全市场名单：近 30 天量够的商品 {len(rows)} 个（{vendor.billable_calls - mid} 次调用"
             + (f"；{len(failed)} 个子类目查询失败、没翻全" if failed else "") + "）")
    young = _uni(state)["young"] = {}  # 先清掉上期的：这期没翻完时不拿旧的排名改善去选
    if (cfg["pool"].get("young") or {}).get("enabled"):
        start = vendor.billable_calls
        young = fetch_young(vendor, cfg, nodes, set(rows), failed)
        log.info(f"早期名单：上架半年内、量还不够全市场门槛的商品 {len(young)} 个（{vendor.billable_calls - start} 次调用）")
    _uni(state)["young"] = young
    return rows

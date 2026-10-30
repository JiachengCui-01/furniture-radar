"""持续热销的月度名单补判。

追踪池只有约 470 个商品（每个每期 1 次日数据调用），月度名单里够得上子类目第 20 名水平的约 540 个商品里有约 380 个
不在池子里（2026-10-20 的数据），它们原来不可能被判持续热销，持续热销对照组也只从池子里挑。这里用卖家精灵的子类目头部名单补上：

  往月名单：product_research 传月份（已结束月份的月末快照），每个子类目按销售额取前 50 个，和月度发现同一口径。
           保留最近 months 个月（持续热销看近 6 个月）。实测 2026-04 的床头柜名单里，同一 ASIN 的月销量和日数据接口的
           4 月销量一致（9399 / 9270、2106 / 2107、5742 / 5724）。月度发现查到的当月名单直接记进来，不另花调用；
           往月的一次性补，每期最多 budget.monthly_list_calls 次（只用核心刷新做完后剩下的预算）。
  近 30 天销量：持续热销的“近 28 天不低于半年平均”用当期的近 30 天销量 ÷ 30 判断（recent_rows）。来源是当期的全市场名单
           （universe.py，近 30 天量够的都在，不另花调用；同一父体两边的销量 99% 差在 ±10% 内）；它覆盖不到头部的子类目
           （小子类目的头部量不够全市场门槛、或这期没翻全，thin_nodes，约 12 个）另查近 30 天名单（product_research 不传月份，
           按销售额取前 50 个，每个子类目 1 次）。全市场名单没翻成时全部子类目都查。在拉日数据之前查，不会被初筛挤掉。

  按件数的往月名单：每个子类目按月销量取前 50（便宜、走量的头部进不了销售额前 50）。当月的由月度发现带上（discovery.fill_units），
           往月的一次性补（约 145 次，在拉日数据之前，每期最多 budget.monthly_list_calls 次）。

  名单更新完没有：卖家精灵上个月的名单月初还没更新完，而且是整个子类目没更新——这个子类目的商品月销量和再上个月一模一样。
           实测 10-09 拉的 9 月名单：衣帽架 49/49、Chest File Cabinets 12/12、Pantries 46/48 个商品和 8 月一模一样，其余 26 个子类目
           最多 14%（已更新完的名单相邻两个月 0.7%~1.5%）；10-22 重拉后销量变了一成以上的 43 个商品里 42 个在这 3 个子类目。
           所以每次拿到名单都逐个子类目查（not_updated），没更新完的记进 unsettled，之后每期只重拉这几个子类目，直到更新完：
           还是当期月份的，重拉月度发现里这几个子类目（discovery.refetch_nodes）；已经换了月份的，重拉那个月的两份名单（resettle）。
           以前是“月份结束后不到 20 天拉的，过了 20 天整份重拉一次”（约 75 次调用）：两周一期时 11-05 拉的 10 月名单，
           下一期 11-19 才第 19 天、再下一期已经是 12 月，整个 11 月都用没更新完的名单。

state["monthly_lists"] = {
    "months":  {"2026-09": {子类目 nodeIdPath: {asin: [月销量, 月销售额, 价格, 父体]}}},
    "units_months": {同上，按月销量取前 50},
    "rolling": {"date": "2026-10-20", "rows": {asin: 近 30 天名单的一行（discovery.normalize_product）}, "nodes": [查了的子类目]},
    "unsettled": {"2026-10": [卖家精灵还没更新完的子类目 nodeIdPath]},
}
判定（哪些商品、按什么条件）在 analyze.monthly_hot_items。
"""
from __future__ import annotations

from datetime import date

from . import log
from .discovery import keep_product, normalize_product
from .vendor import BudgetExhausted, Vendor, request

STATE_KEY = "monthly_lists"
PAGE_SIZE = 50


def _lists(state: dict) -> dict:
    lists = state.setdefault(STATE_KEY, {})
    lists.setdefault("months", {})
    return lists


def month_keys(period: str, n: int) -> list[str]:
    """以月度名单的月份（YYYYMM）为最后一个月，往前共 n 个月，近的在前：["2026-09", "2026-08", …]。"""
    y, m = int(period[:4]), int(period[4:6])
    out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _compact(row: dict) -> list:
    return [row.get("units") or 0, row.get("revenue") or 0, row.get("price"), row.get("parent")]


# 两种往月名单：销售额前 50（"top"，存在 months）和月销量前 50（"units"，存在 units_months）。
# 按件数的那份是给便宜、走量的头部的：持续热销的门槛按件数排第 20 名，但以前只有销售额前 50 的往月名单，
# 件数够第 20 名、进不了销售额前 50 的头部（价格中位数 $56，销售额前 50 里的 $95）判不了持续热销。
# 回测（2026-09，试跑数据，大子类目只覆盖三到五成，是下限）：这样的头部 ≥94 个、25 个达标；6 月判出的之后两个月保持住 83%（销售额前 50 里的 89%）
KINDS = {"top": ("months", "往月名单"), "units": ("units_months", "按件数的往月名单")}


def _store(state: dict, kind: str) -> dict:
    return _lists(state).setdefault(KINDS[kind][0], {})


def record_discovery(state: dict, disc: dict | None) -> None:
    """月度发现的头部名单就是那个月的名单，直接记进来（月度发现重新拉过的，整个子类目换成新的）：
    销售额前 50（来源 "top"）和月销量前 50（来源 "units"，discovery.fill_units 查过的子类目）。"""
    period = str((disc or {}).get("period") or "")
    if len(period) != 6 or not (disc or {}).get("products"):
        return
    key = f"{period[:4]}-{period[4:]}"
    for kind, done in (("top", disc.get("done_top", [n["path"] for n in disc.get("nodes") or []])),
                       ("units", disc.get("done_units") or [])):
        fresh: dict[str, dict] = {}
        for p in disc["products"].values():
            if kind in p["sources"]:
                fresh.setdefault(p["node"], {})[p["asin"]] = _compact(p)
        store = _store(state, kind).setdefault(key, {})
        for node in disc.get("nodes") or []:
            if node["path"] in done:
                store[node["path"]] = fresh.get(node["path"], {})


def complete(state: dict, nodes: list[dict], period: str, n: int, kind: str = "top") -> bool:
    """最近 n 个月每个子类目的名单都齐了。"""
    months = _store(state, kind)
    return all(node["path"] in (months.get(key) or {}) for key in month_keys(period, n) for node in nodes)


def list_units(state: dict, key: str, node: str) -> dict[str, float]:
    """某个月、某个子类目两份名单（销售额前 50、月销量前 50）里的 {父体（没有父体的按 ASIN）: 月销量}。"""
    out: dict[str, float] = {}
    for kind in KINDS:
        for asin, row in ((_store(state, kind).get(key) or {}).get(node) or {}).items():
            out[row[3] or asin] = row[0]
    return out


def not_updated(state: dict, cfg: dict, key: str, node: str) -> bool:
    """这个子类目 key 那个月的名单卖家精灵还没更新完：两个月都有销量的商品 ≥ discovery.settle_min_items 个，
    其中 ≥ discovery.settle_same_share 的月销量和上个月一模一样（见模块说明）。上个月的名单没有时判断不了，当作更新完了。"""
    dcfg = cfg["discovery"]
    prev = list_units(state, month_keys(key.replace("-", ""), 2)[1], node)
    pairs = [(u, prev[f]) for f, u in list_units(state, key, node).items() if u and prev.get(f)]
    if len(pairs) < int(dcfg.get("settle_min_items", 5)):
        return False
    return sum(1 for u, v in pairs if u == v) >= float(dcfg.get("settle_same_share", 0.5)) * len(pairs)


def unsettled(state: dict) -> dict[str, list[str]]:
    """{月份 "2026-10": [还没更新完的子类目]}。"""
    return _lists(state).setdefault("unsettled", {})


def check_discovery(state: dict, cfg: dict, disc: dict | None) -> list[str]:
    """月度发现的名单记进来以后（record_discovery），逐个子类目查当期那个月的名单更新完没有，记进 unsettled，返回没更新完的。"""
    period = str((disc or {}).get("period") or "")
    if len(period) != 6 or not (disc or {}).get("products"):
        return []
    key = f"{period[:4]}-{period[4:]}"
    flagged = [n["path"] for n in disc.get("nodes") or [] if not_updated(state, cfg, key, n["path"])]
    flags = unsettled(state)
    if flagged:
        flags[key] = flagged
    else:
        flags.pop(key, None)
    return flagged


def resettle(vendor: Vendor, cfg: dict, state: dict, disc: dict) -> int:
    """已经换了月份、当时还没更新完的往月名单：只重拉这几个子类目那个月的两份名单（销售额前 50、月销量前 50，每个子类目 2 次），
    再查一次更新完没有，还没更新完的下期再拉。不在近 hot.list_months 个月里、或已经不在监控范围的子类目不再管。返回调用次数。"""
    flags = unsettled(state)
    period = str(disc["period"])
    current = f"{period[:4]}-{period[4:]}"
    keep = set(month_keys(period, int(cfg["thresholds"]["hot"].get("list_months", 6))))
    nodes = {n["path"]: n for n in disc["nodes"]}
    start = vendor.billable_calls
    for key in sorted(flags):
        if key == current:
            continue  # 当期月份的由 ensure_discovery 重拉（discovery.refetch_nodes）
        paths = [p for p in flags[key] if p in nodes] if key in keep else []
        still: list[str] = []
        for i, path in enumerate(paths):
            got: dict[str, dict] = {}
            try:
                for kind in KINDS:
                    order = cfg["discovery"]["order_field"] if kind == "top" else "total_units"
                    reply = vendor.call("product_research", request(
                        marketplace=cfg["marketplace"], nodeIdPath=path, nodeIdPathEqual="false",
                        month=key.replace("-", ""), size=PAGE_SIZE, page=1,
                        variation="Y" if cfg["discovery"].get("merge_variations", True) else None,
                        order={"field": order, "desc": True}), purpose=f"重拉没更新完的{KINDS[kind][1]}")
                    if not reply.ok and reply.status != "empty":
                        break
                    got[kind] = {}
                    for rank, row in enumerate(reply.rows, start=1):
                        item = normalize_product(row, nodes[path], kind, rank)
                        if keep_product(item, cfg):
                            got[kind][item["asin"]] = _compact(item)
            except BudgetExhausted:
                still += paths[i:]
                break
            if len(got) < len(KINDS):
                still.append(path)  # 没拉全：下期再拉
                continue
            for kind, rows in got.items():
                _store(state, kind).setdefault(key, {})[path] = rows
            if not_updated(state, cfg, key, path):
                still.append(path)
        if still:
            flags[key] = still
        else:
            flags.pop(key, None)
    calls = vendor.billable_calls - start
    if calls:
        log.info(f"往月名单：重拉了上次还没更新完的子类目（{calls} 次调用）"
                 + (f"，还有 {sum(len(v) for k, v in flags.items() if k != current)} 个没更新完，下期再拉"
                    if any(k != current for k in flags) else "，都已更新完"))
    return calls


def backfill(vendor: Vendor, cfg: dict, state: dict, disc: dict, limit: int, kind: str = "top") -> int:
    """补还缺的往月名单（近的先补），最多 limit 次调用。太早的月份不再保留。返回调用次数。
    kind：top = 按销售额取前 50（和月度发现同一口径）；units = 按月销量取前 50。"""
    n = int(cfg["thresholds"]["hot"].get("list_months", 6))
    keys = month_keys(str(disc["period"]), n)
    months, purpose = _store(state, kind), KINDS[kind][1]
    order = cfg["discovery"]["order_field"] if kind == "top" else "total_units"
    for old in [k for k in months if k not in keys]:
        months.pop(old, None)
    start = vendor.billable_calls
    try:
        for key in keys:
            for node in disc["nodes"]:
                if node["path"] in (months.get(key) or {}):
                    continue
                if vendor.billable_calls - start >= limit:
                    raise BudgetExhausted(f"{purpose}：本期名额用完")
                reply = vendor.call("product_research", request(
                    marketplace=cfg["marketplace"], nodeIdPath=node["path"], nodeIdPathEqual="false",
                    month=key.replace("-", ""), size=PAGE_SIZE, page=1,
                    variation="Y" if cfg["discovery"].get("merge_variations", True) else None,
                    order={"field": order, "desc": True}), purpose=purpose)
                if not reply.ok and reply.status != "empty":
                    log.warn(f"{purpose} {key} {node.get('name') or node['path']}：{reply.status} {reply.detail}，下期再补")
                    continue  # 没拿到就不记，免得被当成“这个月名单是空的”
                rows = {}
                for rank, row in enumerate(reply.rows, start=1):
                    item = normalize_product(row, node, kind, rank)
                    if keep_product(item, cfg):
                        rows[item["asin"]] = _compact(item)
                months.setdefault(key, {})[node["path"]] = rows
    except BudgetExhausted:
        pass
    calls = vendor.billable_calls - start
    if calls:
        have = sum(1 for k in keys if all(nd["path"] in (months.get(k) or {}) for nd in disc["nodes"]))
        log.info(f"{purpose}：补了 {calls} 次调用，{have}/{len(keys)} 个月已齐")
    return calls


def thin_nodes(disc: dict, cfg: dict, failed=()) -> set[str]:
    """全市场名单覆盖不到头部的子类目（nodeIdPath）：月度名单里有头部商品的量不够全市场门槛（≥opportunity_min_units 件
    或 ≥opportunity_min_revenue 美元）的小子类目，以及这期全市场名单没翻全的（failed）。2026-10-22 的近 30 天名单：
    28 个子类目里 12 个有这样的头部，10-23 那期补判上榜的 113 个里 4 个在全市场名单里看不到。"""
    pcfg = cfg["pool"]
    floor_units, floor_revenue = float(pcfg["opportunity_min_units"]), float(pcfg.get("opportunity_min_revenue") or float("inf"))
    out = set(failed or [])
    for p in ((disc or {}).get("products") or {}).values():
        if "top" in p["sources"] and (p.get("units") or 0) < floor_units and (p.get("revenue") or 0) < floor_revenue:
            out.add(p["node"])
    return out


def recent_rows(state: dict, nodes: list[dict], today: date | None) -> tuple[dict[str, dict], set[str]]:
    """持续热销补判用的近 30 天销量：当期的全市场名单（每个子类目里近 30 天量够的都在，不另花调用）+ 当期查的近 30 天名单
    （全市场名单覆盖不到头部的子类目，见 thin_nodes）。返回 (行 {asin: 行}, 有当期近 30 天销量的子类目)。
    不是当期的不用（以前近 30 天名单放在最后用剩下的预算查，初筛把预算占满时整期不查，补判用的是好几天前的旧名单）。
    today 为空时（旧的调用方式）不看日期。"""
    day = today.isoformat() if today else None
    uni = state.get("universe") or {}
    rolling = _lists(state).get("rolling") or {}
    rows: dict[str, dict] = {}
    covered: set[str] = set()
    if day and uni.get("date") == day and uni.get("rows"):
        rows.update(uni["rows"])
        covered |= {n["path"] for n in nodes} - set(uni.get("failed") or [])
    if rolling.get("rows") is not None and (day is None or rolling.get("date") == day):
        rows.update(rolling.get("rows") or {})
        covered |= set(rolling.get("nodes") or [])
    return rows, covered


def fetch_rolling(vendor: Vendor, cfg: dict, state: dict, disc: dict, today: date, limit: int,
                  only: set[str] | None = None) -> int:
    """子类目的近 30 天名单（截至当天），按销售额取前 50 个。only：只查这些子类目（全市场名单覆盖不到头部的，thin_nodes），
    空 = 全部。返回调用次数；没查完的子类目这期不补判。"""
    rows: dict[str, dict] = {}
    done: list[str] = []
    start = vendor.billable_calls
    try:
        for node in [n for n in disc["nodes"] if only is None or n["path"] in only]:
            if vendor.billable_calls - start >= limit:
                raise BudgetExhausted("近 30 天名单：本期名额用完")
            reply = vendor.call("product_research", request(
                marketplace=cfg["marketplace"], nodeIdPath=node["path"], nodeIdPathEqual="false",
                size=PAGE_SIZE, page=1, variation="Y" if cfg["discovery"].get("merge_variations", True) else None,
                order={"field": cfg["discovery"]["order_field"], "desc": True}), purpose="近30天名单")
            if not reply.ok and reply.status != "empty":
                log.warn(f"近 30 天名单 {node.get('name') or node['path']}：{reply.status} {reply.detail}")
                continue  # 这个子类目这期不补判
            for rank, row in enumerate(reply.rows, start=1):
                item = normalize_product(row, node, "rolling", rank)
                if keep_product(item, cfg):
                    rows.setdefault(item["asin"], item)
            done.append(node["path"])
    except BudgetExhausted:
        log.warn(f"近 30 天名单：预算不足，只查了 {len(done)}/{len(disc['nodes'])} 个子类目")
    _lists(state)["rolling"] = {"date": today.isoformat(), "rows": rows, "nodes": done}
    return vendor.billable_calls - start


def _row(state: dict, key: str, node: str, asin: str, parent: str | None) -> list | None:
    rows = ((_lists(state)["months"].get(key) or {}).get(node)) or {}
    row = rows.get(asin)
    if row is None and parent:
        row = next((r for r in rows.values() if r[3] == parent), None)
    return row


def growth_pct(state: dict, p: dict, period: str) -> float | None:
    """月度名单里这个商品本月比上月的销量增长（%），用自己存的往月名单算；上月不在名单里返回 None。
    卖家精灵行里的 unitsGr 字段有两成是 0 或空的（598 个头部商品里 133 个），和日数据接口比误差中位数 5.9%，自己算的 2.6%。"""
    prev = _row(state, month_keys(period, 2)[1], p["node"], p["asin"], p.get("parent"))
    if not prev or not prev[0] or p.get("units") is None:
        return None
    return round((p["units"] / prev[0] - 1) * 100, 2)


def rolling_ratio(state: dict, p: dict, period: str, n: int) -> float | None:
    """近 30 天名单的日均 ÷ 近 n 个月名单里的日均：在往上走的头部商品（没有日数据、判不了上升中的）。
    近 30 天名单里没有它、或某个月不在名单里返回 None。"""
    rolling = (_lists(state).get("rolling") or {}).get("rows") or {}
    r = rolling.get(p["asin"]) or next((x for x in rolling.values() if p.get("parent") and x.get("parent") == p["parent"]), None)
    months = months_of(state, p["node"], p["asin"], p.get("parent"), month_keys(period, n))
    if not r or not months:
        return None
    daily = sum(u / _days(k) for k, u, *_ in months) / len(months)
    return round((r.get("units") or 0) / 30 / daily, 2) if daily > 0 else None


def _days(key: str) -> int:
    y, m = int(key[:4]), int(key[5:])
    return ((date(y + (m == 12), m % 12 + 1, 1)) - date(y, m, 1)).days


def months_of(state: dict, node: str, asin: str, parent: str | None, keys: list[str]) -> list[list] | None:
    """这个商品在 keys 这几个月的 [月份, 月销量, 月销售额, 价格]（按时间升序）：每个月在销售额前 50 或月销量前 50 的名单里找。
    有一个月两份名单里都没有就返回 None：跌出了头部的月份看不到销量。同一父体换了代表的子体时按父体对上。"""
    out = []
    for key in sorted(keys):
        row = None
        for kind in KINDS:
            rows = (_store(state, kind).get(key) or {}).get(node) or {}
            row = rows.get(asin)
            if row is None and parent:
                row = next((r for r in rows.values() if r[3] == parent), None)
            if row is not None:
                break
        if row is None:
            return None
        out.append([key, row[0], row[1], row[2]])
    return out

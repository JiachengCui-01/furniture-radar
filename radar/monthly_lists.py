"""持续热销的月度名单补判。

追踪池只有约 470 个商品（每个每期 1 次日数据调用），月度名单里够得上子类目第 20 名水平的约 540 个商品里有约 380 个
不在池子里（2026-10-20 的数据），它们原来不可能被判持续热销，持续热销对照组也只从池子里挑。这里用卖家精灵的子类目头部名单补上：

  往月名单：product_research 传月份（已结束月份的月末快照），每个子类目按销售额取前 50 个，和月度发现同一口径。
           保留最近 months 个月（持续热销看近 6 个月）。实测 2026-04 的床头柜名单里，同一 ASIN 的月销量和日数据接口的
           4 月销量一致（9399 / 9270、2106 / 2107、5742 / 5724）。月度发现查到的当月名单直接记进来，不另花调用；
           往月的一次性补，每期最多 budget.monthly_list_calls 次（只用核心刷新做完后剩下的预算）。
  近 30 天名单：product_research 不传月份（截至当天的近 30 天），每个子类目按销售额取前 50 个。往月名单补齐后每期都查
           （每个子类目 1 次）：持续热销的“近 28 天不低于半年平均”用它的近 30 天销量 ÷ 30 判断。

state["monthly_lists"] = {
    "months":  {"2026-09": {子类目 nodeIdPath: {asin: [月销量, 月销售额, 价格, 父体]}}},
    "rolling": {"date": "2026-10-20", "rows": {asin: 近 30 天名单的一行（discovery.normalize_product）}},
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


def record_discovery(state: dict, disc: dict | None) -> None:
    """月度发现的头部名单就是那个月的名单，直接记进来（月度发现重新拉过的，整个子类目换成新的）。"""
    period = str((disc or {}).get("period") or "")
    if len(period) != 6 or not (disc or {}).get("products"):
        return
    fresh: dict[str, dict] = {}
    for p in disc["products"].values():
        if "top" in p["sources"]:
            fresh.setdefault(p["node"], {})[p["asin"]] = _compact(p)
    store = _lists(state)["months"].setdefault(f"{period[:4]}-{period[4:]}", {})
    for node in disc.get("nodes") or []:
        if node["path"] in disc.get("done_top", [node["path"]]):
            store[node["path"]] = fresh.get(node["path"], {})


def complete(state: dict, nodes: list[dict], period: str, n: int) -> bool:
    """最近 n 个月每个子类目的名单都齐了。"""
    months = _lists(state)["months"]
    return all(node["path"] in (months.get(key) or {}) for key in month_keys(period, n) for node in nodes)


def backfill(vendor: Vendor, cfg: dict, state: dict, disc: dict, limit: int) -> int:
    """补还缺的往月名单（近的先补），最多 limit 次调用。太早的月份不再保留。返回调用次数。"""
    n = int(cfg["thresholds"]["hot"].get("list_months", 6))
    keys = month_keys(str(disc["period"]), n)
    lists = _lists(state)
    for old in [k for k in lists["months"] if k not in keys]:
        lists["months"].pop(old, None)
    start = vendor.billable_calls
    try:
        for key in keys:
            for node in disc["nodes"]:
                if node["path"] in (lists["months"].get(key) or {}):
                    continue
                if vendor.billable_calls - start >= limit:
                    raise BudgetExhausted("往月名单：本期名额用完")
                reply = vendor.call("product_research", request(
                    marketplace=cfg["marketplace"], nodeIdPath=node["path"], nodeIdPathEqual="false",
                    month=key.replace("-", ""), size=PAGE_SIZE, page=1,
                    variation="Y" if cfg["discovery"].get("merge_variations", True) else None,
                    order={"field": cfg["discovery"]["order_field"], "desc": True}), purpose="往月名单")
                if not reply.ok and reply.status != "empty":
                    log.warn(f"往月名单 {key} {node.get('name') or node['path']}：{reply.status} {reply.detail}，下期再补")
                    continue  # 没拿到就不记，免得被当成“这个月名单是空的”
                rows = {}
                for rank, row in enumerate(reply.rows, start=1):
                    item = normalize_product(row, node, "top", rank)
                    if keep_product(item, cfg):
                        rows[item["asin"]] = _compact(item)
                lists["months"].setdefault(key, {})[node["path"]] = rows
    except BudgetExhausted:
        pass
    calls = vendor.billable_calls - start
    if calls:
        have = sum(1 for k in keys if all(nd["path"] in (lists["months"].get(k) or {}) for nd in disc["nodes"]))
        log.info(f"往月名单：补了 {calls} 次调用，{have}/{len(keys)} 个月已齐")
    return calls


def fetch_rolling(vendor: Vendor, cfg: dict, state: dict, disc: dict, today: date, limit: int) -> int:
    """每个子类目的近 30 天名单（截至当天），按销售额取前 50 个。返回调用次数；没查完的子类目这期不补判。"""
    rows: dict[str, dict] = {}
    done: list[str] = []
    start = vendor.billable_calls
    try:
        for node in disc["nodes"]:
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


def months_of(state: dict, node: str, asin: str, parent: str | None, keys: list[str]) -> list[list] | None:
    """这个商品在 keys 这几个月的 [月份, 月销量, 月销售额, 价格]（按时间升序）。有一个月不在名单里就返回 None：
    跌出子类目销售额前 50 的月份看不到销量。同一父体换了代表的子体时按父体对上。"""
    months = _lists(state)["months"]
    out = []
    for key in sorted(keys):
        rows = (months.get(key) or {}).get(node) or {}
        row = rows.get(asin)
        if row is None and parent:
            row = next((r for r in rows.values() if r[3] == parent), None)
        if row is None:
            return None
        out.append([key, row[0], row[1], row[2]])
    return out

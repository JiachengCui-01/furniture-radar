"""子类目月度销量历史（季节模型 detect/seasonality.py 的数据）。

卖家精灵 market_research 传月份查：每个根类目一次返回下面所有子类目的当月销量合计
（nodeIdPathEqual=false，含后代，和月度发现同一口径），可以查到 3 年前（实测 2023-10 有数据）。
整个子类目所有商品的合计，不是只算我们追踪的商品，所以没有“只看得到现在还在卖的”这种偏差。

state["category_history"] = {
    "months":  {"2026-09": {子类目 nodeIdPath: 月销量}},   只存本期追踪范围里的子类目
    "scanned": {"2026-09": [翻完了所有页的根类目]},
}
一个月份、一个根类目算补完：这个根类目下要追踪的子类目都找到了，或者翻完了所有页（最多 max_pages 页）。
每期补最近 history_months 个已结束月份里还缺的（近的先补），只用核心刷新做完后剩下的预算、最多 budget.history_calls 次
（3 年约 110 次，两期补完），不影响追踪商品的刷新。月度发现查到的当月数据直接记进来，不另花调用。
"""
from __future__ import annotations

from datetime import date

from . import log
from .vendor import BudgetExhausted, Vendor, request

STATE_KEY = "category_history"
PAGE_SIZE = 50


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def month_keys(today: date, n: int) -> list[str]:
    """最近 n 个已结束月份，近的在前：["2026-09", "2026-08", …]。"""
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return out


def _targets(nodes: list[dict]) -> dict[str, set[str]]:
    by_root: dict[str, set[str]] = {}
    for node in nodes or []:
        by_root.setdefault(node.get("root") or node["path"], set()).add(node["path"])
    return by_root


def _hist(state: dict) -> dict:
    hist = state.setdefault(STATE_KEY, {})
    hist.setdefault("months", {})
    hist.setdefault("scanned", {})
    return hist


def complete(state: dict, month: str, root: str, want: set[str]) -> bool:
    hist = _hist(state)
    return want <= set(hist["months"].get(month) or {}) or root in (hist["scanned"].get(month) or [])


def record(state: dict, month: str, rows: list[dict], wanted: set[str]) -> None:
    """把一页 market_research 的行（或月度发现选出的子类目）记进来，只留要追踪的子类目。"""
    store = _hist(state)["months"].setdefault(month, {})
    for row in rows:
        path = str(row.get("nodeIdPath") or row.get("path") or "")
        units = _num(row.get("totalUnits", row.get("units")))
        if path in wanted and units is not None:
            store[path] = units


def record_discovery(state: dict, disc: dict | None) -> None:
    """月度发现用的就是上个月的 market_research，选出的子类目带当月销量，直接记进历史。"""
    period = str((disc or {}).get("period") or "")
    if len(period) != 6 or not (disc or {}).get("nodes"):
        return
    wanted = {n["path"] for n in disc["nodes"]}
    record(state, f"{period[:4]}-{period[4:]}", disc["nodes"], wanted)


def series(state: dict, path: str) -> dict[str, float]:
    """一个子类目的 {月份: 月销量}。"""
    return {m: v[path] for m, v in (_hist(state)["months"]).items() if path in v}


def backfill(vendor: Vendor, cfg: dict, state: dict, nodes: list[dict], today: date, limit: int) -> int:
    """补还缺的月份，最多 limit 次调用（预算用完就停）。返回调用次数。"""
    hcfg = cfg["thresholds"]["seasonality"]
    targets = _targets(nodes)
    months = month_keys(today, int(hcfg["history_months"]))
    hist = _hist(state)
    for old in [m for m in hist["months"] if m not in months]:  # 太早的不再保留
        hist["months"].pop(old, None)
        hist["scanned"].pop(old, None)
    start = vendor.billable_calls
    max_pages = int(hcfg.get("max_pages", 4))
    try:
        for month in months:
            for root, want in targets.items():
                page = 0
                while not complete(state, month, root, want) and page < max_pages:
                    if vendor.billable_calls - start >= limit:
                        raise BudgetExhausted("类目历史：本期名额用完")
                    page += 1
                    reply = vendor.call("market_research", request(
                        marketplace=cfg["marketplace"], nodeIdPath=root, nodeIdPathEqual="false",
                        month=month.replace("-", ""), size=PAGE_SIZE, page=page,
                        order={"field": "total_amount", "desc": True}), purpose="类目历史")
                    record(state, month, reply.rows, want)
                    if len(reply.rows) < PAGE_SIZE or page >= max_pages:
                        hist["scanned"].setdefault(month, []).append(root)
    except BudgetExhausted:
        pass
    calls = vendor.billable_calls - start
    if calls:
        have = sum(1 for m in months if all(complete(state, m, r, w) for r, w in targets.items()))
        log.info(f"类目历史：补了 {calls} 次调用，{have}/{len(months)} 个月已齐")
    return calls

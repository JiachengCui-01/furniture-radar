"""子类目月度销量历史（季节模型 detect/seasonality.py 的数据）。

卖家精灵 market_research 传月份查：每个根类目一次返回下面所有子类目的当月销量合计
（nodeIdPathEqual=false，含后代，和月度发现同一口径），可以查到 3 年前（实测 2023-10 有数据）。
整个子类目所有商品的合计，不是只算我们追踪的商品，所以没有“只看得到现在还在卖的”这种偏差。

state["category_history"] = {
    "months":  {"2026-09": {子类目 nodeIdPath: 月销量}},   只存本期追踪范围里的子类目
    "scanned": {"2026-09": [翻完了的根类目]},
    "pages":   {"2026-09|根类目": 翻到第几页 或 "end"（翻到底了）},
}
一个月份、一个根类目算补完：这个根类目下要追踪的子类目都找到了，或者翻到底了 / 翻满 max_pages_missing 页还找不到。
先翻 max_pages 页；还缺子类目（那个月它的销售额排在 max_pages×50 名以后）就接着翻到 max_pages_missing 页
（以前翻满 max_pages 页就记成补完：实测办公类一个子类目缺了 2024-10、2024-12 两个月，36 个月凑不齐，永远判不成季节性品类）。
查询失败（被拒 / 连不上）不算翻完，下期接着补。
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


def _deep(cfg_or_none: dict | None) -> int:
    hcfg = cfg_or_none or {}
    return int(hcfg.get("max_pages_missing") or 2 * int(hcfg.get("max_pages", 4)))


def complete(state: dict, month: str, root: str, want: set[str], hcfg: dict | None = None) -> bool:
    """要追踪的子类目都有了，或者这个根类目翻到底了 / 翻满 max_pages_missing 页（hist["pages"] 记着）。
    以前的记录只有 scanned、没有 pages 的（翻满 max_pages 页就记成补完的），缺子类目时不算补完，接着往后翻。"""
    hist = _hist(state)
    if want <= set(hist["months"].get(month) or {}):
        return True
    page = (hist.get("pages") or {}).get(f"{month}|{root}")
    return root in (hist["scanned"].get(month) or []) and (page == "end" or (isinstance(page, int) and page >= _deep(hcfg)))


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
        for key in [k for k in hist.get("pages") or {} if k.startswith(f"{old}|")]:
            hist["pages"].pop(key)
    start = vendor.billable_calls
    max_pages, deep = int(hcfg.get("max_pages", 4)), _deep(hcfg)
    pages = hist.setdefault("pages", {})
    try:
        for month in months:
            for root, want in targets.items():
                key = f"{month}|{root}"
                if complete(state, month, root, want, hcfg):
                    continue
                # 接着上次翻到的页往后翻；以前只记了 scanned 的（翻满了 max_pages 页）从 max_pages 页之后接着翻
                done = pages.get(key)
                page = done if isinstance(done, int) else (max_pages if root in (hist["scanned"].get(month) or []) else 0)
                while not want <= set(hist["months"].get(month) or {}) and page < deep:
                    if vendor.billable_calls - start >= limit:
                        raise BudgetExhausted("类目历史：本期名额用完")
                    reply = vendor.call("market_research", request(
                        marketplace=cfg["marketplace"], nodeIdPath=root, nodeIdPathEqual="false",
                        month=month.replace("-", ""), size=PAGE_SIZE, page=page + 1,
                        order={"field": "total_amount", "desc": True}), purpose="类目历史")
                    if not reply.ok and reply.status != "empty":
                        log.warn(f"类目历史 {month} 第 {page + 1} 页查询失败（{reply.status}），下期再补")
                        break  # 不算翻完
                    page += 1
                    pages[key] = page
                    record(state, month, reply.rows, want)
                    if len(reply.rows) < PAGE_SIZE:
                        pages[key] = "end"
                        break
                if pages.get(key) == "end" or (isinstance(pages.get(key), int) and pages[key] >= deep):
                    if root not in (hist["scanned"].get(month) or []):
                        hist["scanned"].setdefault(month, []).append(root)
    except BudgetExhausted:
        pass
    calls = vendor.billable_calls - start
    if calls:
        have = sum(1 for m in months if all(complete(state, m, r, w, hcfg) for r, w in targets.items()))
        log.info(f"类目历史：补了 {calls} 次调用，{have}/{len(months)} 个月已齐")
    return calls

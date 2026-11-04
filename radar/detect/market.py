"""各子类目的市场面：只读本期翻到的全市场名单（state["universe"]["rows"]，近 30 天 ≥300 件或 ≥4 万美元的商品），0 次调用。
报告“开发方向”一览和小窗里的价格带、新品占比、头部评论、品牌集中度都来自这里，不参与任何判定。

  主力价格带 = 按近 30 天销售额加权的售价 P25~P75（中位 = P50）：一半的销售额落在这个区间；
  新品占比   = 上架 ≤ new_days 天的商品占这个子类目名单销售额的比例（新链接能不能分到钱）；
  头部评论   = 名单里销售额前 head_n 名的评论数中位数（新品要追的评论量）；
  品牌集中度 = 销售额前 top_brands 个已知品牌占名单销售额的比例（缺品牌的商品不算任何品牌）；
  价格分布   = 各价格段（EDGES，约每档 ×1.4）占名单销售额的比例，小窗里画成柱子。
名单不是本期翻的（日期 ≠ today，例如本期预算不够没翻）时返回空：不拿旧名单说现在的市场。
名单里的销量、销售额是父体合计（variation=Y），这里只用比例和中位数，不写绝对金额。"""
from __future__ import annotations

from datetime import date

from ..discovery import keep_product
from .design import known_brand_key

EDGES = (0, 25, 35, 50, 70, 100, 140, 200, 280, 400, 560, 800, float("inf"))


def node_of(row: dict) -> str:
    return row.get("node_cn") or row.get("node_name") or ""


def _weighted(pairs: list[tuple[float, float]], q: float) -> float | None:
    """按权重的分位数：从低价往上累计销售额，第一个达到 q × 合计的售价。"""
    total = sum(w for _p, w in pairs)
    if total <= 0:
        return None
    acc = 0.0
    for price, w in sorted(pairs):
        acc += w
        if acc >= q * total - 1e-9:
            return price
    return pairs[-1][0]


def _age(row: dict, today: date) -> int | None:
    try:
        return (today - date.fromisoformat(str(row.get("available") or "")[:10])).days
    except ValueError:
        return None


def summarize(state: dict, today: date, cfg: dict) -> dict[str, dict]:
    """{子类目中文名: {...}}，口径见模块说明。"""
    uni = state.get("universe") or {}
    if uni.get("date") != today.isoformat():
        return {}
    c = (cfg["report"].get("market") or {})
    new_days, head_n, top_brands = int(c.get("new_days", 180)), int(c.get("head_n", 20)), int(c.get("top_brands", 5))
    groups: dict[str, list[dict]] = {}
    for row in (uni.get("rows") or {}).values():
        if node_of(row) and keep_product(row, cfg):
            groups.setdefault(node_of(row), []).append(row)
    out = {}
    for node, rows in groups.items():
        revenue = sum(float(r.get("revenue") or 0) for r in rows)
        if revenue <= 0:
            continue
        pairs = [(float(r["price"]), float(r.get("revenue") or 0)) for r in rows if r.get("price")]
        new = [r for r in rows if (age := _age(r, today)) is not None and age <= new_days]
        heads = sorted(rows, key=lambda r: -float(r.get("revenue") or 0))[:head_n]
        reviews = sorted(float(r.get("ratings") or 0) for r in heads)
        brands: dict[str, float] = {}
        for r in rows:
            if (key := known_brand_key(r)) is not None:
                brands[key] = brands.get(key, 0.0) + float(r.get("revenue") or 0)
        hist = [0.0] * (len(EDGES) - 1)
        for price, w in pairs:
            hist[next(k for k in range(len(EDGES) - 1) if price < EDGES[k + 1])] += w
        priced = sum(w for _p, w in pairs)
        mid = len(reviews) // 2
        out[node] = {
            "n": len(rows), "n_new": len(new), "new_days": new_days,
            "new_share": round(sum(float(r.get("revenue") or 0) for r in new) / revenue, 3),
            "head_n": len(heads),
            "head_reviews": (reviews[mid] if len(reviews) % 2 else (reviews[mid - 1] + reviews[mid]) / 2) if reviews else None,
            "top_brands": top_brands,
            "brand_share": round(sum(sorted(brands.values(), reverse=True)[:top_brands]) / revenue, 3),
            "price": {"p25": _weighted(pairs, 0.25), "p50": _weighted(pairs, 0.5), "p75": _weighted(pairs, 0.75)} if pairs else None,
            "hist": [round(h / priced, 4) for h in hist] if priced else [],
        }
    return out

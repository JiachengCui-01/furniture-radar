"""把每个追踪中的 ASIN 跑一遍全部判定，得到带标签的结果列表。纯计算，不调接口。"""
from __future__ import annotations

from datetime import date

from . import clock
from .detect import fakehot, traits
from .detect.design import main_material
from .discovery import clean_text
from .detect.rules import (current_rating, evaluate_hot, evaluate_potential, evaluate_surge, low_rating,
                           rating_floor)
from .series import Daily, closed_months, median

def display_name(brand: str, title: str) -> str:
    """标题通常以品牌开头，避免出现“Avenco Avenco SleepSphere”。"""
    brand, title = (brand or "").strip(), (title or "").strip()
    if not brand or title.lower().startswith(brand.lower()):
        return title
    return f"{brand} {title}"


LABEL_CN = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销", "fake": "异常信号", "watch": "观察",
            "low": "评分偏低"}
# 不进入任何榜单和外观分析样本的标签
UNLISTED = ("fake", "low")


def node_stats(disc: dict | None, cfg: dict) -> dict[str, dict]:
    rank = int(cfg["thresholds"]["hot"]["rank_in_node"])
    by_node: dict[str, list[dict]] = {}
    for p in ((disc or {}).get("products") or {}).values():
        by_node.setdefault(p["node"], []).append(p)
    stats = {}
    for node, items in by_node.items():
        per_parent: dict[str, float] = {}
        for p in items:
            if "top" in p["sources"]:
                key = p.get("parent") or p["asin"]
                per_parent[key] = max(per_parent.get(key, 0), p["units"] or 0)
        units = sorted(per_parent.values(), reverse=True)
        threshold = (units[rank - 1] if len(units) >= rank else units[-1]) if units else None
        stats[node] = {
            "median_rate": median([p.get("ratings_rate") for p in items]),
            "threshold": threshold,
            "count": len(items),
        }
    return stats


def _last(values: list):
    for v in reversed(values or []):
        if v is not None:
            return v
    return None


def evaluate(asin: str, rec: dict, disc_row: dict | None, stats: dict, cfg: dict, today: date) -> dict:
    th = cfg["thresholds"]
    deals = cfg.get("deal_windows") or []
    d = Daily.from_record(rec.get("series"))
    as_of = d.as_of() if d else None
    idx = d.index_of(as_of) if d and as_of else None
    node = rec.get("node") or (disc_row or {}).get("node")
    st = stats.get(node, {})
    stale = as_of is None or (today - as_of).days > th["stale_days"]

    surge = evaluate_surge(d, idx, th["surge"], deals) if idx is not None else None
    restock = None
    if surge and surge.get("restock"):
        restock, surge = surge, None
    new_ramp = None
    if surge and surge.get("from_zero"):
        sc = th["surge"]
        node_daily = (st.get("threshold") or 0) / 30
        need = max(sc["new_listing_min_daily"], sc["new_listing_node_share"] * node_daily)
        if surge["recent_avg"] < need:
            new_ramp, surge = surge, None
    hot = evaluate_hot(rec.get("months") or [], d, as_of, st.get("threshold"), th["hot"])
    potential = evaluate_potential(rec, d, as_of, th["potential"]) if idx is not None else None
    if stale:  # 日数据停更（下架/断货/数据延迟）时不做“正在爆/正在涨”的判断
        surge = potential = None
        new_ramp = None
    fake = fakehot.score(rec, d, as_of, disc_row, st.get("median_rate"), th["fake"], deals)
    rising = None
    if idx is not None and not stale and not surge and not restock:
        rcfg = th.get("rising") or {}
        avg28, prev28 = _avg(d, idx, 28), _avg(d, idx - 28, 28)
        if avg28 and prev28 and avg28 >= rcfg.get("min_daily_sales", 3) \
                and avg28 / prev28 >= rcfg.get("min_ratio", 1.3):
            rising = {"ratio": round(avg28 / prev28, 2), "avg28": avg28}

    rating, n_ratings = current_rating(rec, disc_row)
    low = low_rating(rating, n_ratings, th)
    if low:  # 评分不达标：不进任何榜单，由排在后面的商品依次补位
        label = "low"
    elif fake["level"]:
        label = "fake"
    elif surge:
        label = "surge"
    elif potential:
        label = "potential"
    elif hot:
        label = "hot"
    else:
        label = "watch"

    tags: list[str] = []
    if low:
        floor = rating_floor(n_ratings, th)
        few = f"评论仅 {int(n_ratings)} 条，" if floor > th["min_rating"] else ""
        tags.append(f"{few}评分 {rating} 低于 {floor}")
    if surge:
        tags.append(surge["kind"])
        if surge["seasonal"]:
            tags.append("季节性")
        if surge["deal"]:
            tags.append(f"大促：{surge['deal']}")
        if surge["price_driven"]:
            tags.append("降价驱动")
    if rising and label in ("watch", "hot"):
        tags.append(f"上升中 ×{rising['ratio']:.1f}")
    if restock:
        tags.append("断货恢复")
    if new_ramp:
        tags.append("新品起量")
    if hot and label != "hot":
        tags.append("持续热销")
    if potential and label not in ("potential",):
        tags.append("潜力")
    if label == "fake" and surge:
        tags.insert(0, "假爆火嫌疑")
    tags.extend(fake["tags"])
    if stale:
        tags.append("数据陈旧")

    age = clock.days_between(clock.parse_day(rec.get("available")), as_of or today)
    obs = (rec.get("obs") or [{}])[-1]
    months = closed_months(rec.get("months") or [], as_of)
    return {
        "asin": asin,
        "title": clean_text(rec.get("title") or (disc_row or {}).get("title") or asin),
        "brand": clean_text(rec.get("brand") or (disc_row or {}).get("brand") or ""),
        "image": rec.get("image") or (disc_row or {}).get("image") or "",
        "node": node,
        "node_name": rec.get("node_name") or (disc_row or {}).get("node_name") or "",
        "node_cn": rec.get("node_cn") or (disc_row or {}).get("node_cn") or "",
        "price": (_last(d.price) if d else None) or rec.get("price"),
        "bsr": _last(d.bsr) if d else None,
        "rating": rating,
        "ratings": n_ratings,
        "variations": rec.get("variations"),
        "age_days": age,
        "as_of": as_of.isoformat() if as_of else None,
        "stale": stale,
        "label": label,
        "label_cn": LABEL_CN[label],
        "tags": tags,
        "surge": surge,
        "restock": restock,
        "rising": rising,
        "material": main_material(clean_text(rec.get("title") or (disc_row or {}).get("title") or "")),
        "hot": hot,
        "potential": potential,
        "fake": fake,
        "spark": (d.sales[max(0, idx - 89): idx + 1] if idx is not None else []),
        "spark_start": d.day(max(0, idx - 89)).isoformat() if idx is not None else None,
        "monthly": [[k, s] for k, s in months[-12:]],
        "recent_avg7": (surge or {}).get("recent_avg") if surge else _avg(d, idx, 7),
        "recent_avg28": _avg(d, idx, 28),
        "fetched_run": rec.get("fetched_run"),
        "source": rec.get("pool_reason"),
    }


def _avg(d: Daily | None, idx: int | None, days: int) -> float | None:
    if d is None or idx is None:
        return None
    values = d.window("sales", idx, days)
    return round(sum(values) / len(values), 2) if values else None


def analyze(state: dict, disc: dict | None, pool: list[str], cfg: dict, today: date,
            extra_rows: dict | None = None) -> dict:
    """extra_rows：发现基线以外的月度行（例如 BSR 上升榜），用于留评率等信号。"""
    stats = node_stats(disc, cfg)
    products = {**(extra_rows or {}), **((disc or {}).get("products") or {})}
    items = []
    for asin in pool:
        rec = state["asins"].get(asin)
        if not rec or not rec.get("series"):
            continue
        items.append(evaluate(asin, rec, products.get(asin), stats, cfg, today))
    return {"items": items, "node_stats": stats}


def _growth(item: dict) -> float:
    p = item.get("potential") or {}
    if p.get("growth") is not None:
        return p["growth"]
    return (p.get("recent_ratio") or 1) - 1


def sections(items: list[dict], top_n: int) -> dict[str, list[dict]]:
    surge = sorted((i for i in items if i["label"] == "surge"), key=lambda i: -i["surge"]["strength"])
    fake = sorted((i for i in items if i["label"] == "fake"),
                  key=lambda i: (-i["fake"]["score"], -(i.get("recent_avg28") or 0)))
    potential = sorted((i for i in items if i["label"] == "potential"), key=lambda i: -_growth(i))
    hot = sorted((i for i in items if i["label"] == "hot"), key=lambda i: -(i["hot"]["avg_monthly"] or 0))
    return {"surge": surge[:top_n], "fake": fake[:top_n], "potential": potential[:top_n], "hot": hot[:top_n],
            "counts": {"surge": len(surge), "fake": len(fake), "potential": len(potential), "hot": len(hot),
                       "rising": sum(1 for i in items if i.get("rising") and i["label"] not in UNLISTED),
                       "low": sum(1 for i in items if i["label"] == "low"), "total": len(items)}}


def focus_items(items: list[dict]) -> list[dict]:
    """外观分析的样本：突然爆火 + 潜力 + 上升中（都排除异常信号和评分偏低），按势头排序。
    只看“当期”数据，每期报告独立成立，不依赖往期积累。"""
    def strength(i: dict) -> float:
        if i.get("surge"):
            return 100 + i["surge"]["strength"]
        if i.get("potential"):
            return 50 + ((i["potential"] or {}).get("recent_ratio") or 1)
        return (i.get("rising") or {}).get("ratio") or 0

    picked = [i for i in items if i["label"] not in UNLISTED
              and (i["label"] in ("surge", "potential") or i.get("rising"))]
    return sorted(picked, key=lambda i: -strength(i))


def trait_rows(items: list[dict], disc: dict | None, state: dict) -> tuple[list[dict], list[dict]]:
    """返回 (基线行, 关注行)：关注 = 突然爆火 + 潜力 + 上升中。"""
    products = (disc or {}).get("products") or {}
    baseline = list(products.values())
    focus = []
    for item in focus_items(items):
        row = products.get(item["asin"])
        if row is None:
            rec = state["asins"].get(item["asin"]) or {}
            row = {"asin": item["asin"], "title": item["title"], "node": item["node"],
                   "node_cn": item["node_cn"], "node_name": item["node_name"], "price": item["price"],
                   "rating": item["rating"], "ratings": item["ratings"], "variations": rec.get("variations"),
                   "fulfillment": rec.get("fulfillment"), "seller_nation": rec.get("seller_nation"),
                   "available": rec.get("available")}
        focus.append(row)
    return baseline, focus


def compute_traits(items: list[dict], disc: dict | None, state: dict, today: date) -> dict:
    baseline, focus = trait_rows(items, disc, state)
    return traits.compute(baseline, focus, today)

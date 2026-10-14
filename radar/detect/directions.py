"""开发方向：增长商品最多的几个子类目，各自写出“做什么品类、用什么主材质 / 风格 / 造型 / 工艺 / 颜色 / 功能、卖什么价”。

每一项都只来自该子类目的增长商品，可以逐个核对。按品牌计数：同一品牌的多条链接只算一次
（实测：某期“梳妆台”4 个增长商品里 3 个是同一品牌，“白色 4/4、LED 4/4”其实是一家的产品线）。
  入选：增长商品 ≥ min_items 个、来自 ≥ min_brands 个品牌的子类目，按品牌数、增长商品个数、其中突然爆火的个数、
        势头最强的那个排在多前排序，取前 max 个；没有任何共同主材质或特征（画不出画像）的跳过，由下一个补上；
  特征：该子类目里至少 feature_min_share（且不少于 2 个）品牌的增长商品具备的（标题词库或主图识别，任一有就算），
        每个维度最多 per_dim 个，按具备的品牌数排序；
  主材质：同“外观与工艺依据”的口径，每个商品一类，入选条件同上；
  价格：该子类目增长商品的最低价 ~ 最高价，3 个以上再给中位数；
  参考：该子类目势头最强、没有任何异常信号（异常分 0）的两个增长商品，两个不同品牌。
特征同时在全部增长商品里“比对照组更多”的，标 trend（报告里显示 ↑）。
"""
from __future__ import annotations

import math
from collections import Counter

from ..analyze import display_name
from . import design
from .design import brand_key

DIMENSIONS = ("风格", "造型", "工艺", "颜色", "功能卖点")
VISION_DIMENSIONS = ("风格", "造型", "工艺", "颜色")  # 主图识别没有功能卖点
VAGUE_MATERIALS = ("未注明", "木质（未注明）", "其他")


def _features(item: dict, state: dict) -> dict[str, set[str]]:
    feats = design.extract(item.get("title", ""))
    tags = (((state.get("asins") or {}).get(item["asin"]) or {}).get("vision") or {}).get("tags") or {}
    return {dim: feats.get(dim, set()) | (set(tags.get(dim) or []) if dim in VISION_DIMENSIONS else set())
            for dim in DIMENSIONS}


def _median(values: list[float]) -> float:
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def compute(focus: list[dict], state: dict, appearance: dict | None, cfg: dict) -> list[dict]:
    """focus：增长商品（analyze.focus_items 的顺序 = 势头），每个商品要已带 item["material"]。"""
    c = cfg["thresholds"]["directions"]
    min_items, need_share, per_dim = int(c["min_items"]), float(c["feature_min_share"]), int(c["per_dim"])
    min_brands = int(c.get("min_brands", 1))
    trend = {dim: {f["label"] for f in rows.get("more") or []} for dim, rows in (appearance or {}).items()}

    groups: dict[str, list[dict]] = {}
    for item in focus:
        node = item.get("node_cn") or item.get("node_name")
        if node:
            groups.setdefault(node, []).append(item)
    first = {node: n for n, node in enumerate(dict.fromkeys(i.get("node_cn") or i.get("node_name") for i in focus))}

    def n_brands(rows: list[dict]) -> int:
        return len({brand_key(i) for i in rows})

    ranked = sorted((g for g in groups.items() if len(g[1]) >= min_items and n_brands(g[1]) >= min_brands),
                    key=lambda g: (-n_brands(g[1]), -len(g[1]), -sum(1 for i in g[1] if i["label"] == "surge"),
                                   first[g[0]]))

    out = []
    for node, rows in ranked:
        if len(out) >= int(c["max"]):
            break
        n, nb = len(rows), n_brands(rows)
        need = max(2, math.ceil(need_share * nb - 1e-9))
        # 每个品牌：它的增长商品里任何一个有就算这个品牌有
        by_brand: dict[str, dict[str, set[str]]] = {}
        for item in rows:
            have = by_brand.setdefault(brand_key(item), {dim: set() for dim in (*DIMENSIONS, "主材质")})
            for dim, labels in _features(item, state).items():
                have[dim] |= labels
            have["主材质"].add(item.get("material") or "未注明")
        brands = list(by_brand.values())
        features = {}
        for dim in DIMENSIONS:
            counts = Counter(label for b in brands for label in b[dim])
            order = {label: k for k, label in enumerate(dict.fromkeys(label for b in brands for label in sorted(b[dim])))}
            picked = sorted((label for label, k in counts.items() if k >= need),
                            key=lambda label: (-counts[label], label not in trend.get(dim, set()), order[label]))
            features[dim] = [{"label": label, "count": counts[label], "trend": label in trend.get(dim, set())}
                             for label in picked[:per_dim]]
        mats = Counter(label for b in brands for label in b["主材质"])
        materials = [{"label": label, "count": k} for label, k in mats.most_common() if k >= need]
        if not any(features.values()) and not any(m["label"] not in VAGUE_MATERIALS for m in materials):
            continue  # 没有任何共同的主材质或特征：画不出产品画像，换下一个子类目
        prices = [float(i["price"]) for i in rows if i.get("price")]
        refs: list[dict] = []
        for item in rows:  # 势头顺序；有异常信号的不当参考，两个参考来自不同品牌
            if len(refs) < 2 and not (item.get("fake") or {}).get("score") \
                    and brand_key(item) not in {brand_key(r) for r in refs}:
                refs.append(item)
        out.append({
            "node": node, "n": n, "n_brands": nb, "need": need,
            "n_surge": sum(1 for i in rows if i["label"] == "surge"),
            "materials": materials,
            "features": features,
            "price": {"min": min(prices), "max": max(prices),
                      "median": _median(prices) if len(prices) >= 3 else None} if prices else None,
            "refs": [{"asin": i["asin"], "brand": i.get("brand") or "", "name": display_name(i.get("brand"), i["title"]),
                      "price": i.get("price"), "look": i.get("look") or ""} for i in refs],
        })
    return out


def price_text(price: dict | None) -> str:
    if not price:
        return ""
    lo, hi = f"${price['min']:,.0f}", f"${price['max']:,.0f}"
    return lo if lo == hi else f"{lo}–{hi}"


def headline(d: dict) -> str:
    """一句话产品画像：品类：主材质，风格，造型，工艺，颜色，功能，价格。只用入选的特征。"""
    parts = [m["label"] for m in d["materials"][:1] if m["label"] not in VAGUE_MATERIALS]
    parts += ["、".join(f["label"] for f in d["features"][dim]) for dim in DIMENSIONS if d["features"][dim]]
    if d.get("price"):
        parts.append(price_text(d["price"]))
    return f"{d['node']}：{'，'.join(parts)}" if parts else d["node"]

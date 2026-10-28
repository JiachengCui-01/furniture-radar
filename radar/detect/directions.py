"""开发方向：增长商品带来的销售额增量最大的几个子类目，各写一个产品画像——做什么品类、
哪些外观 / 功能是增长商品比同子类目头部商品多的、哪些是这个品类本来就都有的、卖什么价、
包装多大、FBA 配送费多少。每一项都只来自该子类目，可以逐个核对。

  入选：增长商品 ≥ min_items 个、来自 ≥ min_brands 个品牌的子类目，按增量销售额从大到小
        （同额时看品牌数、增长商品个数、突然爆火个数、势头最强的那个排在多前）全部列出（max > 0 时取前 max 个）；
        没有任何特征也没有明确主材质（画不出画像）的跳过；本期要点和钉钉只写前 summary_max 个
        （实测：10-23 够格的 12 个、前 5 个只占增量的 70%；11-01 彩排 24 个、前 5 个只占 53%）；
  月销售额 = 该子类目每个增长商品的 近 28 天日均 × 当前售价 × 30，加起来；
  增量销售额 = 每个增长商品的（近 28 天日均 − 之前 28 天在卖日子的日均）× 当前售价 × 30，加起来，
        之前 28 天没在卖的按 0 算（日均和“上升中”是同一个口径，大促日不算；销量是卖家精灵按 BSR 估算的）；
        近 28 天日均：卖家精灵会回头改最近几天的日销量，本期测到了修订幅度时用最近几天按它校正后的（recent_daily；
        2026-10-20 的数据按 ×0.85 校正后，前 5 个方向顺序不变、每个方向的增量少 7~16%）；
        老品换链接（rules.relisted）之前 28 天没在卖、或在卖不到 thresholds.short_base_days 天时增量按 0：
        它的销量是从老链接转过来的，不是新增的需求（以前只看“没在卖”：之前零星卖过两天 1 件的，现在的销量几乎全算成了增量，
        11-01 彩排里一个这样的梳妆台占了增量第一的方向的 27%）；
        老链接重新起量（上架超过 180 天、停了很久又开始卖）增量也按 0：多是断货很久后恢复、或销量从别的链接转过来，
        不是新增的需求（回放：有它的 7 个日期里 5 次改变了前 5 个方向，3 次靠它一个商品排到第一，占该子类目增量的 83~99%）；
        只按品牌数排序时，便宜、门槛低的品类会排前面（实测 2026-10-20：$35 的衣帽架 8 个品牌排第一，增量约 $26 万；
        $138 的梳妆台 4 个品牌进不了前 5，增量约 $57 万）；
  特征按品牌计数（同一品牌的多条链接只算一次，该品牌任一增长商品有就算），每个维度分两档，一个特征只进一档：
    比头部多 = 至少 2 个品牌有，且具备的品牌占比 比 同子类目头部商品里的占比 高 head_min_gap 以上、
              是它的 head_min_lift 倍以上（只看差距时，4 个品牌全有、头部 73% 有的“带抽屉”也会算进来）；
    基本款   = 至少 feature_min_share（且不少于 2 个）品牌有，但不比头部多（例如书架的“开放格”、衣帽架的“带挂钩”）；
  同子类目头部商品 = 月度名单里同一子类目、不在增长商品里（同一父体也不算）的商品，按月销量取前 head_max 个，
    按商品数算占比。两边特征的来源要一致：有主图识别的头部商品 ≥ head_min 个时，两边都用“标题 + 主图”
    （颜色只看主图），头部只算识别过的那些；否则两边都只看标题。头部商品不足 head_min 个时不分档，
    只列“共同特征”（同基本款的门槛，但没有和头部比过）；
  主材质：同“外观与工艺依据”的口径，每个商品一类，至少 feature_min_share（且 ≥2 个）品牌是这一类才列，不和头部比；
  价格：该子类目增长商品的最低价 ~ 最高价，3 个以上再给中位数；
  包装：该子类目增长商品的包装体积、包装重量、FBA 配送费的中位数（shipping.py，只算有数据的）；
  参考：该子类目势头最强、没有任何异常信号（异常分 0）的两个增长商品，两个不同品牌。
特征同时在全部增长商品里“比对照组更多”的，标 trend（报告里显示 ↑）。
"""
from __future__ import annotations

import math
from collections import Counter
from .. import shipping, vision
from ..analyze import display_name
from . import design
from .design import brand_key

DIMENSIONS = ("风格", "造型", "工艺", "颜色", "功能卖点")
VAGUE_MATERIALS = ("未注明", "木质（未注明）", "其他")
MONTH_DAYS = 30


def node_of(row: dict) -> str:
    return row.get("node_cn") or row.get("node_name") or ""


def recent_daily(item: dict) -> float:
    """近 28 天日均：本期测到了日数据修订时，用最近几天按修订幅度校正后的（analyze 的 item["revised_avg28"]）。"""
    value = item.get("revised_avg28")
    return value if value is not None else (item.get("recent_avg28") or 0)


def monthly_sales(item: dict) -> float:
    return recent_daily(item) * (item.get("price") or 0) * MONTH_DAYS


def increment(item: dict) -> float:
    if item.get("relist") and (item.get("prev_avg28") is None or item["relist"].get("base_short")):
        return 0.0  # 老品换链接：之前没在卖（或只零星卖了几天），现在的销量是从老链接转过来的
    if (item.get("surge") or {}).get("kind") == "老链接重新起量":
        return 0.0  # 停了很久又开始卖：不是新增的需求
    return (recent_daily(item) - (item.get("prev_avg28") or 0)) * (item.get("price") or 0) * MONTH_DAYS


def _features(item: dict, state: dict, use_vision: bool = True) -> dict[str, set[str]]:
    """标题词库 + 主图识别，任一有就算；颜色（design.IMAGE_FIRST_DIMENSIONS）有主图识别时只看主图。
    use_vision=False 时只看标题（和只能看标题的头部商品比的时候）。"""
    feats = design.extract(item.get("title", ""))
    seen = vision.of(state, item["asin"]) if use_vision else {}
    tags = seen.get("tags") or {}
    out = {}
    for dim in DIMENSIONS:
        title = set() if seen and dim in design.IMAGE_FIRST_DIMENSIONS else feats.get(dim, set())
        out[dim] = title | set(tags.get(dim) or [])
    return out


def _median(values: list[float]) -> float:
    values = sorted(values)
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def _n_brands(rows: list[dict]) -> int:
    return len({brand_key(i) for i in rows})


def ranked(focus: list[dict], cfg: dict) -> list[tuple[str, list[dict]]]:
    """够格的子类目（增长商品数、品牌数达标），按增量销售额排好。focus 是按势头排好的增长商品。"""
    c = cfg["thresholds"]["directions"]
    groups: dict[str, list[dict]] = {}
    for item in focus:
        if node_of(item):
            groups.setdefault(node_of(item), []).append(item)
    first = {node: n for n, node in enumerate(dict.fromkeys(node_of(i) for i in focus if node_of(i)))}
    ok = [g for g in groups.items()
          if len(g[1]) >= int(c["min_items"]) and _n_brands(g[1]) >= int(c.get("min_brands", 1))]
    return sorted(ok, key=lambda g: (-round(sum(increment(i) for i in g[1])), -_n_brands(g[1]), -len(g[1]),
                                     -sum(1 for i in g[1] if i["label"] == "surge"), first[g[0]]))


def heads(node: str, disc: dict | None, focus: list[dict], cfg: dict) -> list[dict]:
    """同子类目头部商品：月度名单里这个子类目、不在增长商品里（同一父体也不算）的，按月销量取前 head_max 个。"""
    c = cfg["thresholds"]["directions"]
    taken = {i["asin"] for i in focus}
    parents = {p for p in (((disc or {}).get("products") or {}).get(a, {}).get("parent") for a in taken) if p}
    rows = [r for r in ((disc or {}).get("products") or {}).values()
            if node_of(r) == node and r["asin"] not in taken and not (r.get("parent") and r["parent"] in parents)]
    rows.sort(key=lambda r: -(r.get("units") or 0))
    return rows[: int(c.get("head_max", 30))]


def ref_candidates(rows: list[dict], n: int = 2) -> list[dict]:
    """参考商品：按势头顺序，没有异常信号（异常分 0）、来自不同品牌的前 n 个。"""
    refs: list[dict] = []
    for item in rows:
        if len(refs) < n and not (item.get("fake") or {}).get("score") \
                and brand_key(item) not in {brand_key(r) for r in refs}:
            refs.append(item)
    return refs


def vision_targets(focus: list[dict], disc: dict | None, cfg: dict, spare: int = 2) -> list[dict]:
    """主图识别要看的：够格的子类目（max > 0 时只看前 max + spare 个，有的会因为画不出画像被跳过）的增长商品和头部商品。"""
    limit = int(cfg["thresholds"]["directions"].get("max") or 0)
    nodes = ranked(focus, cfg)
    out: list[dict] = []
    for node, rows in (nodes[: limit + spare] if limit else nodes):
        out += rows + heads(node, disc, focus, cfg)
    return out


def compute(focus: list[dict], state: dict, appearance: dict | None, cfg: dict, *,
            disc: dict | None = None) -> list[dict]:
    """focus：增长商品（analyze.focus_items 的顺序 = 势头），每个商品要已带 item["material"]。
    disc：月度名单（同子类目头部商品从这里取）。"""
    c = cfg["thresholds"]["directions"]
    limit = int(c.get("max") or 0)  # 0 = 够格的全部列出
    need_share, per_dim = float(c["feature_min_share"]), int(c["per_dim"])
    head_min, gap, lift = int(c.get("head_min", 10)), float(c.get("head_min_gap", 0.2)), float(c.get("head_min_lift", 1.5))
    trend = {dim: {f["label"] for f in rows.get("more") or []} for dim, rows in (appearance or {}).items()}

    out = []
    for node, rows in ranked(focus, cfg):
        if limit and len(out) >= limit:
            break
        n, nb = len(rows), _n_brands(rows)
        need = max(2, math.ceil(need_share * nb - 1e-9))
        hs = heads(node, disc, focus, cfg)
        tagged = [h for h in hs if vision.of(state, h["asin"])]
        if len(hs) < head_min:
            mode, sample = None, []
        elif len(tagged) >= head_min:
            mode, sample = "image", tagged
        else:
            mode, sample = "title", hs
        use_vision = mode != "title"

        # 每个品牌：它的增长商品里任何一个有就算这个品牌有
        by_brand: dict[str, dict[str, set[str]]] = {}
        for item in rows:
            have = by_brand.setdefault(brand_key(item), {dim: set() for dim in (*DIMENSIONS, "主材质")})
            for dim, labels in _features(item, state, use_vision).items():
                have[dim] |= labels
            have["主材质"].add(item.get("material") or "未注明")
        brands = list(by_brand.values())
        head_feats = [_features(h, state, use_vision) for h in sample]

        features = {}
        for dim in DIMENSIONS:
            counts = Counter(label for b in brands for label in b[dim])
            head_counts = Counter(label for f in head_feats for label in f[dim])
            order = {label: k for k, label in enumerate(dict.fromkeys(label for b in brands for label in sorted(b[dim])))}

            def row(label: str) -> dict:
                share = head_counts[label] / len(sample) if sample else None
                return {"label": label, "count": counts[label], "trend": label in trend.get(dim, set()),
                        "head_share": None if share is None else round(share, 3)}

            def is_more(label: str, k: int) -> bool:
                share, head = k / nb, head_counts[label] / len(sample)
                return k >= 2 and share - head >= gap - 1e-9 and share >= lift * head - 1e-9

            more = [] if mode is None else [label for label, k in counts.items() if is_more(label, k)]
            more.sort(key=lambda label: (-(counts[label] / nb - head_counts[label] / len(sample)), -counts[label],
                                         order[label]))
            base = [label for label, k in counts.items() if k >= need and label not in more]
            base.sort(key=lambda label: (-counts[label], label not in trend.get(dim, set()), order[label]))
            features[dim] = {"more": [row(label) for label in more[:per_dim]],
                             "base": [row(label) for label in base[:per_dim]]}
        mats = Counter(label for b in brands for label in b["主材质"])
        materials = [{"label": label, "count": k} for label, k in mats.most_common() if k >= need]
        if not any(f["more"] or f["base"] for f in features.values()) \
                and not any(m["label"] not in VAGUE_MATERIALS for m in materials):
            continue  # 没有任何共同的主材质或特征：画不出产品画像，换下一个子类目
        prices = [float(i["price"]) for i in rows if i.get("price")]
        refs = ref_candidates(rows)  # 势头顺序；有异常信号的不当参考，两个参考来自不同品牌
        out.append({
            "node": node, "n": n, "n_brands": nb, "need": need,
            "n_surge": sum(1 for i in rows if i["label"] == "surge"),
            "sales": {"monthly": round(sum(monthly_sales(i) for i in rows)),
                      "increment": round(sum(increment(i) for i in rows))},
            "materials": materials,
            "features": features,
            "compare": {"mode": mode, "n_head": len(sample), "n_head_all": len(hs)},
            "price": {"min": min(prices), "max": max(prices),
                      "median": _median(prices) if len(prices) >= 3 else None} if prices else None,
            "pkg": shipping.summarize(rows),
            "refs": [{"asin": i["asin"], "brand": i.get("brand") or "", "name": display_name(i.get("brand"), i["title"]),
                      "price": i.get("price"), "look": i.get("look") or "", "pkg": i.get("pkg")} for i in refs],
        })
    return out


def overview(items: list[dict], focus: list[dict], shown: list[dict], disc: dict | None, cfg: dict) -> list[dict]:
    """全部子类目一览：监控范围里的每个子类目（月度名单），加上追踪商品、增长商品自己所在的子类目。每行：
    追踪商品数（本期拉了日数据的）、增长商品数 / 品牌数 / 突然爆火数、增长商品的月销售额和增量（口径同画像）、
    品类大盘（追踪商品身上的 universe.node_tide，本期没翻全市场名单时没有），以及有没有画像——没有的写原因：
    没有增长商品 / 增长商品不到 min_items 个 / 只来自 1 个品牌 / 排在前 max 个之后 / 没有共同的主材质或特征。
    按增量从大到小（同额看品牌数、增长商品数、突然爆火数、追踪商品数）。shown = compute 的结果。"""
    c = cfg["thresholds"]["directions"]
    limit, min_items, min_brands = int(c.get("max") or 0), int(c["min_items"]), int(c.get("min_brands", 1))
    tracked: dict[str, list[dict]] = {}
    for item in items:
        if item.get("daily", True) and node_of(item):
            tracked.setdefault(node_of(item), []).append(item)
    growth: dict[str, list[dict]] = {}
    for item in focus:
        if node_of(item):
            growth.setdefault(node_of(item), []).append(item)
    names = [n.get("name_cn") or n.get("name") or "" for n in (disc or {}).get("nodes") or []]
    names = [n for n in dict.fromkeys(names + list(tracked) + list(growth)) if n]
    order = [node for node, _rows in ranked(focus, cfg)]
    pos = {d["node"]: order.index(d["node"]) for d in shown}
    last = max(pos.values(), default=-1)
    rows = []
    for name in names:
        g = growth.get(name, [])
        nb = _n_brands(g)
        if name in pos:
            status = None
        elif not g:
            status = "没有增长商品"
        elif len(g) < min_items:
            status = f"增长商品不到 {min_items} 个"
        elif nb < min_brands:
            status = f"增长商品只来自 {nb} 个品牌"
        elif limit and len(shown) >= limit and order.index(name) > last:
            status = f"增量排在前 {limit} 个之后"
        else:
            status = "没有共同的主材质或特征"
        tide = next((i["tide"] for i in tracked.get(name, []) if i.get("tide") is not None), None)
        rows.append({"node": name, "n_tracked": len(tracked.get(name, [])), "n": len(g), "n_brands": nb,
                     "n_surge": sum(1 for i in g if i["label"] == "surge"),
                     "monthly": round(sum(monthly_sales(i) for i in g)), "increment": round(sum(increment(i) for i in g)),
                     "tide": tide, "shown": status is None, "status": status})
    rows.sort(key=lambda r: (-r["increment"], -r["n_brands"], -r["n"], -r["n_surge"], -r["n_tracked"]))
    return rows


def price_text(price: dict | None) -> str:
    if not price:
        return ""
    lo, hi = f"${price['min']:,.0f}", f"${price['max']:,.0f}"
    return lo if lo == hi else f"{lo}–{hi}"


def money_text(value: float | None) -> str:
    """$1,405,807 → “$141 万”；不到 1 万的写整数美元。"""
    if value is None:
        return ""
    sign = "-" if value < 0 else ""
    value = abs(value)
    return f"{sign}${value / 10_000:,.0f} 万" if value >= 10_000 else f"{sign}${value:,.0f}"


def labels(d: dict, tier: str) -> list[str]:
    """一档特征的名字，按维度顺序。tier = "more"（比头部多）/ "base"（基本款或共同特征）。"""
    return [f["label"] for dim in DIMENSIONS for f in d["features"][dim][tier]]


def headline(d: dict) -> str:
    """一句话产品画像：品类：主材质，比头部多的特征（没有的话写基本款），价格。只用入选的特征。"""
    parts = [m["label"] for m in d["materials"][:1] if m["label"] not in VAGUE_MATERIALS]
    more, base = labels(d, "more"), labels(d, "base")
    if more:
        parts.append("、".join(more))
    elif base:
        parts.append(("基本款 " if (d.get("compare") or {}).get("mode") else "") + "、".join(base[:4]))
    if d.get("price"):
        parts.append(price_text(d["price"]))
    return f"{d['node']}：{'，'.join(parts)}" if parts else d["node"]

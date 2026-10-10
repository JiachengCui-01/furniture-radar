"""增长商品（突然爆火 + 潜力 + 上升中）的共性特点：与全部头部商品（发现阶段的约 1600 个 ASIN）对比，找出显著偏高的特征。

重点是外观与工艺（design.py 的设计词库），其次是子类目、价格段、上架时长等结构特征。

所有结论都是计算出来的“占比 + 提升度”，大模型只负责把它们写成文字，不允许编造数字。
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import date

from .. import clock
from . import design

STOPWORDS = set("""
a an the and or for with of to in on by from at as is are be this that it its your you our
set pcs piece pieces inch inches ft feet cm lbs lb x w d h l up into over under per new
""".split())


def title_tokens(title: str) -> set[str]:
    words = [w for w in re.findall(r"[a-z][a-z\-]{2,}", (title or "").lower()) if w not in STOPWORDS]
    grams = set(words)
    grams.update(f"{a} {b}" for a, b in zip(words, words[1:]))
    return grams


def _price_bands(rows: list[dict]) -> dict[str, tuple[float, float]]:
    by_node: dict[str, list[float]] = {}
    for r in rows:
        if r.get("price"):
            by_node.setdefault(r.get("node") or "", []).append(float(r["price"]))
    bands = {}
    for node, prices in by_node.items():
        prices.sort()
        if len(prices) >= 6:
            bands[node] = (prices[len(prices) // 3], prices[2 * len(prices) // 3])
    return bands


def _band(row: dict, bands: dict) -> str | None:
    cut = bands.get(row.get("node") or "")
    price = row.get("price")
    if not cut or not price:
        return None
    if price < cut[0]:
        return "子类目内低价段"
    if price > cut[1]:
        return "子类目内高价段"
    return "子类目内中价段"


def _variations(v) -> str | None:
    if v is None:
        return None
    if v <= 1:
        return "单体（无变体）"
    if v <= 5:
        return "2~5 个变体"
    if v <= 20:
        return "6~20 个变体"
    return "20 个以上变体"


def _age(available: str | None, today: date) -> str | None:
    days = clock.days_between(clock.parse_day(available), today)
    if days is None:
        return None
    if days < 90:
        return "上架不到 3 个月"
    if days < 180:
        return "上架 3~6 个月"
    if days < 365:
        return "上架 6~12 个月"
    return "上架 1 年以上"


MISSING = {"", "NA", "N/A", "-", "NULL", "NONE", "UNKNOWN"}


def _fulfillment(value: str | None) -> str | None:
    value = (value or "").strip().upper()
    if value in MISSING:
        return None
    if "AMZ" in value:
        return "亚马逊自营"
    return {"FBA": "FBA 发货", "FBM": "自发货（FBM）"}.get(value, value)


def _nation(value: str | None) -> str | None:
    value = (value or "").strip().upper()
    if value in MISSING:
        return None
    return {"CN": "中国卖家", "US": "美国卖家", "HK": "中国香港卖家"}.get(value, f"{value} 卖家" if value else None)


DIMENSIONS = {
    "子类目": lambda r, ctx: r.get("node_cn") or r.get("node_name"),
    "价格段": lambda r, ctx: _band(r, ctx["bands"]),
    "配送方式": lambda r, ctx: _fulfillment(r.get("fulfillment")),
    "卖家": lambda r, ctx: _nation(r.get("seller_nation")),
    "变体": lambda r, ctx: _variations(r.get("variations")),
    "上架时长": lambda r, ctx: _age(r.get("available"), ctx["today"]),
}


def _median(values: list) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    mid = len(values) // 2
    return values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2


def compute(baseline: list[dict], focus: list[dict], today: date, appearance: dict | None = None) -> dict:
    """appearance：config.yaml 的 thresholds.appearance（标题统计的门槛）。"""
    nf, nb = len(focus), len(baseline)
    result = {"n_focus": nf, "n_baseline": nb, "facts": [], "keywords": [], "numbers": {}, "design": {},
              "title_shares": {}}
    if nf < 3 or nb < 20:
        result["note"] = "本期增长商品太少（少于 3 个），暂不总结共性特点"
        return result

    a = appearance or {}
    result["design"] = design.compare(baseline, focus, per_dim=int(a.get("per_dim", 5)),
                                      min_count=a.get("min_count"), min_share=float(a.get("title_min_share", 0.15)),
                                      min_lift=float(a.get("title_min_lift", 1.3)))
    result["title_shares"] = design.shares(baseline, focus)
    ctx = {"bands": _price_bands(baseline), "today": today}
    for dim, fn in DIMENSIONS.items():
        cf = Counter(v for v in (fn(r, ctx) for r in focus) if v)
        cb = Counter(v for v in (fn(r, ctx) for r in baseline) if v)
        for value, count in cf.items():
            share_f = count / nf
            share_b = cb.get(value, 0) / nb
            lift = ((count + 0.5) / (nf + 1)) / ((cb.get(value, 0) + 0.5) / (nb + 1))
            if count >= 2 and share_f >= 0.2 and lift >= 1.5:
                result["facts"].append({"dimension": dim, "value": value, "count": count,
                                        "share": round(share_f, 3), "baseline_share": round(share_b, 3),
                                        "lift": round(lift, 2)})
    result["facts"].sort(key=lambda f: -(f["lift"] * f["share"]))
    result["facts"] = result["facts"][:10]

    df_focus: Counter = Counter()
    df_base: Counter = Counter()
    for r in focus:
        df_focus.update(title_tokens(r.get("title", "")))
    for r in baseline:
        df_base.update(title_tokens(r.get("title", "")))
    min_support = 3 if nf >= 20 else max(2, math.ceil(0.15 * nf))
    keywords = []
    for gram, count in df_focus.items():
        if count < min_support:
            continue
        lift = ((count + 1) / (nf + 2)) / ((df_base.get(gram, 0) + 1) / (nb + 2))
        if lift >= 2:
            keywords.append({"term": gram, "count": count, "share": round(count / nf, 3),
                             "baseline_share": round(df_base.get(gram, 0) / nb, 3), "lift": round(lift, 2)})
    # 二元词组优先；被二元词组完全覆盖的单词去掉
    keywords.sort(key=lambda k: (-(k["lift"] * k["count"]), -len(k["term"])))
    picked: list[dict] = []
    for k in keywords:
        if " " not in k["term"] and any(k["term"] in p["term"].split() and p["count"] >= k["count"] for p in picked):
            continue
        picked.append(k)
    result["keywords"] = picked[:15]

    for name, key in (("价格中位数", "price"), ("评分中位数", "rating"), ("评论数中位数", "ratings")):
        result["numbers"][name] = {"focus": _median([r.get(key) for r in focus]),
                                   "baseline": _median([r.get(key) for r in baseline])}
    return result


def merge_appearance(tr: dict, vision: dict | None, cfg: dict) -> dict[str, dict[str, list[dict]]]:
    """外观特征清单（风格 / 造型 / 工艺 / 颜色 / 功能卖点；材质只看主材质）。两种依据按特征名合并，每个特征只出现一次。
    依据（都要至少 min_count 个增长商品有，才算数）：
      title：标题统计，增长商品 vs 全部头部商品
      image：主图识别，增长商品里势头最强的若干个 vs 持续热销对照组
    更多：标题里占比 ≥ title_min_share 且是全部头部商品的 title_min_lift 倍以上，或主图里比对照组多 image_min_gap 以上；
          任何一种算数的依据说“更少”，就是依据冲突，不列。按“比对照组多出的占比”排序，每个维度最多 per_dim 个。
    常见：不在“更多”里、增长商品中占比 ≥ common_share 的（有主图识别看主图，没有看标题），不代表比对照组多。"""
    a = cfg["thresholds"]["appearance"]
    min_count, gap_min = int(a["min_count"]), float(a["image_min_gap"])
    vdims = (vision or {}).get("dimensions") or {}
    qualified_title = {dim: {r["label"] for r in rows} for dim, rows in (tr.get("design") or {}).items()}
    out: dict[str, dict[str, list[dict]]] = {}
    for dim in (*design.APPEARANCE_DIMENSIONS, "功能卖点"):
        titles = {label: r for label, r in ((tr.get("title_shares") or {}).get(dim) or {}).items()
                  if r["count"] >= min_count}
        images = {r["label"]: r for r in vdims.get(dim, []) if r["focus"] >= min_count}
        more, common = [], []
        for label in dict.fromkeys([*titles, *images]):
            t, i = titles.get(label), images.get(label)
            t_more = label in qualified_title.get(dim, set())
            i_gap = None if not i or i.get("reference_share") is None else i["focus_share"] - i["reference_share"]
            i_more = i_gap is not None and i_gap >= gap_min - 1e-9
            t_less = t is not None and t["share"] < t["baseline_share"]
            i_less = i_gap is not None and i_gap < 0
            row = {"label": label, "title": t, "image": i}
            if (t_more or i_more) and not (t_less or i_less):
                more.append(row)
            elif (i or t) and (i["focus_share"] if i else t["share"]) >= float(a["common_share"]) - 1e-9:
                common.append(row)

        def gap(f: dict) -> float:
            t, i = f.get("title"), f.get("image")
            x = t["share"] - t["baseline_share"] if t else 0.0
            y = i["focus_share"] - (i["reference_share"] or 0) if i else 0.0
            return max(x, y) + (0.05 if t and i else 0.0)  # 两种依据都支持的排前面

        more.sort(key=lambda f: -gap(f))
        common.sort(key=lambda f: -((f["image"] or {}).get("focus_share") or (f["title"] or {}).get("share") or 0))
        if more or common:
            out[dim] = {"more": more[: int(a["per_dim"])], "common": common[:3]}
    return out

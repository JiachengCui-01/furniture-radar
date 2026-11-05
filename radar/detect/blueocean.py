"""蓝海产品：半年内上架的新链接，评论只有头部的零头，却已经在子类目里卖进了前几十名——
这个子类目的买家不看评论积累、老品牌也没把位置占住，新卖家进得来。

蓝海产品 = 下面五条都满足（thresholds.blue_ocean）：
  ① 新品：上架 ≤ max_age_days 天，且不是老品换链接（rules.relisted，要日数据）；
  ② 卖进头部：近 30 天销售额排进所在子类目的前 top_rank 名（本期全市场名单，同一父体算一个；小件和不在范围内的不算）；
  ③ 评论少：评论数 ≤ 同子类目销售额前 head_n 名评论数中位数的 review_share（例：电视架头部中位 142 条 → ≤28 条）；
  ④ 不是老品牌带起来的：品牌已知，而且这个品牌在本期全市场名单里（全部子类目）没有上架超过 max_age_days 天的商品
     （老卖家的新链接靠品牌、店铺流量和经验起量，证明不了新卖家进得来。只看同一个子类目时，11-01 的 31 个里
     12 个是别的子类目的老品牌，例：FEZIBO 的升降桌在“计算机工作站”是新品，在“家庭办公桌”有老品）；
  ⑤ 日数据核对过：本期拉到了它的日数据、不陈旧，没有回落，名单和日数据对得上（analyze.data_tags），
     不是异常信号、评分达标。
①~④ 只用本期翻到的全市场名单（0 次调用），挑出来的候选进追踪池拉日数据（pool.ocean_max），⑤ 在判定时核对。
名单不是本期翻的（日期 ≠ today）时不判：不拿旧名单说现在的市场。

回测（本地数据，没有花调用）：
  · 新品进得了头部这件事在同一个子类目里会重复出现：2026-05~09 每个月的月度名单，某个月有半年内上架的商品卖进前 20 的子类目，
    之后两个月里另一个新品也卖进前 20 的占 83%（90 个子类目·月），没有的只有 52%（50 个）；
  · 蓝海产品下个月不比别的新品更容易掉：9 月名单按 ①~④ 挑出的 69 个，10 月销售额保住九月八成以上的 39%（其余进了前 50 的新品 32%），
    保住一半以上 64%（69%）——它说的是“这个子类目新卖家进得来”，不是“这个商品下个月还会更好”；
  · 只按名单会把数据错误当成蓝海：9 月名单 342 件的储物柜，日数据当月只有 4 件，所以要日数据核对（⑤）。
"""
from __future__ import annotations

from datetime import date
from statistics import median

from .. import clock
from ..discovery import keep_product
from ..universe import family_key
from .design import known_brand_key
from .rules import low_rating

DATA_MISMATCH = "名单和日数据对不上"


def settings(cfg: dict) -> dict:
    c = cfg["thresholds"].get("blue_ocean") or {}
    return {"enabled": bool(c.get("enabled", True)), "max_age_days": int(c.get("max_age_days", 180)),
            "top_rank": int(c.get("top_rank", 50)), "head_n": int(c.get("head_n", 20)),
            "review_share": float(c.get("review_share", 0.2))}


def _age(row: dict, today: date) -> int | None:
    return clock.days_between(clock.parse_day(row.get("available")), today)


def candidates(state: dict, today: date, cfg: dict) -> dict[str, dict]:
    """{父体: 名单上的证据}：本期全市场名单里满足 ①~④ 的（日数据还没核对）。
    证据 = {asin, node, rank（子类目里按近 30 天销售额第几名）, n（子类目里够门槛的商品数）, revenue, ratings,
    head_reviews（前 head_n 名评论数中位数）, limit（评论数上限）, age}。"""
    c = settings(cfg)
    uni = state.get("universe") or {}
    if not c["enabled"] or uni.get("date") != today.isoformat():
        return {}
    th = cfg["thresholds"]
    groups: dict[str, dict[str, dict]] = {}
    for asin, row in (uni.get("rows") or {}).items():
        if not row.get("node") or not keep_product(row, cfg):
            continue
        fam = family_key(row, asin)
        node = groups.setdefault(row["node"], {})
        if fam not in node or float(row.get("revenue") or 0) > float(node[fam].get("revenue") or 0):
            node[fam] = row
    old = {known_brand_key(r) for fams in groups.values() for r in fams.values()
           if (a := _age(r, today)) is not None and a > c["max_age_days"] and known_brand_key(r)}
    out: dict[str, dict] = {}
    for node, fams in groups.items():
        ranked = sorted(fams.items(), key=lambda kv: -float(kv[1].get("revenue") or 0))
        heads = [float(r.get("ratings") or 0) for _f, r in ranked[: c["head_n"]]]
        head = median(heads) if heads else 0.0
        limit = head * c["review_share"]
        for rank, (fam, r) in enumerate(ranked[: c["top_rank"]], start=1):
            age = _age(r, today)
            brand = known_brand_key(r)
            if age is None or not 0 <= age <= c["max_age_days"] or r.get("ratings") is None:
                continue
            if float(r["ratings"]) > limit + 1e-9 or brand is None or brand in old:
                continue
            if low_rating(r.get("rating"), r.get("ratings"), th):
                continue
            out[fam] = {"asin": r["asin"], "node": node, "rank": rank, "n": len(ranked),
                        "revenue": float(r.get("revenue") or 0), "ratings": float(r["ratings"]),
                        "head_reviews": head, "limit": round(limit, 1), "age": age}
    return out


def miss_reason(item: dict, cfg: dict) -> str | None:
    """⑤ 日数据核对：不通过的原因，通过返回 None。"""
    if not item.get("daily", True) or not item.get("spark"):
        return "没有日数据"
    if item.get("stale"):
        return "数据陈旧"
    if item["label"] in ("fake", "low"):
        return "异常信号" if item["label"] == "fake" else "评分不达标"
    if item.get("relist"):
        return "老品换链接"
    if item.get("age_days") is None or item["age_days"] > settings(cfg)["max_age_days"]:
        return "上架天数不符"
    if item.get("fading"):
        return "最近回落"
    if any(t.startswith(DATA_MISMATCH) for t in item.get("tags") or []):
        return DATA_MISMATCH
    return None


def mark(items: list[dict], cands: dict[str, dict], cfg: dict) -> dict:
    """候选里拉到日数据、核对通过的商品写上 item["ocean"]（证据）。返回 {"candidates": 名单上符合 ①~④ 的个数,
    "tracked": 其中拉到日数据的, "listed": 核对通过的, "missed": {原因: 个数}}。"""
    by_asin = {ev["asin"]: ev for ev in cands.values()}
    seen: set[str] = set()
    missed: dict[str, int] = {}
    listed = 0
    for item in items:
        ev = by_asin.get(item["asin"]) or cands.get(item.get("parent") or item["asin"])
        if ev is None or not item.get("daily", True) or ev["asin"] in seen:
            continue
        seen.add(ev["asin"])
        why = miss_reason(item, cfg)
        if why:
            missed[why] = missed.get(why, 0) + 1
            continue
        item["ocean"] = dict(ev)
        listed += 1
    tracked = len(seen)
    missed["没有日数据"] = missed.get("没有日数据", 0) + len(cands) - tracked
    return {"candidates": len(cands), "tracked": tracked, "listed": listed,
            "missed": {k: v for k, v in missed.items() if v}}


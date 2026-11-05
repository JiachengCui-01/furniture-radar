"""蓝海产品：为某个特定小圈子人群 / 用途专门设计的家具——缝纫机桌、卧床的人用的床边桌、黑胶唱片机柜、蒙氏地床、
带枪支暗格的床头柜、店铺前台……和普通常规款不同，针对性强，买的人圈子小、做的卖家也少。

蓝海产品 = 下面四条都满足（thresholds.blue_ocean）：
  ① 针对特定人群 / 用途：标题里写明了 niches 词表里的某一类（每类一个正则，见 config.yaml；只认写明专门用途的说法，
     例：“record player stand”算黑胶唱片，“drum table”是鼓形造型不算；“for classroom”这类适用场景的罗列不算）；
  ② 有需求：在本期全市场名单里（近 30 天 ≥300 件或 ≥4 万美元），在监控范围内、不是小件；
  ③ 圈子小：这一类在本期全市场名单里够门槛的商品（同一父体算一个）不超过 max_products 个——多了就是大众品类
     （11-01：写着 gaming 的 114 个、standing desk 的 194 个，即使加进词表也不算）；
  ④ 评分达标（和各板块一样）；本期拉到了日数据的，还要不是异常信号、名单和日数据对得上（analyze.data_tags）。
只用本期翻到的全市场名单（0 次调用）；名单不是本期翻的（日期 ≠ today）时不判，不拿旧名单说现在的市场。
符合的商品进追踪池拉日数据（pool.ocean_max 个，按近 30 天销售额），卡片上才有走势图；没拉到的照样列出，写明只有名单数据。
"""
from __future__ import annotations

import re
from datetime import date
from statistics import median

from .. import clock
from ..discovery import clean_text, keep_product
from ..universe import family_key
from .design import known_brand_key
from .rules import low_rating

DATA_MISMATCH = "名单和日数据对不上"
LIST_ONLY = "只有名单数据（本期没拉到日数据：没有走势图，没核对异常信号）"


def settings(cfg: dict) -> dict:
    c = cfg["thresholds"].get("blue_ocean") or {}
    niches = [(str(n["name"]), re.compile(str(n["regex"]), re.I)) for n in c.get("niches") or [] if n.get("name") and n.get("regex")]
    return {"enabled": bool(c.get("enabled", True)) and bool(niches), "max_products": int(c.get("max_products", 50)),
            "niches": niches}


def niche_of(title: str | None, niches: list[tuple[str, re.Pattern]]) -> str | None:
    """标题属于词表里的哪一类（按词表顺序取第一类），都不是返回 None。"""
    for name, rx in niches:
        if rx.search(title or ""):
            return name
    return None


def scan(state: dict, today: date, cfg: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """返回 (候选 {父体: 证据}, 各类的市场 {类名: {...}})。
    证据 = {asin, node, niche, revenue, n（这一类全市场几个）}；
    各类 = {n, revenue（这一类近 30 天合计）, brands（已知品牌数）, head_reviews（这一类销售额前 10 名评论数中位数）,
            small（圈子小，③）, nodes（在哪些子类目）}。评分不达标的不进候选，但算进这一类的个数（它们也是竞品）。"""
    c = settings(cfg)
    uni = state.get("universe") or {}
    if not c["enabled"] or uni.get("date") != today.isoformat():
        return {}, {}
    fams: dict[str, tuple[str, dict]] = {}
    for asin, row in (uni.get("rows") or {}).items():
        if not row.get("node") or not keep_product(row, cfg):
            continue
        niche = niche_of(row.get("title"), c["niches"])
        if niche is None:
            continue
        fam = family_key(row, asin)
        if fam not in fams or float(row.get("revenue") or 0) > float(fams[fam][1].get("revenue") or 0):
            fams[fam] = (niche, row)
    groups: dict[str, list[tuple[str, dict]]] = {}
    for fam, (niche, row) in fams.items():
        groups.setdefault(niche, []).append((fam, row))
    stats, out = {}, {}
    for niche, members in groups.items():
        members.sort(key=lambda m: -float(m[1].get("revenue") or 0))
        heads = [float(r.get("ratings") or 0) for _f, r in members[:10]]
        small = len(members) <= c["max_products"]
        stats[niche] = {"n": len(members), "revenue": sum(float(r.get("revenue") or 0) for _f, r in members),
                        "brands": len({k for _f, r in members if (k := known_brand_key(r))}),
                        "head_reviews": median(heads) if heads else 0.0, "small": small,
                        "nodes": sorted({r.get("node_cn") or r.get("node_name") or "" for _f, r in members} - {""})}
        if not small:
            continue
        for fam, r in members:
            if low_rating(r.get("rating"), r.get("ratings"), cfg["thresholds"]):
                continue
            out[fam] = {"asin": r["asin"], "node": r["node"], "niche": niche, "revenue": float(r.get("revenue") or 0),
                        "n": len(members), "niche_revenue": stats[niche]["revenue"]}
    return out, stats


def candidates(state: dict, today: date, cfg: dict) -> dict[str, dict]:
    return scan(state, today, cfg)[0]


def miss_reason(item: dict) -> str | None:
    """④ 拉到了日数据的再核对一遍：不通过的原因，通过返回 None。"""
    if item["label"] == "fake":
        return "异常信号"
    if item["label"] == "low":
        return "评分不达标"
    if any(t.startswith(DATA_MISMATCH) for t in item.get("tags") or []):
        return DATA_MISMATCH
    return None


def mark(items: list[dict], cands: dict[str, dict]) -> tuple[dict, set[str]]:
    """本期判过的候选（拉到了日数据的，或月度名单补判的持续热销）核对 ④，通过的写上 item["ocean"]。
    返回 (统计 {candidates, daily: 有日数据的, monthly: 月度名单补判的, missed: {原因: 个数}}, 判过的候选 ASIN)。"""
    by_asin = {ev["asin"]: ev for ev in cands.values()}
    seen: set[str] = set()
    missed: dict[str, int] = {}
    daily = monthly = 0
    for item in items:
        ev = by_asin.get(item["asin"]) or cands.get(item.get("parent") or item["asin"])
        has_daily = item.get("daily", True) and bool(item.get("spark"))
        if ev is None or not (has_daily or item.get("source") == "monthly") or ev["asin"] in seen:
            continue
        seen.add(ev["asin"])
        daily, monthly = daily + has_daily, monthly + (not has_daily)
        why = miss_reason(item)
        if why:
            missed[why] = missed.get(why, 0) + 1
            continue
        item["ocean"] = dict(ev)
    return {"candidates": len(cands), "daily": daily, "monthly": monthly, "missed": missed}, seen


def list_items(state: dict, cands: dict[str, dict], tracked: set[str], today: date) -> list[dict]:
    """没拉到日数据的候选：按全市场名单那一行做成卡片（不进任何板块、不进“全部追踪商品”，只在蓝海产品里列出）。"""
    rows = (state.get("universe") or {}).get("rows") or {}
    out = []
    for ev in cands.values():
        r = rows.get(ev["asin"])
        if r is None or ev["asin"] in tracked:
            continue
        title = clean_text(r.get("title") or ev["asin"])
        out.append({
            "asin": ev["asin"], "title": title, "brand": clean_text(r.get("brand") or ""), "image": r.get("image") or "",
            "node": r.get("node"), "node_name": r.get("node_name") or "", "node_cn": r.get("node_cn") or "",
            "parent": r.get("parent"), "price": r.get("price"), "bsr": r.get("bsr"), "rating": r.get("rating"),
            "ratings": r.get("ratings"), "variations": r.get("variations"),
            "age_days": clock.days_between(clock.parse_day(r.get("available")), today), "as_of": None, "stale": False,
            "label": "watch", "label_cn": "观察", "tags": [LIST_ONLY], "surge": None, "restock": None, "rising": None,
            "material": None, "hot": None, "potential": None, "fake": {"score": 0, "level": None, "signals": [], "tags": []},
            "relist": None, "spark": [], "spark_start": None, "monthly": [], "recent_avg7": None,
            "recent_avg28": None, "prev_avg28": None, "pkg": r.get("pkg"),
            "fetched_run": None, "source": "universe", "daily": False, "list_only": True, "ocean": dict(ev),
        })
    return out

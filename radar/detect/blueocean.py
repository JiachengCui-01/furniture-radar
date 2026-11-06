"""蓝海产品：和某个小众兴趣爱好直接相关的家具 / 收纳——桌游桌、拼图桌、爬宠箱和鱼缸柜、唱片机柜、手办 / 乐高展示柜、缝纫手工桌、
绘画桌、音乐工作台、街机柜、台球桌、枪柜、钓竿架、雪茄柜……（2026-11-06 用户重新界定：人群 / 场景 / 商用的——蒙氏地床、
卧床护理的床边桌、店铺前台、员工更衣柜、商用备餐台——不算）。玩这个爱好的人才买，圈子小、做的卖家也少。

蓝海产品 = 下面六条都满足（thresholds.blue_ocean）：
  ① 兴趣爱好专用：标题里写明了 niches 词表里的某一类（每类一个正则，见 config.yaml；只认写明专门用途的说法，
     例：“record player stand”算黑胶影音收藏，“drum table”是鼓形造型不算；“for classroom”这类适用场景的罗列不算）；
  ② 有需求：在本期全市场名单里（近 30 天 ≥300 件或 ≥4 万美元），在监控范围内、不是小件；
  ③ 圈子小：这一类在本期全市场名单里够门槛的商品（同一父体算一个）不超过 max_products 个——多了就是大众品类
     （11-01：写着 gaming 的 114 个、standing desk 的 194 个，即使加进词表也不算）；
  ④ 评分达标（和各板块一样）；本期拉到了日数据的，还要不是异常信号、名单和日数据对得上（analyze.data_tags）；
  ⑤ 不是太小、结构太简单的（too_simple；圈子再小，这种品也没有开发价值）：
     太小 = 包装不到 tiny_weight 磅，而且没有升降、翻转、折叠、翻板、暗格这类机构（MECHANISM）；
     结构太简单 = 书架 / 置物架类（标题是书架、玩具架、展示架这类，或主图描述是开放格、书架、绘本架），包装不到 simple_weight 磅、
     不是超大件（三边在 59 × 33 × 33 英寸以内），而且没有柜门、也没有上面这些机构（书架下面带个抽屉也还是书架）。
     没有包装数据的判断不了，不算太小；
  ⑥ 近期新上架、月售够（new_enough）：上架 ≤ max_age_days 天（不设时和潜力产品同一个数：rules.new_days），近 30 天销量 ≥ min_units 件。
     只卡候选，不卡 ③ 的个数（老款也是这一类的竞品）。上架日期不知道的判断不了，不算。
     候选只来自全市场名单，所以实际还要近 30 天 ≥300 件或 ≥4 万美元：月售 50~300 件、销售额不到 4 万美元的新品这里看不到。
只用本期翻到的全市场名单（0 次调用）；名单不是本期翻的（日期 ≠ today）时不判，不拿旧名单说现在的市场。
符合的商品进追踪池拉日数据（pool.ocean_max 个，按近 30 天销售额），卡片上才有走势图；没拉到的照样列出，写明只有名单数据。
"""
from __future__ import annotations

import re
from datetime import date
from statistics import median

from .. import clock, vision
from ..discovery import clean_text, keep_product
from ..universe import family_key
from .design import known_brand_key
from .rules import low_rating, new_days

DATA_MISMATCH = "名单和日数据对不上"
# 活动机构：有它的小件不算“结构太简单”（缝纫桌靠折叠，唱片柜的翻板，拼图桌的翻盖，枪支暗格）
MECHANISM = re.compile(r"\b(?:height[- ]adjustable|adjustable[- ]height|adjustable(?! shel| storage)|pneumatic|standing desk|sit[- ]stand|"
                       r"tilt(?:able|ing)?|fold(?:ing|able|s)?|fold[- ](?:out|away|down)|flip[- ]?(?:down|up|top)|lift[- ]?top|"
                       r"murphy|wall bed|convertible|extendable|gun drawer|hidden (?:compartment|storage)|secret compartment|swivel|rotating)\b",
                       re.I)
MECHANISM_LOOK = re.compile(r"升降|翻转|翻板|翻门|翻盖|折叠|可调节|暗格|隐藏储物|旋转|伸缩|拉出式台面")
DOORS = re.compile(r"\b(?:doors?|\d+ (?:\w+ )?cabinets|with (?:\w+ )?cabinets)\b", re.I)
DOORS_LOOK = re.compile(r"柜门|门板|封闭")
SHELVING = re.compile(r"\b(?:book ?(?:shel(?:f|ves)|cases?|racks?)|toy (?:shel(?:f|ves)|storage|organizer)|storage organizer|"
                      r"montessori shel(?:f|ves)|cube storage|display (?:shel(?:f|ves)|rack)|record (?:storage|shelf|rack))\b",
                      re.I)
SHELVING_LOOK = re.compile(r"开放格|开放式|书架|绘本架|置物架|展示架|层架|收纳架")
XL_DIMS = (59, 33, 33)  # 亚马逊超大件以下（大号大件）的三边上限，英寸
LIST_ONLY = "只有名单数据（本期没拉到日数据：没有走势图，没核对异常信号）"


def settings(cfg: dict) -> dict:
    c = cfg["thresholds"].get("blue_ocean") or {}
    niches = [(str(n["name"]), re.compile(str(n["regex"]), re.I)) for n in c.get("niches") or [] if n.get("name") and n.get("regex")]
    return {"enabled": bool(c.get("enabled", True)) and bool(niches), "max_products": int(c.get("max_products", 50)),
            "niches": niches, "tiny_weight": float(c.get("tiny_weight", 30)), "simple_weight": float(c.get("simple_weight", 50)),
            "max_age_days": int(c.get("max_age_days") or new_days(cfg["thresholds"])), "min_units": float(c.get("min_units", 50))}


def new_enough(row: dict, today: date, c: dict) -> str | None:
    """⑥ 不是近期新上架、月售不够的原因（数据说明里写个数），够的返回 None。"""
    age = clock.days_between(clock.parse_day(row.get("available")), today)
    if age is None or age > c["max_age_days"]:
        return "old"
    if float(row.get("units") or 0) < c["min_units"] - 1e-9:
        return "few"
    return None


def too_simple(row: dict, look: str, c: dict) -> str | None:
    """⑤ 太小、结构太简单的原因（卡片和数据说明里写），不是的返回 None。look：主图识别的一句话外观（没识别过是空串）。"""
    pkg = row.get("pkg") or {}
    dims, weight = pkg.get("dims"), pkg.get("weight")
    if not dims or len(dims) != 3 or not weight:
        return None
    title = row.get("title") or ""
    mechanism = bool(MECHANISM.search(title) or MECHANISM_LOOK.search(look))
    if weight < c["tiny_weight"] and not mechanism:
        return "太小"
    within = all(d <= lim + 1e-9 for d, lim in zip(sorted(dims, reverse=True), XL_DIMS))
    shelving = bool(SHELVING.search(title) or SHELVING_LOOK.search(look))
    doors = bool(DOORS.search(title) or DOORS_LOOK.search(look))
    if shelving and within and weight <= c["simple_weight"] + 1e-9 and not doors and not mechanism:
        return "结构太简单"
    return None


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
            small（圈子小，③）, nodes（在哪些子类目）, simple（太小、结构太简单不算的个数，⑤）,
            old（不是近期新上架的个数，⑥）, few（月售不够的个数，⑥）, picked（进了候选的个数）}。
    评分不达标的、不是近期新上架或月售不够的、太小结构太简单的不进候选，但算进这一类的个数（它们也是竞品）。"""
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
                        "nodes": sorted({r.get("node_cn") or r.get("node_name") or "" for _f, r in members} - {""}),
                        "simple": 0, "old": 0, "few": 0, "picked": 0}
        if not small:
            continue
        for fam, r in members:
            if low_rating(r.get("rating"), r.get("ratings"), cfg["thresholds"]):
                continue
            why = new_enough(r, today, c)
            if why:
                stats[niche][why] += 1
                continue
            if too_simple(r, vision.of(state, r["asin"]).get("summary") or "", c):
                stats[niche]["simple"] += 1
                continue
            stats[niche]["picked"] += 1
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

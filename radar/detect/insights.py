"""商品卡片上的“原因”和“发现”（2026-11-16 用户要求：卡片不只放卖家精灵查得到的销量，还要写看不到的发现，
并简要说明这个商品为什么能爆火 / 有潜力，和别的商品比多了哪些元素、功能）。

每一条都是这个商品和它“同类”比出来的，写明数字，可以核对。（以前标题写“可能原因”，2026-11-16 用户要求描述里不写“可能”。）
同类 = 本期全市场名单（state["universe"]["rows"]，近 30 天 ≥300 件或 ≥4 万美元）里同一个子类目、在监控范围内的商品，
一个父体算一个（取销售额大的那一行），不含它自己那个父体。0 次调用。名单不是本期翻的（日期 ≠ today）时什么都不写。

原因：
  少见的元素   = 它的特色（detect.showcase.features：主图和标题里、这个子类目基本结构以外的设计）里，同类有的不超过 rare_share 的。
                 主图看得出的特征只在看过主图的同类里算（至少 min_seen 个，不够就不比），只看标题的特征（电子壁炉、壁床……）在全部同类的标题里算；
  同类爆品也有 = 同子类目的其他增长商品（不同父体）里至少 trend_min 个、且至少 trend_share 也有这个特色；
  价格更低     = 售价比同类中位价低 cheap 以上；
  新品追得快   = 上架 ≤ new_days 天，近 30 天销量已排同类前 new_rank_share；
  评论护城河   = 评论数是同类销量前 head_n 名评论数中位数的 moat 倍以上（多见于持续热销）。
发现：
  排名         = 近 30 天销量在同类里排第几（名单里的父体销量；不在名单里的按日数据近 28 天日均 × 30 估）；
  价格位置     = 比同类中位价高 / 低多少（不到 10% 写“和同类中位价差不多”）；
  同价位竞品   = 售价在它 ±band 以内的同类有几个、占同类销售额多少；
  FBA 占售价   = 卖家精灵给的 FBA 费 ÷ 售价（≥ fba_share 才写）。
卡片和图墙上的一句话（short）从原因里取前几条拼成。
潜力SKU 表格的“为什么值得看”没有大模型写的句子时用 worth_rule（先同类少见的外观、功能，价格放最后）。"""
from __future__ import annotations

from datetime import date
from statistics import median

from .. import vision
from ..discovery import keep_product
from . import design
from .directions import design_of

APPEARANCE = ("风格", "颜色", "造型", "工艺")  # 外观
FUNCTION = ("功能卖点",)                       # 功能
GENERIC = set(vision.GENERIC_STYLES)           # 现代、极简：几乎每件都这样，比不出不同
# 功能写进句子时的说法（“同类多有带储物/抽屉”读着别扭）
SAY = {"带储物/抽屉": "储物/抽屉", "带挂钩": "挂钩", "带镜子": "镜子", "带轮可移动": "轮子", "带锁": "锁", "带拖床": "拖床"}


def settings(cfg: dict) -> dict:
    c = (cfg.get("report") or {}).get("insights") or {}
    return {"enabled": bool(c.get("enabled", True)), "rare_share": float(c.get("rare_share", 0.25)),
            "min_seen": int(c.get("min_seen", 8)), "trend_min": int(c.get("trend_min", 2)),
            "trend_share": float(c.get("trend_share", 0.4)), "cheap": float(c.get("cheap", 0.2)),
            "new_days": int(c.get("new_days", 180)), "new_rank_share": float(c.get("new_rank_share", 0.1)),
            "head_n": int(c.get("head_n", 20)), "moat": float(c.get("moat", 2.0)), "band": float(c.get("band", 0.15)),
            "fba_share": float(c.get("fba_share", 0.25)), "max_reasons": int(c.get("max_reasons", 4)),
            "common_share": float(c.get("common_share", 0.4)),
            # 界线按同类的分布定（2026-11-16 用户：规则尽量靠数据定界线，固定数字只当下限保护）
            "cheap_quantile": float(c.get("cheap_quantile", 0.25)), "cheap_floor": float(c.get("cheap_floor", 0.1)),
            "premium_quantile": float(c.get("premium_quantile", 0.75)),
            "moat_quantile": float(c.get("moat_quantile", 0.9)), "moat_floor": float(c.get("moat_floor", 1.5)),
            "fba_quantile": float(c.get("fba_quantile", 0.75)), "fba_floor": float(c.get("fba_floor", 0.2))}


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def quantile(values: list[float], q: float) -> float | None:
    """第 q 分位（线性插值）；没有数返回 None。"""
    xs = sorted(values)
    if not xs:
        return None
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def _money(v: float) -> str:
    return f"${v:,.0f}" if v >= 10 else f"${v:,.2f}"


def _labels(state: dict, asin: str, title: str) -> tuple[set[str], bool]:
    """(标题和主图里出现的全部特征名, 有没有新提示词的主图识别)。"""
    out = {label for labels in design.extract(title or "").values() for label in labels}
    raw = vision.raw_of(state, asin)
    seen = bool(raw) and not vision.legacy(raw)
    if seen:
        out |= {label for labels in (vision.of(state, asin, title).get("tags") or {}).values() for label in labels}
    return out, seen


def context(state: dict, today: date, cfg: dict, focus: list[dict]) -> dict:
    """{子类目 nodeIdPath: 同类的统计}；focus：本期增长商品（算“同类爆品也有”）。"""
    c = settings(cfg)
    uni = state.get("universe") or {}
    if not c["enabled"] or uni.get("date") != today.isoformat():
        return {}
    fams: dict[str, dict[str, dict]] = {}
    for asin, row in (uni.get("rows") or {}).items():
        node = row.get("node")
        if not node or not keep_product(row, cfg):
            continue
        fam = row.get("parent") or asin
        best = fams.setdefault(node, {}).get(fam)
        if best is None or (_num(row.get("revenue")) or 0) > (_num(best.get("revenue")) or 0):
            fams[node][fam] = {**row, "asin": row.get("asin") or asin}
    out = {}
    for node, rows in fams.items():
        labels = {fam: _labels(state, r["asin"], r.get("title") or "") for fam, r in rows.items()}
        # 各维度的特征（和商品自己同一个口径：标题 + 主图，颜色、风格有主图时只取主色、主风格）
        designs = {fam: design_of(r, state) for fam, r in rows.items()}
        by_units = sorted(rows.values(), key=lambda r: -(_num(r.get("units")) or 0))
        heads = [_num(r.get("ratings")) for r in by_units[: c["head_n"]] if _num(r.get("ratings")) is not None]
        out[node] = {"rows": rows, "labels": labels, "designs": designs, "head_reviews": median(heads) if heads else None,
                     "revenue": sum(_num(r.get("revenue")) or 0 for r in rows.values()), "growth": []}
    for item in focus:
        node = item.get("node")
        if node in out:
            out[node]["growth"].append((item.get("parent") or item["asin"], set(item.get("features") or [])))
    return out


def _peers(node_ctx: dict, fam: str) -> list[tuple[str, dict]]:
    return [(f, r) for f, r in node_ctx["rows"].items() if f != fam]


def _share(label: str, dim: str, peers: list[str], node_ctx: dict, c: dict) -> tuple[float, int, int] | None:
    """(同类里有这个特征的比例, 有的个数, 分母)：主图看得出的特征只在看过主图的同类里算（不够 min_seen 个返回 None），
    只看标题的在全部同类里算。"""
    pool = [f for f in peers if node_ctx["labels"][f][1]] if label in design.LABEL_DIM else list(peers)
    if not pool or (label in design.LABEL_DIM and len(pool) < c["min_seen"]):
        return None
    have = sum(1 for f in pool if label in node_ctx["designs"][f].get(dim, ()))
    return have / len(pool), have, len(pool)


def _pct(share: float) -> str:
    return "没有" if share < 0.005 else f"{share:.0%}"


def compare(item: dict, node_ctx: dict, peers: list[str], c: dict
            ) -> tuple[list[tuple[str, str]], list[tuple[str, float]], dict[str, list[tuple[str, float]]]]:
    """外观、功能各一句：同类多是什么（至少 common_share 的同类有），它不同在哪（它有、同类有的不超过 rare_share）。
    返回 ([(句子, 依据)], [(它不同的特征, 同类比例)], {"外观" / "功能": 它不同的特征（同类比例从低到高，最多 3 个）})。"""
    own = item.get("design") or {}
    node = item.get("node_cn") or item.get("node_name") or "商品"
    lines, distinct_all, by_kind = [], [], {}
    for name, dims in (("外观", APPEARANCE), ("功能", FUNCTION)):
        common, distinct, plain, tips = [], [], [], []
        seen_labels = set()
        for dim in dims:
            # 同类多是：同类里出现过的特征逐个算比例
            counts = {}
            for f in peers:
                for label in node_ctx["designs"][f].get(dim, ()):
                    counts[label] = counts.get(label, 0) + 1
            for label in counts:
                if label in GENERIC:
                    continue
                sh = _share(label, dim, peers, node_ctx, c)
                if sh and sh[0] >= c["common_share"] - 1e-9:
                    common.append((label, sh[0]))
            for label in sorted(own.get(dim) or ()):
                if label in GENERIC or label in seen_labels:
                    continue
                seen_labels.add(label)
                sh = _share(label, dim, peers, node_ctx, c)
                if sh is None:
                    continue
                if sh[0] <= c["rare_share"] + 1e-9:
                    distinct.append((label, sh[0]))
                    tips.append(f"{label}：{'看过主图的' if label in design.LABEL_DIM else '标题里'} {sh[2]} 个同类里 {sh[1]} 个有")
                else:
                    plain.append(label)
        common.sort(key=lambda t: -t[1])
        def say(label: str) -> str:  # “多有带储物/抽屉”→“多有储物/抽屉”
            return SAY.get(label, label)
        common_txt = "、".join(f"{say(label)}（{share:.0%}）" for label, share in common[:3])
        distinct.sort(key=lambda t: t[1])
        distinct_txt = "、".join(f"{say(label)}（同类{'' if share < 0.005 else ' '}{_pct(share)}）" for label, share in distinct[:3])
        verb_common, verb_own = ("多是", "不同在") if name == "外观" else ("多有", "还有")
        if distinct and common:
            text = f"{name}：同类{node}{verb_common}{common_txt}；它{verb_own}{distinct_txt}。"
        elif distinct:
            text = f"{name}：它{verb_own}{distinct_txt}，同类里少见。"
        elif plain:
            text = f"{name}：和同类差不多（{'、'.join(say(x) for x in plain[:3])}）。"
        else:
            continue
        tip = (f"同类多是 / 多有 = 至少 {c['common_share']:.0%} 的同类有（括号里是比例）；不同 = 它有、同类有的不超过 {c['rare_share']:.0%}。"
               "主图看得出的特征只在看过主图的同类里算，只看标题的（电子壁炉、壁床……）在全部同类的标题里算"
               + ("；" + "；".join(tips) if tips else ""))
        lines.append((text, tip))
        distinct_all += distinct[:3]
        if distinct:
            by_kind[name] = distinct[:3]
    return lines, distinct_all, by_kind


def _rare_txt(pairs: list[tuple[str, float]]) -> str:
    return "、".join(f"{SAY.get(label, label)}（同类{'' if share < 0.005 else ' '}{_pct(share)}）" for label, share in pairs)


def worth_rule(by_kind: dict[str, list[tuple[str, float]]], features: list[str], trend: str | None,
               cheaper: float | None) -> str:
    """潜力SKU 表格里“为什么值得看”的规则句子（没有大模型写的、或没通过核对时用）。2026-11-18 用户：要聚焦元素和功能，
    价格是次要的，放最后。顺序：同类少见的外观（最多 2 个）→ 同类少见的功能（最多 2 个）
    （都没有时写它自己的特色 showcase.features）→ 同类爆品也有的特色 → 价格更低的话最后补一句。
    说不出外观、功能的返回 ""（只有价格也不写）。trend：同类爆品也有的特色；cheaper：比同类中位价便宜多少（够“价格更低”才给）。"""
    parts, said = [], set()
    for name, head in (("外观", "外观少见："), ("功能", "功能多了")):
        pairs = by_kind.get(name) or []
        if pairs:
            parts.append(head + _rare_txt(pairs[:2]))
            said |= {label for label, _s in pairs[:2]}
    if not said:
        own = [f for f in features if f not in GENERIC and f != trend][:3]
        if own:
            parts.append("特色：" + "、".join(SAY.get(f, f) for f in own))
    if trend and trend not in said:
        parts.append(f"同类爆品也有「{trend}」")
    if not parts:  # 说不出外观、功能的不写（只剩价格就不是“聚焦元素和功能”了），表格用判定依据
        return ""
    if cheaper is not None:
        parts.append(f"价格也比同类中位低 {cheaper:.0%}")
    return "；".join(parts) + "。"


def for_item(item: dict, ctx: dict, cfg: dict) -> dict | None:
    """{"reasons": [(文字, 依据)], "findings": [(文字, 依据)], "short": 一句话}；没有同类数据时返回 None。"""
    c = settings(cfg)
    node_ctx = ctx.get(item.get("node") or "")
    if not node_ctx:
        return None
    fam = item.get("parent") or item["asin"]
    peers = _peers(node_ctx, fam)
    if len(peers) < 5:
        return None
    n = len(peers)
    reasons: list[tuple[str, str]] = []
    findings: list[tuple[str, str]] = []
    short: list[str] = []
    basis = (f"同类 = 本期全市场名单里同子类目、近 30 天 ≥300 件或 ≥4 万美元的 {n} 个商品（一个父体算一个，不含它自己）")

    # 外观、功能和同类比（写成句子：同类多是什么、它不同在哪）
    cmp_lines, distinct, by_kind = compare(item, node_ctx, [f for f, _r in peers], c)
    trend, cheaper = None, None  # 给 worth_rule：同类爆品也有的特色、比同类中位便宜多少
    if distinct:
        short.append("比同类多：" + "、".join(label for label, _s in distinct[:2]))

    # 同类爆品也有
    others = [(f, feats) for f, feats in node_ctx["growth"] if f != fam]
    if len(others) >= c["trend_min"]:
        shared = []
        for label in item.get("features") or []:
            k = sum(1 for _f, feats in others if label in feats)
            if k >= c["trend_min"] and k / len(others) >= c["trend_share"] - 1e-9:
                shared.append((label, k))
        shared.sort(key=lambda t: -t[1])
        if shared:
            label, k = shared[0]
            trend = label
            reasons.append((f"同类爆品也有：同子类目另 {len(others)} 个增长商品里 {k} 个也有「{label}」",
                            "增长商品 = 本期突然爆火、新品潜力、上升中（不同父体）；多个一起涨、又都带这个元素，是这类商品现在的卖点"))
            if not distinct:
                short.append(f"同类爆品多有{label}")

    # 价格
    price = _num(item.get("price"))
    prices = [p for p in (_num(r.get("price")) for _f, r in peers) if p]
    if price and len(prices) >= 5:
        mid = median(prices)
        diff = price / mid - 1
        low_cut = quantile(prices, c["cheap_quantile"])
        high_cut = quantile(prices, c["premium_quantile"])
        cheap_tip = basis + (f"；价格更低 = 在同类最便宜的 {c['cheap_quantile']:.0%} 里（≤ {_money(low_cut)}），"
                             f"而且比中位价至少低 {c['cheap_floor']:.0%}")
        if price <= low_cut + 1e-9 and diff <= -c["cheap_floor"] + 1e-9:
            reasons.append((f"价格更低：{_money(price)}，在同类最便宜的 {c['cheap_quantile']:.0%} 里（同类中位 {_money(mid)}，便宜 {-diff:.0%}）",
                            cheap_tip))
            short.append(f"比同类便宜 {-diff:.0%}")
            cheaper = -diff
        elif price >= high_cut - 1e-9 and diff >= c["cheap_floor"] - 1e-9:
            findings.append((f"价格在同类最贵的 {1 - c['premium_quantile']:.0%} 里（{_money(price)}，同类中位 {_money(mid)}，贵 {diff:.0%}）",
                             basis + f"；最贵的 {1 - c['premium_quantile']:.0%} = ≥ {_money(high_cut)}"))
        elif abs(diff) < c["cheap_floor"]:
            findings.append((f"价格和同类中位价差不多（{_money(price)}，同类中位 {_money(mid)}）", basis))
        else:
            findings.append((f"价格比同类中位价{'高' if diff > 0 else '低'} {abs(diff):.0%}（{_money(price)}，同类中位 {_money(mid)}）",
                             basis))
        lo, hi = price * (1 - c["band"]), price * (1 + c["band"])
        band = [r for _f, r in peers if (p := _num(r.get("price"))) and lo <= p <= hi]
        rev = sum(_num(r.get("revenue")) or 0 for r in band)
        total = sum(_num(r.get("revenue")) or 0 for _f, r in peers)  # 同类不含它自己
        text = (f"同价位（{_money(lo)}~{_money(hi)}）没有够量的同类" if not band else
                f"同价位（{_money(lo)}~{_money(hi)}）够量的同类 {len(band)} 个"
                + (f"，占同类销售额 {rev / total:.0%}" if total > 0 else ""))
        findings.append((text, basis + f"；同价位 = 售价在它的 ±{c['band']:.0%} 以内"))

    # 排名（近 30 天销量）
    own = node_ctx["rows"].get(fam)
    units = _num((own or {}).get("units"))
    est = False
    if units is None and _num(item.get("recent_avg28")) is not None:
        units, est = float(item["recent_avg28"]) * 30, True
    rank = None
    if units is not None:
        rank = 1 + sum(1 for _f, r in peers if (_num(r.get("units")) or 0) > units)
        findings.append((f"近 30 天销量排同类第 {rank} / {n + 1}" + ("（按日数据估）" if est else ""),
                         basis + ("；它不在名单里（量还不够门槛），销量按日数据近 28 天日均 × 30 估" if est else
                                  "；名单里的销量是父体合计")))

    # 新品追得快
    age, ratings = item.get("age_days"), _num(item.get("ratings"))
    if age is not None and age <= c["new_days"] and rank is not None and rank <= max(1, round((n + 1) * c["new_rank_share"])):
        rv = f"、评论 {ratings:,.0f} 条" if ratings is not None else ""
        reasons.append((f"新品追得快：上架 {age} 天{rv}，近 30 天销量已排同类第 {rank}", basis))
        short.append(f"上架 {age} 天排同类第 {rank}")

    # 评论护城河
    head = node_ctx.get("head_reviews")
    moat_cut = quantile([v for v in (_num(r.get("ratings")) for _f, r in peers) if v is not None], c["moat_quantile"])
    if ratings and head and moat_cut is not None and ratings >= moat_cut - 1e-9 and ratings >= c["moat_floor"] * head:
        reasons.append((f"评论护城河：评论 {ratings:,.0f} 条，在同类前 {1 - c['moat_quantile']:.0%}，"
                        f"是同类销量前 {c['head_n']} 名中位数（{head:,.0f}）的 {ratings / head:.1f} 倍",
                        basis + f"；同类前 {1 - c['moat_quantile']:.0%} = 评论 ≥ {moat_cut:,.0f} 条，而且至少是头部中位数的 "
                        f"{c['moat_floor']:g} 倍；评论多的老链接新品很难追上"))
        if len(short) < 2:
            short.append(f"评论是同类头部的 {ratings / head:.1f} 倍")

    # FBA 占售价
    fba = _num((item.get("pkg") or {}).get("fba"))
    shares = [f_ / p_ for _f, r in peers if (f_ := _num((r.get("pkg") or {}).get("fba"))) and (p_ := _num(r.get("price")))]
    fba_cut = quantile(shares, c["fba_quantile"]) if len(shares) >= 5 else None
    if fba and price and fba_cut is not None and fba / price >= max(fba_cut, c["fba_floor"]) - 1e-9:
        findings.append((f"FBA 约 {_money(fba)}，占售价 {fba / price:.0%}，比同类 {c['fba_quantile']:.0%} 的商品都高（利润空间要算清楚）",
                         f"卖家精灵给的 FBA 配送费估算 ÷ 当前售价；同类 {c['fba_quantile']:.0%} 的商品不超过 {fba_cut:.0%}，"
                         f"至少 {c['fba_floor']:.0%} 才写"))

    if not cmp_lines and not reasons and not findings:
        return None
    # compare：外观、功能各一句和同类比；rare：它不同的特征 {特征: 同类里有的比例}（图墙卡片上标亮）；short：图墙卡片上的一句话；
    # worth_rule：潜力SKU 表格“为什么值得看”的规则句子（先外观、功能，价格最后）
    return {"compare": cmp_lines, "reasons": reasons[: c["max_reasons"]], "findings": findings,
            "short": " · ".join(short[:3]), "rare": {label: round(share, 3) for label, share in distinct},
            "worth_rule": worth_rule(by_kind, list(item.get("features") or []), trend, cheaper)}

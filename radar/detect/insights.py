"""商品卡片上的“可能原因”和“发现”（2026-11-16 用户要求：卡片不只放卖家精灵查得到的销量，还要写看不到的发现，
并简要说明这个商品为什么能爆火 / 有潜力，和别的商品比多了哪些元素、功能）。

每一条都是这个商品和它“同类”比出来的，写明数字，可以核对；不证明因果，所以标题叫“可能原因”。
同类 = 本期全市场名单（state["universe"]["rows"]，近 30 天 ≥300 件或 ≥4 万美元）里同一个子类目、在监控范围内的商品，
一个父体算一个（取销售额大的那一行），不含它自己那个父体。0 次调用。名单不是本期翻的（日期 ≠ today）时什么都不写。

可能原因：
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
卡片和图墙上的一句话（short）从可能原因里取前几条拼成。"""
from __future__ import annotations

from datetime import date
from statistics import median

from .. import vision
from ..discovery import keep_product
from . import design


def settings(cfg: dict) -> dict:
    c = (cfg.get("report") or {}).get("insights") or {}
    return {"enabled": bool(c.get("enabled", True)), "rare_share": float(c.get("rare_share", 0.25)),
            "min_seen": int(c.get("min_seen", 8)), "trend_min": int(c.get("trend_min", 2)),
            "trend_share": float(c.get("trend_share", 0.4)), "cheap": float(c.get("cheap", 0.2)),
            "new_days": int(c.get("new_days", 180)), "new_rank_share": float(c.get("new_rank_share", 0.1)),
            "head_n": int(c.get("head_n", 20)), "moat": float(c.get("moat", 2.0)), "band": float(c.get("band", 0.15)),
            "fba_share": float(c.get("fba_share", 0.25)), "max_reasons": int(c.get("max_reasons", 4))}


def _num(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


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
        by_units = sorted(rows.values(), key=lambda r: -(_num(r.get("units")) or 0))
        heads = [_num(r.get("ratings")) for r in by_units[: c["head_n"]] if _num(r.get("ratings")) is not None]
        out[node] = {"rows": rows, "labels": labels, "head_reviews": median(heads) if heads else None,
                     "revenue": sum(_num(r.get("revenue")) or 0 for r in rows.values()), "growth": []}
    for item in focus:
        node = item.get("node")
        if node in out:
            out[node]["growth"].append((item.get("parent") or item["asin"], set(item.get("features") or [])))
    return out


def _peers(node_ctx: dict, fam: str) -> list[tuple[str, dict]]:
    return [(f, r) for f, r in node_ctx["rows"].items() if f != fam]


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

    # 少见的元素
    rare = []
    seen_peers = [f for f, _r in peers if node_ctx["labels"][f][1]]
    for label in item.get("features") or []:
        if label in design.LABEL_DIM:  # 主图看得出：只在看过主图的同类里算
            pool = seen_peers
            if len(pool) < c["min_seen"]:
                continue
        else:
            pool = [f for f, _r in peers]
        have = sum(1 for f in pool if label in node_ctx["labels"][f][0])
        share = have / len(pool)
        if share <= c["rare_share"] + 1e-9:
            rare.append((label, share, have, len(pool)))
    rare.sort(key=lambda t: t[1])
    if rare:
        text = "、".join(f"{label}（同类{'没有' if share < 0.005 else f' {share:.0%}'}）" for label, share, _h, _p in rare[:3])
        tip = basis + "；" + "；".join(
            f"{label}：{'看过主图的' if label in design.LABEL_DIM else '标题里'} {p} 个同类里 {h} 个有" for label, _s, h, p in rare[:3])
        reasons.append(("少见的元素：" + text, tip))
        short.append("少见：" + "·".join(label for label, *_ in rare[:2]))

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
            reasons.append((f"同类爆品也有：同子类目另 {len(others)} 个增长商品里 {k} 个也有「{label}」",
                            "增长商品 = 本期突然爆火、潜力、上升中（不同父体）；多个一起涨、又都带这个元素，可能是这类商品现在的卖点"))
            if not rare:
                short.append(f"同类爆品多有{label}")

    # 价格
    price = _num(item.get("price"))
    prices = [p for p in (_num(r.get("price")) for _f, r in peers) if p]
    if price and len(prices) >= 5:
        mid = median(prices)
        diff = price / mid - 1
        if diff <= -c["cheap"] + 1e-9:
            reasons.append((f"价格更低：{_money(price)}，比同类中位价 {_money(mid)} 低 {-diff:.0%}", basis))
            short.append(f"比同类便宜 {-diff:.0%}")
        elif abs(diff) < 0.1:
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
    if ratings and head and ratings >= c["moat"] * head:
        reasons.append((f"评论护城河：评论 {ratings:,.0f} 条，是同类销量前 {c['head_n']} 名中位数（{head:,.0f}）的 {ratings / head:.1f} 倍",
                        basis + "；评论多的老链接新品很难追上"))
        if len(short) < 2:
            short.append(f"评论是同类头部的 {ratings / head:.1f} 倍")

    # FBA 占售价
    fba = _num((item.get("pkg") or {}).get("fba"))
    if fba and price and fba / price >= c["fba_share"] - 1e-9:
        findings.append((f"FBA 约 {_money(fba)}，占售价 {fba / price:.0%}（利润空间要算清楚）",
                         "卖家精灵给的 FBA 配送费估算 ÷ 当前售价"))

    if not reasons and not findings:
        return None
    # rare：少见的元素 {特征: 同类里有的比例}（图墙卡片上把这些特征标亮）；more：一句话里除少见元素以外的几条（图墙卡片用）
    return {"reasons": reasons[: c["max_reasons"]], "findings": findings, "short": " · ".join(short[:3]),
            "rare": {label: round(share, 3) for label, share, _h, _p in rare[:3]},
            "more": " · ".join(x for x in short if not x.startswith("少见："))}

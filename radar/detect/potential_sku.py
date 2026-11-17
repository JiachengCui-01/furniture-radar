"""潜力SKU（2026-11-17 领导要求“抓一抓潜力SKU”）：全市场有潜力的商品放成一份能直接用的清单，不分哪家店铺。
用户说潜力SKU 是“要很大概率以后能卖爆的”；“卖爆” = 判定后第 2 个月的 30 天销量 ≥ 现在近 30 天的 2 倍，而且 ≥300 件（用户定）。
回测里没有任何信号能做到“大概率”卖爆（最准的约三成），所以分两层，都列出、各写回测的命中率（用户选“两份都留”）：

  爆发苗头 = 近 7 天日均 ≥ 近 28 天日均 × boom_a7（最近一周的节奏翻倍了）、没有回落、不是老品换链接，
            按最近 7 天的节奏一个月够全市场名单门槛（近 7 天日均 × 30 ≥ pool.opportunity_min_units 件，或 × 售价 ≥ opportunity_min_revenue 美元）。
            哪个板块的都算（突然爆火、新品潜力、观察、持续热销）。
  稳步在涨 = 不是爆发苗头，下面两种之一：
    · 本期的突然爆火、新品潜力、上升中，加上近期新上架、销量已经起来但还不能确认的“新品起量”（标签见 analyze.evaluate），
      而且近 14 天日均 ≥ 近 28 天日均 × steady_accel（最近两周还在加速）；没再加速的不列（原先的“待观察”，回测不比普通商品强）；
    · 刚起势：不是上面这几类、没在跌（近 28 天日均 ≥ 之前 28 天在卖日子的日均 × early_not_falling），
      近 14 天和近 7 天日均都 ≥ 近 28 天日均 × early_accel（最近两周开始加速），近 28 天日均 × 30 在全市场名单门槛以上，不是老品换链接。

日均都去掉大促日（同 analyze），最近几天按修订幅度校正过的就用校正后的。

回测（10-23 全市场试跑 6,458 个商品各 418 天的日数据，2025-11-15~2026-09-26 每周用现在的规则倒推判一次，看之后的真实销量）：
  全部商品 132,742 次：卖爆 3.0%，2 个月内新进子类目销量前 10% 的 2.6%。
  爆发苗头 409 次：卖爆 30%，新进前 10% 17%；每个月都比普通商品高（最差的 12 月 6% 比 1.9%）。
    门槛 2 倍：1.8 倍多判 7 成、卖爆降到 23%；2.5 倍卖爆 45%，但只有三分之一的量。
    （它之后 28 天“守住、继续涨”的比例 95% / 90% 是算出来就会高的：最近 7 天已经是近 28 天的 2 倍，不拿来说。）
  稳步在涨 9,515 次：卖爆 6.5%，但之后 28 天日均 ≥ 判定时近 28 天的八成 90%、比它高 71%（全部商品 77% / 48%）——
    会接着卖、继续涨，很少会爆。其中正在涨里最近两周还在加速的 7.5%，刚起势 3.5%。
    评分高低、连续两个 28 天都在涨、有没有风险标签、销量大小都分不出好坏，所以不用。
  原先设想的“有空间没发力”（评分、价格不输同子类目同价位卖得最好的 3 个，销量不到它们一半）之后 56 天起量 14%，
    不如同门槛的基准 16% → 用户改选“刚起势”，没有做。
  样本是 10-23 那天近 30 天 ≥60 件的商品（往前看有幸存者偏差），所以只比各组之间的差，不看绝对数。
这些数写在 config.yaml thresholds.potential_sku.backtest，报告提示里照抄；改了门槛要重新回测再改这些数。

不进清单：评分偏低、异常信号、蓝海产品（只在蓝海产品里列出，2026-11-07 用户要求）、数据陈旧、
只有月度名单数据的（没有日数据，判断不了最近一两周）。原有板块都不动，商品照样在原板块里。"""
from __future__ import annotations

KINDS = ("突然爆火", "新品潜力", "上升中", "新品起量", "刚起势")
BOOM_TIER, STEADY_TIER = "爆发苗头", "稳步在涨"
BOOM, RISING, EARLY = "boom", "rising", "early"
DEFAULTS = {"boom_a7": 2.0, "steady_accel": 1.1, "early_accel": 1.1, "early_not_falling": 0.9}


def settings(cfg: dict) -> dict:
    """thresholds.potential_sku，缺的用默认；量门槛和全市场名单同一个数（pool.opportunity_min_units / _revenue）。"""
    own = (cfg.get("thresholds") or {}).get("potential_sku") or {}
    pool = cfg.get("pool") or {}
    out = {**DEFAULTS, **{k: v for k, v in own.items() if v is not None}}
    out["min_units"] = float(pool.get("opportunity_min_units", 300))
    out["min_revenue"] = float(pool.get("opportunity_min_revenue", 40000))
    return out


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def _enough(daily: float, item: dict, s: dict) -> bool:
    """日均 × 30 够全市场名单门槛：≥ min_units 件，或再 × 售价 ≥ min_revenue 美元。"""
    units = daily * 30
    return units >= s["min_units"] - 1e-9 or units * float(item.get("price") or 0) >= s["min_revenue"] - 1e-9


def rising_kind(item: dict) -> str | None:
    """正在涨的是哪一种（和 analyze.growth_kind 同一个口径，另加“新品起量”）。"""
    label = item["label"]
    if label == "surge":
        return "突然爆火"
    if label == "potential":
        return "新品潜力"
    if item.get("rising") and label in ("watch", "hot"):
        return "上升中"
    if label == "watch" and not item.get("fading") and any(t.startswith("新品起量（") for t in item.get("tags") or []):
        return "新品起量"
    return None


def classify(item: dict, s: dict) -> dict | None:
    """这个商品算不算潜力SKU：返回 {type, tier, kind, a7, a14[, base]}，不算的返回 None。s = settings(cfg)。
    先看爆发苗头，不是再看稳步在涨；正在涨里最近两周没再加速的返回 None，也不再往下判刚起势（它已经在增长商品里）。"""
    if item["label"] in ("low", "fake") or item.get("ocean") or item.get("stale") or item.get("daily") is False:
        return None
    a28 = item.get("recent_avg28")
    a7 = _ratio(item.get("last7_avg"), a28)
    a14 = _ratio(item.get("recent_avg14"), a28)
    kind = rising_kind(item)
    r7 = None if a7 is None else round(a7, 2)
    r14 = None if a14 is None else round(a14, 2)
    if (a7 is not None and a7 >= float(s["boom_a7"]) - 1e-9 and not item.get("fading") and not item.get("relist")
            and _enough(item["last7_avg"], item, s)):
        return {"type": BOOM, "tier": BOOM_TIER, "kind": kind or "", "a7": r7, "a14": r14}
    if kind:
        if a14 is None or a14 < float(s["steady_accel"]) - 1e-9:
            return None
        return {"type": RISING, "tier": STEADY_TIER, "kind": kind, "a7": r7, "a14": r14}
    if item["label"] not in ("watch", "hot") or item.get("fading") or item.get("relist"):
        return None
    prev = item.get("prev_avg28")
    if not a28 or not prev or a28 < float(s["early_not_falling"]) * prev - 1e-9:
        return None
    need = float(s["early_accel"]) - 1e-9
    if a14 is None or a7 is None or a14 < need or a7 < need or not _enough(a28, item, s):
        return None
    return {"type": EARLY, "tier": STEADY_TIER, "kind": "刚起势", "a7": r7, "a14": r14, "base": round(a28 / prev, 2)}


def mark(items: list[dict], cfg: dict) -> int:
    """给本期商品标上 item["sku"]（不算的是 None），返回个数。"""
    s = settings(cfg)
    n = 0
    for item in items:
        item["sku"] = classify(item, s)
        n += bool(item["sku"])
    return n


def tier(item: dict) -> int:
    """清单里的顺序：爆发苗头 → 稳步在涨（正在涨）→ 稳步在涨（刚起势）（同档里的顺序见 analyze.sections）。"""
    return {BOOM: 0, RISING: 1, EARLY: 2}.get((item.get("sku") or {}).get("type"), 3)


def label_of(sku: dict | None) -> str:
    """卡片、表格上的一句：“爆发苗头·新品潜力”“爆发苗头”“稳步在涨·上升中”“稳步在涨·刚起势”。"""
    if not sku:
        return ""
    return f"{sku['tier']}·{sku['kind']}" if sku.get("kind") else sku["tier"]


def why(item: dict) -> str:
    """为什么算（规则一句话，数字取自商品）：表格、钉钉、卡片上都用这一句。"""
    sku = item.get("sku") or {}
    if sku.get("type") == BOOM:
        return (f"近 7 天日均是近 28 天的 ×{sku['a7']:.2f}，最近一周的节奏翻倍了；"
                f"按这个节奏一个月约 {item['last7_avg'] * 30:,.0f} 件")
    if sku.get("type") == EARLY:
        return (f"近 14 天日均是近 28 天的 ×{sku['a14']:.2f}、近 7 天 ×{sku['a7']:.2f}，开始加速；"
                f"近 28 天是之前 28 天的 ×{sku['base']:.2f}，还没到增长门槛")
    return f"{sku.get('kind', '')}；近 14 天日均是近 28 天的 ×{sku['a14']:.2f}，还在加速"


def record(items: list[dict]) -> dict[str, list[str]]:
    """本期的潜力SKU {ASIN: [层, 类型]}，记在 runs 里：下期复查（recheck）、下期优先拉日数据（tracking.watchlist）。"""
    return {i["asin"]: [i["sku"]["tier"], i["sku"]["kind"]] for i in items if i.get("sku")}


def recheck(prev_run: dict | None, current: dict[str, list[str]], tracked: dict) -> dict | None:
    """上期列出的潜力SKU本期怎样了：仍在清单（held）/ 本期还在追踪但不再符合（lost，值是上期的层）/ 本期没追踪（gone）。
    每期在“与上期对比”里写出来，清单靠不靠谱每期都看得到真实结果。tracked：本期追踪的商品（labels）。"""
    before = (prev_run or {}).get("sku") or {}
    if not before:
        return None
    return {"total": len(before),
            "held": [a for a in before if a in current],
            "lost": {a: v[0] for a, v in before.items() if a not in current and a in tracked},
            "gone": [a for a in before if a not in current and a not in tracked]}

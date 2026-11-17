"""潜力SKU（2026-11-17 领导要求“抓一抓潜力SKU”）：全市场不分店铺，本期增长商品里最容易后续爆火的一份清单。
用户定的口径：潜力SKU 是“本期增长的产品里最容易后续爆火的产品”；
“卖爆” = 判定后第 2 个月的 30 天销量 ≥ 现在近 30 天的 2 倍，而且 ≥300 件。

  潜力SKU = 本期增长商品（突然爆火、新品潜力、上升中，同 analyze.growth_kind）里，
            近 7 天日均 ≥ 近 28 天日均 × min_a7（最近一周在加速）的，分两档：
    一周翻倍   = 近 7 天日均 ≥ 近 28 天日均 × top_a7；
    一周涨五成 = 其余（min_a7 ~ top_a7 倍）。
日均都去掉大促日（同 analyze），最近几天按修订幅度校正过的就用校正后的。

回测（10-23 全市场试跑 6,458 个商品各 418 天的日数据，2025-11-15~2026-09-26 每周用现在的规则倒推判一次，看之后的真实销量），
只看增长商品 13,135 次：后续卖爆 5.6%（全部商品 3.0%）。
  一周翻倍 486 次：卖爆 26.5%、2 个月内新进子类目销量前 10% 13.6%；各月 9%~40%。
  一周涨五成 1,927 次：卖爆 11.8%、新进前 10% 6.3%；各月 9%~24%。
  加速 1.2~1.5 倍的 5.8%，和增长商品平均一样；不到 1.2 倍的 2.2% → 都不列。
  评分、评论数、上架天数、价格、降价与否、是爆火 / 新品潜力 / 上升中，叠加上去都提不高多少（组合最好的也就 3 成左右），所以只按这一条。
  （之后 28 天“守住”的比例 93%~96% 是算出来就会高的：最近 7 天已经高出近 28 天一截，不拿来说。）
  样本是 10-23 那天近 30 天 ≥60 件的商品（往前看有幸存者偏差），所以只比各组之间的差，不看绝对数。
这些数写在 config.yaml thresholds.potential_sku.backtest，报告提示、钉钉里照抄；改了门槛要重新回测再改这些数。

之前试过、用户否掉的（都在 2026-11-17）：“有空间没发力”（回测起量不比基准高）、“把握大 / 待观察”（按会不会接着涨分，不是按会不会爆）、
“爆发苗头 + 稳步在涨”（稳步在涨卖爆只有 6.5%，而且“稳步在涨·突然爆火”名字自相矛盾；不在增长商品里的也收了）。

不进清单：蓝海产品（只在蓝海产品里列出，2026-11-07 用户要求）、数据陈旧、只有月度名单数据的（没有日数据，看不出最近一周）。
评分偏低、异常信号本来就不是增长商品。原有板块都不动，商品照样在原板块里。"""
from __future__ import annotations

TOP, MID = "一周翻倍", "一周涨五成"
KIND_CN = {"surge": "突然爆火", "potential": "新品潜力", "rising": "上升中"}
DEFAULTS = {"top_a7": 2.0, "min_a7": 1.5}


def settings(cfg: dict) -> dict:
    """thresholds.potential_sku，缺的用默认。"""
    own = (cfg.get("thresholds") or {}).get("potential_sku") or {}
    return {**DEFAULTS, **{k: v for k, v in own.items() if v is not None}}


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def growth_kind(item: dict) -> str | None:
    """增长商品是哪一种（和 analyze.growth_kind 同一个口径；这里另写一份，免得和 analyze 互相引用）。"""
    if item["label"] in ("surge", "potential"):
        return item["label"]
    if item.get("rising") and item["label"] in ("watch", "hot"):
        return "rising"
    return None


def classify(item: dict, s: dict) -> dict | None:
    """这个商品算不算潜力SKU：返回 {tier, kind, a7, a14}，不算的返回 None。s = settings(cfg)。"""
    growth = growth_kind(item)
    if not growth or item.get("ocean") or item.get("stale") or item.get("daily") is False:
        return None
    a28 = item.get("recent_avg28")
    a7 = _ratio(item.get("last7_avg"), a28)
    if a7 is None or a7 < float(s["min_a7"]) - 1e-9:
        return None
    a14 = _ratio(item.get("recent_avg14"), a28)
    return {"tier": TOP if a7 >= float(s["top_a7"]) - 1e-9 else MID, "kind": KIND_CN[growth],
            "a7": round(a7, 2), "a14": None if a14 is None else round(a14, 2)}


def mark(items: list[dict], cfg: dict) -> int:
    """给本期商品标上 item["sku"]（不算的是 None），返回个数。"""
    s = settings(cfg)
    n = 0
    for item in items:
        item["sku"] = classify(item, s)
        n += bool(item["sku"])
    return n


def tier(item: dict) -> int:
    """清单里的顺序：一周翻倍 → 一周涨五成（同档里按近 7 天 ÷ 近 28 天从高到低，见 analyze.sections）。"""
    return 0 if (item.get("sku") or {}).get("tier") == TOP else 1


def label_of(sku: dict | None) -> str:
    """卡片、表格上的一句：“一周翻倍·突然爆火”“一周涨五成·上升中”。"""
    return f"{sku['tier']}·{sku['kind']}" if sku else ""


def why(item: dict) -> str:
    """为什么算（规则一句话，数字取自商品）：表格、钉钉、卡片上都用这一句。"""
    sku = item.get("sku") or {}
    tail = "，最近一周翻倍了" if sku.get("tier") == TOP else "，最近一周涨了五成以上"
    return f"{sku.get('kind', '')}；近 7 天日均是近 28 天的 ×{sku['a7']:.2f}{tail}"


def record(items: list[dict]) -> dict[str, list[str]]:
    """本期的潜力SKU {ASIN: [档, 类型]}，记在 runs 里：下期复查（recheck）、下期优先拉日数据（tracking.watchlist）。"""
    return {i["asin"]: [i["sku"]["tier"], i["sku"]["kind"]] for i in items if i.get("sku")}


def recheck(prev_run: dict | None, current: dict[str, list[str]], tracked: dict) -> dict | None:
    """上期列出的潜力SKU本期怎样了：仍在清单（held）/ 本期还在追踪但不再符合（lost，值是上期的档）/ 本期没追踪（gone）。
    每期在“与上期对比”里写出来，清单靠不靠谱每期都看得到真实结果。tracked：本期追踪的商品（labels）。"""
    before = (prev_run or {}).get("sku") or {}
    if not before:
        return None
    return {"total": len(before),
            "held": [a for a in before if a in current],
            "lost": {a: v[0] for a, v in before.items() if a not in current and a in tracked},
            "gone": [a for a in before if a not in current and a not in tracked]}

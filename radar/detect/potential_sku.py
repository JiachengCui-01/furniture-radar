"""潜力SKU（2026-11-17 领导要求“抓一抓潜力SKU”）：全市场不分店铺，本期增长商品里最容易后续爆火的一份清单。
用户定的口径：潜力SKU 是“本期增长的产品里最容易后续爆火的产品”；
“卖爆” = 判定后第 2 个月的 30 天销量 ≥ 现在近 30 天的 2 倍，而且 ≥300 件。

  潜力SKU = 本期增长商品（突然爆火、新品潜力、上升中，同 analyze.growth_kind）里，
            加速倍数 ≥ min_a7（最近一周在加速）的，分两档：
    一周翻倍   = 加速倍数 ≥ top_a7；
    一周涨五成 = 其余（min_a7 ~ top_a7 倍）。
  加速倍数 = 近 7 天日均 ÷ 近 28 天日均，所在子类目这周整体也在加速的先扣掉整体的（除以子类目的倍数，只除大于 1 的）。
  子类目的倍数（node_pace）= 本期追踪的同子类目商品（有日数据、不陈旧、近 28 天日均 × 30 ≥ node_min_units 件）
  各自近 7 天 ÷ 近 28 天的中位数，不到 node_min 个的子类目不算（不扣）。
日均都去掉大促日（同 analyze），最近几天按修订幅度校正过的就用校正后的。

为什么扣子类目整体（2026-11-18 复核回测发现）：整个子类目一起涨的那几周（年底节日这类），单个商品的加速不是它自己的，
很快回落——子类目中位数 ≥1.25 时一周翻倍只有 6.3% 后续卖爆（79 次），子类目没在涨（<1.0）时 38.8%（183 次）；
一周涨五成 5.7% 对 15.7%。现有的“跑赢大盘”（universe.node_tide）比的是近 30 天和约 30 天前的整月，管不到这一周。
子类目的倍数用本期追踪的商品算（全市场名单没有日数据）：11-16 那期各子类目 0.97~1.14；回测里 2025-11 各子类目周的中位 1.08
（P10~P90 1.00~1.16），同一个季节对得上，追踪商品偏向候选、把中位数抬高的影响看不出来。

回测（10-23 全市场试跑 6,458 个商品各 418 天的日数据，2025-11-15~2026-09-26 每周用现在的规则倒推判一次，看之后的真实销量；
第 2 个月的结果要到判定后 57 天才有，所以算进比例的只到 2026-08-22 判的），只看增长商品 13,135 次：后续卖爆 5.6%（全部商品 3.1%）。
  一周翻倍 359 次：卖爆 32.0%（2025-11~2026-03 判的 26.5%、2026-04~08 的 38.2%），2 个月内新进子类目销量前 10% 17.3%。
  一周涨五成 1,495 次：卖爆 13.3%（前段 12.0%、后段 14.5%），新进前 10% 6.5%。
  加速 1.2~1.5 倍的 6.7%；不到 1.2 倍的 2.3% → 都不列。
  不扣子类目整体时一周翻倍 486 次 26.5%、一周涨五成 1,927 次 11.8%；扣了以后少了约四分之一，抓到的后续卖爆从 48% 降到 42%。
  评分、评论数、上架天数、价格、降价与否、是爆火 / 新品潜力 / 上升中，叠加上去都提不高多少（组合最好的也就 3 成左右），所以只按这一条。
“卖爆”的标准（第 2 个月 ≥ 现在近 30 天的 2 倍）和一周翻倍有一半是重合的：一周翻倍的最近一周已经是近 28 天的 2 倍，
只要把这个速度保持两个月就够标准；在这周的速度上再翻一倍的只有 8.1%（不到 1.2 倍的 1.7%）。报告里写明这一点。
样本是 10-23 那天还在全市场名单里的商品（幸存者偏差：越靠近 10-23 判的比例越高，后段比前段高一截），实际会低一些，
报告和钉钉里写前后两段的范围，不只写平均。
这些数写在 config.yaml thresholds.potential_sku.backtest，报告提示、钉钉里照抄；改了门槛要重新回测再改这些数。

之前试过、用户否掉的（都在 2026-11-17）：“有空间没发力”（回测起量不比基准高）、“把握大 / 待观察”（按会不会接着涨分，不是按会不会爆）、
“爆发苗头 + 稳步在涨”（稳步在涨卖爆只有 6.5%，而且“稳步在涨·突然爆火”名字自相矛盾；不在增长商品里的也收了）。

不进清单：蓝海产品（只在蓝海产品里列出，2026-11-07 用户要求）、数据陈旧、只有月度名单数据的（没有日数据，看不出最近一周）。
评分偏低、异常信号本来就不是增长商品。原有板块都不动，商品照样在原板块里。

风险标签“近7天含大促日”（PROMO，2026-11-18 复核时加）：近 7 天（数据截止日往前 7 天）里有大促日（events.detect 从本期日数据测出的）。
加速倍数是去掉大促日算的，剩下的是大促前后几天，回测里明显不准：一周翻倍的后续卖爆 18.4%（76 次），不含的 35.7%（283 次），
黑五（2025-11-29、12-06 两个判定日）和 Prime Day（2026-06-27）都低；一周涨五成 10.1%（158 次）对 13.7%。只打标签、不剔除
（还是比不列的加速 1.2~1.5 倍的 6.7% 高）。现有的“大促拉动”管不到：它只标去掉大促日就不是增长商品的，潜力SKU 都是增长商品。
复核时还查过“近 7 天靠一两天冲出来的”，不是风险，没加标签：同样的加速倍数下，最高两天占近 7 天 ≥40% 的后续卖爆不比其他低
（1.5~1.75 倍 24% 对 11%、1.75~2 倍 25% 对 18%、2~2.5 倍 32% 对 29%）；最高一天占 ≥35% 的 1,772 次里只有 6 次；
近 7 天缺数据的 6 次。先前看到的“最高一天占一半以上、后续很少卖爆”是缺了几天数据的，不是冲量。

实盘命中率（track / LIVE_KEY，2026-11-18 加）：回测有幸存者偏差，只能靠实盘纠正。每期把判定的潜力SKU（含简单款，同回测）记进
state["sku_live"]：判定时的数据截止日、档、类型、加速倍数、近 30 天销量（近 28 天日均 × 30，去掉大促日、校正过修订的，同表格）；
同一个商品还在等结果时再入选不另记（回测按商品去重后比例几乎不变）。判定后第 56 天的日数据有了（那期拉到了它的日数据）就算结果，
口径同回测：第 2 个月 = 判定后第 27~56 天这 30 天的销量（不去掉大促日，有 ≥20 天的数才算，按 30 天折算），
≥ 判定时近 30 天的 boom_ratio 倍且 ≥ boom_units 件算卖爆。到期还没出结果的下期必看（tracking.watchlist），拉到日数据就能算。
重新生成本期时先去掉本期记的再按现在的规则重记。报告潜力SKU 小窗、钉钉里写已出结果的个数和比例，和回测的范围放在一起。"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

from .. import clock
from ..series import Daily

TOP, MID = "一周翻倍", "一周涨五成"
KIND_CN = {"surge": "突然爆火", "potential": "新品潜力", "rising": "上升中"}
DEFAULTS = {"top_a7": 2.0, "min_a7": 1.5, "node_min": 10, "node_min_units": 60}
PROMO = "近7天含大促日"  # 风险标签的短名（表格、下载的 Excel、钉钉）
LIVE_KEY = "sku_live"  # state 里实盘跟踪的记录
OUT_FROM, OUT_TO, OUT_MIN_DAYS = 27, 56, 20  # 第 2 个月 = 判定后第 27~56 天（30 天），有 ≥20 天的数才算（同回测）
MAX_PICKS = 3000  # 实盘记录最多留这么多条（每期新记约 10~20 条，够两三年）


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


def node_pace(items: list[dict], s: dict) -> dict[str, float]:
    """{子类目: 这周整体加速多少}：本期追踪的同子类目商品（有日数据、不陈旧、近 28 天日均 × 30 ≥ node_min_units 件）
    各自近 7 天 ÷ 近 28 天的中位数；不到 node_min 个的子类目不算。量的下限同回测样本（判定时近 30 天 ≥60 件）。"""
    by: dict[str, list[float]] = {}
    for item in items:
        a28 = item.get("recent_avg28")
        a7 = _ratio(item.get("last7_avg"), a28)
        if a7 is None or item.get("stale") or item.get("daily") is False or not item.get("node")                 or a28 * 30 < float(s["node_min_units"]) - 1e-9:
            continue
        by.setdefault(item["node"], []).append(a7)
    return {node: round(median(v), 3) for node, v in by.items() if len(v) >= int(s["node_min"])}


def promo_days(as_of, events) -> list[str]:
    """近 7 天（数据截止日 as_of 往前 7 天，含当天）里的大促日。"""
    day = clock.parse_day(as_of) if isinstance(as_of, str) else as_of
    if not day:
        return []
    return sorted(d.isoformat() for d in events or () if 0 <= (day - d).days <= 6)


def classify(item: dict, s: dict, pace: dict[str, float] | None = None, events=()) -> dict | None:
    """这个商品算不算潜力SKU：返回 {tier, kind, a7, net, pace, a14, promo}，不算的返回 None。s = settings(cfg)。
    a7 = 近 7 天 ÷ 近 28 天（表格里显示的）；pace = 所在子类目这周整体的倍数（大于 1 才有，见 node_pace）；
    net = a7 ÷ pace（没有 pace 时就是 a7），按它分档、排序。promo = 近 7 天里的大促日（风险标签 PROMO），没有的是 None。"""
    growth = growth_kind(item)
    if not growth or item.get("ocean") or item.get("stale") or item.get("daily") is False:
        return None
    a28 = item.get("recent_avg28")
    a7 = _ratio(item.get("last7_avg"), a28)
    if a7 is None:
        return None
    tide = (pace or {}).get(item.get("node") or "")
    tide = tide if tide and tide > 1 + 1e-9 else None
    net = a7 / tide if tide else a7
    if net < float(s["min_a7"]) - 1e-9:
        return None
    a14 = _ratio(item.get("recent_avg14"), a28)
    return {"tier": TOP if net >= float(s["top_a7"]) - 1e-9 else MID, "kind": KIND_CN[growth],
            "a7": round(a7, 2), "net": round(net, 2), "pace": tide, "a14": None if a14 is None else round(a14, 2),
            "promo": promo_days(item.get("as_of"), events) or None}


def mark(items: list[dict], cfg: dict, events=()) -> int:
    """给本期商品标上 item["sku"]（不算的是 None），返回个数。子类目整体的倍数用传进来的全部商品算；events = 本期测出的大促日。"""
    s = settings(cfg)
    pace = node_pace(items, s)
    n = 0
    for item in items:
        item["sku"] = classify(item, s, pace, events)
        n += bool(item["sku"])
    return n


def tier(item: dict) -> int:
    """清单里的顺序：一周翻倍 → 一周涨五成（同档里按加速倍数 net 从高到低，见 analyze.sections）。"""
    return 0 if (item.get("sku") or {}).get("tier") == TOP else 1


def label_of(sku: dict | None) -> str:
    """卡片、表格上的一句：“一周翻倍·突然爆火”“一周涨五成·上升中”。"""
    return f"{sku['tier']}·{sku['kind']}" if sku else ""


def why(item: dict) -> str:
    """为什么算（规则一句话，数字取自商品）：表格、钉钉、卡片上都用这一句。子类目整体在涨的写明扣掉以后是多少。"""
    sku = item.get("sku") or {}
    tail = "，最近一周翻倍了" if sku.get("tier") == TOP else "，最近一周涨了五成以上"
    pace = (f"（所在子类目这周整体 ×{sku['pace']:.2f}，扣掉后 ×{sku.get('net', sku['a7']):.2f}）"
            if sku.get("pace") else "")
    return f"{sku.get('kind', '')}；近 7 天日均是近 28 天的 ×{sku['a7']:.2f}{pace}{tail}"


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


def promo_note(sku: dict, cfg: dict) -> str:
    """风险标签“近7天含大促日”的说明（表格里鼠标停在标签上）。回测数取自 backtest.promo_top / promo_mid。"""
    days = sku.get("promo") or []
    if not days:
        return ""
    span = days[0][5:] + (f"~{days[-1][5:]}" if len(days) > 1 else "")
    bt = settings(cfg).get("backtest") or {}
    key = "promo_top" if sku.get("tier") == TOP else "promo_mid"
    rate = (f"；回测里{sku.get('tier')}的这类后续卖爆 {bt[key][0]:.0%}，近 7 天没有大促日的 {bt[key][1]:.0%}"
            if bt.get(key) else "")
    return f"近 7 天里有大促日（{span}）：加速倍数是去掉这几天算的，剩下的是大促前后几天{rate}"


# ---------------- 实盘命中率：判定后第 2 个月到底卖爆没有（说明见文件开头） ----------------

def _due(pick: dict) -> date:
    return clock.parse_day(pick["day"]) + timedelta(days=OUT_TO)


def outcome(series: dict | None, day: date, units: float, s: dict) -> dict | None:
    """判定日 day 之后第 2 个月的结果：{"f": 第 2 个月的 30 天销量, "boom": 卖爆没有}；数据不到第 56 天的返回 None（还要等），
    到了但有数的不到 20 天的 f 是 None（数据不全，不算进比例）。units = 判定时的近 30 天销量。口径同回测。"""
    d = Daily.from_record(series)
    as_of = d.as_of() if d else None
    if d is None or as_of is None or as_of < day + timedelta(days=OUT_TO):
        return None
    idx = d.index_of(day)
    if idx < 0:
        return None
    w = [v for v in d.sales[idx + OUT_FROM: idx + OUT_TO + 1] if v is not None]
    if len(w) < OUT_MIN_DAYS:
        return {"f": None, "boom": False}
    bt = s.get("backtest") or {}
    f = sum(w) * 30 / len(w)
    return {"f": round(f, 1),
            "boom": f >= float(bt.get("boom_ratio", 2)) * units - 1e-9 and f >= float(bt.get("boom_units", 300)) - 1e-9}


def track(state: dict, items: list[dict], report_id: str, cfg: dict, data_date: date | None) -> dict:
    """实盘跟踪：先算到期的（用 state 里这期拉到的日数据），再记下本期判定的潜力SKU（同一个商品还在等结果时不另记）。
    本期的先去掉再重记（重新生成时按现在的规则）。返回 summary()。data_date = 本期日数据的最新日期。"""
    s = settings(cfg)
    live = state.setdefault(LIVE_KEY, {"since": report_id, "picks": []})
    picks = [p for p in live.get("picks") or [] if p.get("rid") != report_id or "f" in p]
    for p in picks:
        if "f" in p:
            continue
        got = outcome(((state.get("asins") or {}).get(p["asin"]) or {}).get("series"), clock.parse_day(p["day"]), p["u"], s)
        if got:
            p.update(got, done=report_id)
    waiting = {p["asin"] for p in picks if "f" not in p}
    for item in items:
        sku = item.get("sku")
        if not sku or item["asin"] in waiting or not item.get("as_of") or not item.get("recent_avg28"):
            continue
        picks.append({"asin": item["asin"], "rid": report_id, "day": item["as_of"], "tier": sku["tier"], "kind": sku["kind"],
                      "net": sku.get("net", sku["a7"]), "u": round(item["recent_avg28"] * 30, 1),
                      "title": (item.get("title") or "")[:80], "node": item.get("node_cn") or item.get("node_name") or ""})
        waiting.add(item["asin"])
    live["picks"] = picks[-MAX_PICKS:]
    return summary(live, data_date)


def summary(live: dict | None, data_date: date | None) -> dict:
    """报告、钉钉里写的：done {档: [出结果的个数, 卖爆的个数]}、还在等的、到期了还没拉到日数据的（overdue）、数据不全没算的（short）、
    下一批出结果的日期（next_due）、出了结果的明细（rows，判定日新的在前）。"""
    picks = (live or {}).get("picks") or []
    done = [p for p in picks if p.get("f") is not None]
    waiting = [p for p in picks if "f" not in p]
    overdue = [p for p in waiting if data_date and _due(p) <= data_date]
    later = [_due(p) for p in waiting if not (data_date and _due(p) <= data_date)]
    return {"since": (live or {}).get("since"), "total": len(picks),
            "done": {t: [sum(1 for p in done if p["tier"] == t), sum(1 for p in done if p["tier"] == t and p["boom"])]
                     for t in (TOP, MID)},
            "waiting": len(waiting), "overdue": len(overdue), "short": sum(1 for p in picks if "f" in p and p["f"] is None),
            "next_due": min(later).isoformat() if later else None,
            "rows": sorted(done, key=lambda p: (p["day"], p["asin"]), reverse=True)}


def waiting(state: dict) -> set[str]:
    """实盘还在等结果的 ASIN（状态里不整条删它们，见 pipeline 的清理）。"""
    return {p["asin"] for p in (state.get(LIVE_KEY) or {}).get("picks") or [] if "f" not in p}


def due(state: dict, today: date | None) -> list[str]:
    """到期（判定后满 56 天）还没出结果的 ASIN：下期必看（tracking.watchlist），拉到日数据才算得出。"""
    if today is None:
        return []
    picks = (state.get(LIVE_KEY) or {}).get("picks") or []
    return list(dict.fromkeys(p["asin"] for p in picks if "f" not in p and _due(p) <= today))

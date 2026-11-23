"""潜力SKU（2026-11-17 领导要求“抓一抓潜力SKU”）：全市场不分店铺，本期增长商品里最容易后续爆火的一份清单。
用户定的口径：潜力SKU 是“本期增长的产品里最容易后续爆火的产品”；
“卖爆” = 判定后第 2 个月的 30 天销量 ≥ 现在近 30 天的 2 倍，而且 ≥300 件。
  “现在近 30 天” = 近 28 天日均 × 30（base_avg = analyze 的 recent_avg28：去掉大促日，断货的日子也算进去，同表格“近30天”那一列）；
  “第 2 个月” = 判定后第 27~56 天这 30 天（去掉大促日，有 ≥20 天的数才算，按 30 天折算）。

  潜力SKU = 本期增长商品（突然爆火、新品潜力、上升中，同 analyze.growth_kind；蓝海产品也算）里，
            加速倍数 ≥ min_a7（1.5，最近一周在加速）的，分两档：
    一周翻倍   = 加速倍数 ≥ top_a7（2）；
    一周涨五成 = 其余（1.5 ~ 2 倍）。
  加速倍数 = 近 7 天日均 ÷ 近 28 天日均（都去掉大促日，最近几天按修订幅度校正过的就用校正后的），按显示的两位小数分档。
  这是 2026-11-17 的判定（2026-11-20 用户看了 11-17 那版判定 26 个、列出 23 个，要恢复成那个数量）。

一直保留的（不影响判定，只是写明、记下）：
  风险标签：近7天含大促日（PROMO）、月度增长尚未验证（UNVER）、老品换链接（RELIST，回测数单算、不进两档的比例），各写回测数；
  表格“回测卖爆”一列（band）：回测里同一段加速倍数的后续卖爆，前段~后段，还符合“同一档里分得出的”条件的括号里另写；
  实盘命中率（track）；蓝海产品照样判（2026-11-20 起，清单上标“蓝海产品”，sku["ocean"]；兴趣爱好类目不在回测样本里）。

回测（10-23 全市场试跑 6,458 个商品各 418 天的日数据，2025-11-15~2026-08-22 每周用现在的规则倒推判一次、41 个判定日，
看之后的真实销量；第 2 个月去掉大促日，同实盘），只看增长商品 13,145 次：后续卖爆 5.3%（全部商品 2.9%）。不含老品换链接：
  一周翻倍 431 次（347 个商品）：卖爆 23.4%（2025-11~2026-03 判的 17.8%、2026-04~08 的 30.7%），2 个月内新进子类目销量前 10% 12.5%。
  一周涨五成 1,811 次（1,272 个商品）：卖爆 9.6%（前段 7.5%、后段 12.0%），新进前 10% 5.3%。
  不列的：加速 1.2~1.5 倍的 5.1%（4,117 次）；不到 1.2 倍的 2.0%。
  按判定周整周重抽，95% 区间一周翻倍 18%~30%、一周涨五成 7.9%~11.7%。两档合起来 12.2%，抓到的增长商品后续卖爆 40%；
  回测样本每期列出的中位 45 个。11-17 定这版时写的 26.5% / 11.8% 是当时的卖爆口径（第 2 个月没去掉大促日、含老品换链接）。
“卖爆”的标准（第 2 个月 ≥ 现在近 30 天的 2 倍）和一周翻倍有一半是重合的：一周翻倍的最近一周已经是近 28 天的 2 倍，
只要把这个速度保持两个月就够标准；在这周的速度上再翻一倍的只有 5.1%。报告里写明这一点。
样本是 10-23 那天还在全市场名单里的商品（幸存者偏差：越靠近 10-23 判的比例越高，后段比前段高一截），实际会低一些，
报告和钉钉里写前后两段的范围，不只写平均。
这些数写在 config.yaml thresholds.potential_sku.backtest，报告提示、钉钉里照抄；改了门槛要重新回测再改这些数。

2026-11-18 ~ 11-20 试过、11-20 又按用户要求恢复成上面这版判定的（数量少了）：
  扣掉子类目整体的加速（除以同子类目追踪商品的中位数）、近 28 天日均只算在售的日子、一周涨五成不收带“月度增长尚未验证”
  “降价驱动”的、线放到 ×1.4 改叫“一周涨四成”。那几样合起来 11-16 那期判定 16 个、列出 13 个（只放到 ×1.4 以前是 8 个、7 个）；
  回测比例高一些（一周翻倍 27%、中间一档 11%），但抓到的少。按显示的两位小数分档（11-19 加的）、实盘等修订完再算、停更不再等留着。
  状态里 11-20 记成“一周涨四成”的（实盘记录、上期的潜力SKU）读的时候按一周涨五成算（tier_name）。
风险标签“近7天含大促日”（PROMO）：加速倍数是去掉大促日算的，剩下的是大促前后几天，回测里明显不准：
  一周翻倍里有的 9.8%（92 次），没有的 27.1%；一周涨五成 6.9%（188 次）对 9.9%。只打标签、不剔除。
月度增长尚未验证：一周翻倍里有的 8.1%（37 次）对 24.9%；一周涨五成 6.2%（209 次）对 10.0%。照收，风险里写明。
实盘命中率（track / LIVE_KEY）：回测有幸存者偏差，只能靠实盘纠正。每期把判定的潜力SKU（含简单款，同回测）记进 state["sku_live"]：
判定时的数据截止日、档、类型、加速倍数、近 30 天销量；同一个商品还在等结果时再入选不另记。到期（第 2 个月结束再过 OUT_SETTLE 天，
最近几天的数改完了）以后拉到它的日数据就算结果，口径同回测。到期还没出结果的下期必看（tracking.watchlist）；到期后 OUT_WAIT 天
还没有第 56 天日数据的不再等，记成停更、不算进比例。重新生成本期时先去掉本期记的再按现在的规则重记。

之前试过、用户否掉的（都在 2026-11-17）：“有空间没发力”（回测起量不比基准高）、“把握大 / 待观察”（按会不会接着涨分，不是按会不会爆）、
“爆发苗头 + 稳步在涨”（稳步在涨卖爆只有 6.5%，而且“稳步在涨·突然爆火”名字自相矛盾；不在增长商品里的也收了）。

不进清单：数据陈旧、只有月度名单数据的（没有日数据，看不出最近一周）。评分偏低、异常信号本来就不是增长商品。
原有板块都不动，商品照样在原板块里。"""
from __future__ import annotations

from datetime import date, timedelta

from .. import clock
from ..series import Daily
from .rules import UNSTEADY_HEADS, unsteady_short

TOP, MID = "一周翻倍", "一周涨五成"
# 别的叫法 → 现在的：2026-11-20 有几个小时线在 ×1.4、叫一周涨四成，又恢复成 ×1.5 的一周涨五成。实盘记录、runs 里上期的潜力SKU 存的是名字；
# 那期重新生成时按现在的规则重记，留下的 ×1.4~1.5 的记录没有
OLD_NAMES = {"一周涨四成": MID}
KIND_CN = {"surge": "突然爆火", "potential": "新品潜力", "rising": "上升中"}
DEFAULTS = {"top_a7": 2.0, "min_a7": 1.5, "live_min": 30}
PROMO = "近7天含大促日"  # 风险标签的短名（表格、下载的 Excel、钉钉）
UNVER, PRICE, RELIST = "月度增长尚未验证", "降价驱动", "老品换链接"
# 风险里显示的（表格、下载的 Excel、钉钉）：2026-11-21 用户要写明是因为新品（只有新品潜力、上架 ≤90 天的才有这个标签）
UNVER_SHOW = "月度增长尚未验证（新品，完整月份不足）"
LIVE_KEY = "sku_live"  # state 里实盘跟踪的记录
OUT_FROM, OUT_TO, OUT_MIN_DAYS = 27, 56, 20  # 第 2 个月 = 判定后第 27~56 天（30 天），有 ≥20 天的数才算（同回测）
# 第 2 个月结束后再等这么多天、用那以后拉的日数据算：最近几天的数卖家精灵还会改（各期测到改最近 5~9 天，×0.82~×1.08，
# 量修订只看 14 天），回测用的是改完的。以前满 56 天就算，第 2 个月按各期测到的修订平均多算约 1.9%（10-23 那次 6.6%）
OUT_SETTLE = 14
# 到期后最多再等这么多天：断货、下架后卖家精灵的日数据停在最后在售那天（本期拉到的 629 个里 29 个停了 3 天以上），
# 等不到第 56 天；以前一直等，每期都进必看、多花一次调用，状态里也不删，“到期了还没拉到”越积越多
OUT_WAIT = 28
MAX_PICKS = 3000  # 实盘记录最多留这么多条（每期新记约 10~20 条，够两三年）


def settings(cfg: dict) -> dict:
    """thresholds.potential_sku，缺的用默认。"""
    own = (cfg.get("thresholds") or {}).get("potential_sku") or {}
    return {**DEFAULTS, **{k: v for k, v in own.items() if v is not None}}


def tier_name(name: str | None) -> str | None:
    """状态里存着的档名换成现在的叫法（OLD_NAMES）；别的原样返回。"""
    return OLD_NAMES.get(name, name) if name else name


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def growth_kind(item: dict) -> str | None:
    """增长商品是哪一种（和 analyze.growth_kind 同一个口径；这里另写一份，免得和 analyze 互相引用）。"""
    if item["label"] in ("surge", "potential"):
        return item["label"]
    if item.get("rising") and item["label"] in ("watch", "hot"):
        return "rising"
    return None


def base_avg(item: dict) -> float | None:
    """近 28 天日均（analyze 的 recent_avg28：去掉大促日，断货的日子也算进去，同表格“近30天”那一列）：加速倍数和卖爆的基数。"""
    return item.get("recent_avg28")


def _tagged(item: dict, word: str) -> bool:
    return any(word in tag for tag in item.get("tags") or [])


def promo_days(as_of, events) -> list[str]:
    """近 7 天（数据截止日 as_of 往前 7 天，含当天）里的大促日。"""
    day = clock.parse_day(as_of) if isinstance(as_of, str) else as_of
    if not day:
        return []
    return sorted(d.isoformat() for d in events or () if 0 <= (day - d).days <= 6)


def classify(item: dict, s: dict, events=()) -> dict | None:
    """这个商品算不算潜力SKU：返回 {tier, kind, a7, net, a14, promo, flags}（蓝海产品另有 ocean: True），不算的返回 None。
    s = settings(cfg)。a7 = net = 近 7 天 ÷ 近 28 天日均（按显示的两位小数），按它分档、排序。
    promo = 近 7 天里的大促日（风险标签 PROMO），没有的是 None。flags = 照收但要写明的：月度增长尚未验证、老品换链接。"""
    growth = growth_kind(item)
    if not growth or item.get("stale") or item.get("daily") is False:
        return None
    a28 = base_avg(item)
    a7 = _ratio(item.get("last7_avg"), a28)
    if a7 is None:
        return None
    # 按显示的两位小数分档、对段（2026-11-19 起）：×1.996 显示 ×2.00 算一周翻倍；×1.4952 显示 ×1.50 算一周涨五成
    net = round(a7, 2)
    if net < float(s["min_a7"]) - 1e-9:
        return None
    tier = TOP if net >= float(s["top_a7"]) - 1e-9 else MID
    a14 = _ratio(item.get("recent_avg14"), a28)
    flags = [UNVER] if _tagged(item, UNVER) else []
    if item.get("relist"):
        flags.append(RELIST)
    out = {"tier": tier, "kind": KIND_CN[growth], "a7": net, "net": net, "a14": None if a14 is None else round(a14, 2),
           "promo": promo_days(item.get("as_of"), events) or None, "flags": flags}
    if item.get("ocean"):  # 蓝海产品只在蓝海产品里列（不进突然爆火、新品潜力、增长商品），清单上写明在哪
        out["ocean"] = True
    unsteady = next((t for t in item.get("tags") or [] if t.startswith(UNSTEADY_HEADS)), None)
    if unsteady:  # 评论太少（analyze 打的标签）：照收，风险里写“评论仅 N 条，评分还不稳”（2026-11-23 用户要）
        out["unsteady"] = unsteady_short(unsteady)
    return out


def mark(items: list[dict], cfg: dict, events=()) -> int:
    """给本期商品标上 item["sku"]（不算的是 None），返回个数。events = 本期测出的大促日。"""
    s = settings(cfg)
    n = 0
    for item in items:
        item["sku"] = classify(item, s, events)
        n += bool(item["sku"])
    return n


def tier(item: dict) -> int:
    """清单里的顺序：一周翻倍 → 一周涨五成（同档里按加速倍数 net 从高到低，见 analyze.sections）。"""
    return 0 if (item.get("sku") or {}).get("tier") == TOP else 1


def label_of(sku: dict | None) -> str:
    """卡片、表格上的一句：“一周翻倍·突然爆火”“一周涨五成·上升中”；蓝海产品后面加“·蓝海产品”。"""
    return f"{sku['tier']}·{sku['kind']}" + ("·蓝海产品" if sku.get("ocean") else "") if sku else ""


def why(item: dict) -> str:
    """为什么算（规则一句话，数字取自商品）：表格、钉钉、卡片上都用这一句。"""
    sku = item.get("sku") or {}
    tail = "，最近一周翻倍了" if sku.get("tier") == TOP else "，最近一周涨了五成以上"
    return f"{sku.get('kind', '')}；近 7 天日均是近 28 天日均的 ×{sku['a7']:.2f}{tail}"


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
            "lost": {a: tier_name(v[0]) for a, v in before.items() if a not in current and a in tracked},
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


def band(sku: dict, cfg: dict) -> tuple[float, float] | None:
    """回测里加速倍数（net）在同一段的后续卖爆：(前段, 后段)，取自 backtest.bands（[下限, 前段, 后段]，从小到大）。"""
    net = sku.get("net", sku.get("a7"))
    hit = None
    for low, front, back in (settings(cfg).get("backtest") or {}).get("bands") or []:
        if net is not None and net >= float(low) - 1e-9:
            hit = (float(front), float(back))
    return hit


def risk_notes(sku: dict, cfg: dict) -> list[str]:
    """表格风险格的说明（鼠标停留）：近7天含大促日、月度增长尚未验证、老品换链接，各写回测数（按这一行的档）。"""
    bt = settings(cfg).get("backtest") or {}
    out = [promo_note(sku, cfg)] if sku.get("promo") else []
    flags = sku.get("flags") or []
    top = sku.get("tier") == TOP
    if UNVER in flags:
        u = bt.get("unver_top" if top else "unver_mid")
        out.append("月度增长尚未验证：新品完整月份还不够，看不出月度在涨"
                   + (f"；回测里{sku.get('tier')}的这类后续卖爆 {u[0]:.0%}，其他 {u[1]:.0%}" if u else ""))
    if RELIST in flags:
        r = bt.get("relist")
        out.append("老品换链接：新链接还在起量、基数低，后续卖爆多半是回到老链接原来的量"
                   + (f"；回测里这类一周翻倍的 {r[0]:.0%}、一周涨五成的 {r[1]:.0%} 后续卖爆，不算进两档的比例" if r else ""))
    if sku.get("unsteady"):
        out.append(f"{sku['unsteady']}：几条评论的评分说明不了什么，之后几条差评就可能掉到评分门槛以下{unsteady_backtest(cfg)}")
    return out


def unsteady_backtest(cfg: dict) -> str:
    """评论太少的回测数（thresholds.unsteady_backtest：[评论下限, 上限, 之后 <4.0 的个数, 商品数]），样本少，写个数。"""
    rows = (cfg.get("thresholds") or {}).get("unsteady_backtest") or []
    parts = [f"评论 {lo}~{hi} 条的 {k}/{n} 个" if hi else f"{lo} 条以上的 {k}/{n} 个" for lo, hi, k, n in rows]
    return ("；回测（各期评论变多了的商品）之后掉到 4.0 以下：" + "、".join(parts)) if parts else ""


# ---------------- 实盘命中率：判定后第 2 个月到底卖爆没有（说明见文件开头） ----------------

def _due(pick: dict) -> date:
    """出结果的日子：第 2 个月结束（判定后第 56 天）再过 OUT_SETTLE 天，最近几天的数改完了。"""
    return clock.parse_day(pick["day"]) + timedelta(days=OUT_TO + OUT_SETTLE)


def _settled(rec: dict, pick: dict) -> bool:
    """这个商品存着的日数据是不是到期（_due）以后拉的：那时第 2 个月的数已经改完。
    按拉取时间（tracking.fetch_one 记的 fetched_at）看，不看日数据最后一天：断货后日数据停在最后在售那天，
    第 56 天以后才停的（回测里算的）也要算得出来。没记拉取时间的（旧状态）看日数据最后一天。"""
    fetched = clock.parse_day(rec.get("fetched_at"))
    if fetched is None:
        d = Daily.from_record(rec.get("series"))
        fetched = d.as_of() if d else None
    return bool(fetched and fetched >= _due(pick))


def outcome(series: dict | None, day: date, units: float, s: dict, events=(), settled: bool = True) -> dict | None:
    """判定日 day 之后第 2 个月的结果：{"f": 第 2 个月的 30 天销量, "boom": 卖爆没有}；数据不到第 56 天的、
    日数据不是到期以后拉的（settled 为 False，最近几天的数还会改）返回 None（还要等），
    到了但有数的不到 20 天的 f 是 None（数据不全，不算进比例）。units = 判定时的近 30 天销量（近 28 天日均 × 30）。
    events = 大促日（不算进第 2 个月）。口径同回测。"""
    d = Daily.from_record(series)
    as_of = d.as_of() if d else None
    if not settled or d is None or as_of is None or as_of < day + timedelta(days=OUT_TO):
        return None
    idx = d.index_of(day)
    if idx < 0:
        return None
    skip = set(events or ())
    w = [d.sales[i] for i in range(idx + OUT_FROM, min(idx + OUT_TO + 1, len(d.sales)))
         if d.sales[i] is not None and d.day(i) not in skip]
    if len(w) < OUT_MIN_DAYS:
        return {"f": None, "boom": False}
    bt = s.get("backtest") or {}
    f = sum(w) * 30 / len(w)
    return {"f": round(f, 1),
            "boom": f >= float(bt.get("boom_ratio", 2)) * units - 1e-9 and f >= float(bt.get("boom_units", 300)) - 1e-9}


def track(state: dict, items: list[dict], report_id: str, cfg: dict, data_date: date | None, events=()) -> dict:
    """实盘跟踪：先算到期的（用 state 里这期拉到的日数据），再记下本期判定的潜力SKU（同一个商品还在等结果时不另记）。
    本期的先去掉再重记（重新生成时按现在的规则）。返回 summary()。data_date = 本期日数据的最新日期；
    events = 本期测出的大促日（第 2 个月里不算）。"""
    s = settings(cfg)
    live = state.setdefault(LIVE_KEY, {"since": report_id, "picks": []})
    picks = [p for p in live.get("picks") or [] if p.get("rid") != report_id or "f" in p]
    for p in picks:
        if p.get("tier") in OLD_NAMES:  # 以前的叫法（一周涨五成）换成现在的，存回状态
            p["tier"] = tier_name(p["tier"])
        if "f" in p:
            continue
        rec = (state.get("asins") or {}).get(p["asin"]) or {}
        got = outcome(rec.get("series"), clock.parse_day(p["day"]), p["u"], s, events, settled=_settled(rec, p))
        if got is None and data_date and data_date >= _due(p) + timedelta(days=OUT_WAIT):
            # 到期 OUT_WAIT 天还没有第 56 天的日数据（停更或一直没拉到）：不再等，不算进比例（回测样本的日数据都到了第 56 天以后）
            got = {"f": None, "boom": False, "stop": True}
        if got:
            p.update(got, done=report_id)
    waiting = {p["asin"] for p in picks if "f" not in p}
    for item in items:
        sku = item.get("sku")
        if not sku or item["asin"] in waiting or not item.get("as_of") or not base_avg(item):
            continue
        picks.append({"asin": item["asin"], "rid": report_id, "day": item["as_of"], "tier": sku["tier"], "kind": sku["kind"],
                      "net": sku.get("net", sku["a7"]), "u": round(base_avg(item) * 30, 1),
                      "title": (item.get("title") or "")[:80], "node": item.get("node_cn") or item.get("node_name") or "",
                      "flags": list(sku.get("flags") or [])})
        waiting.add(item["asin"])
    live["picks"] = picks[-MAX_PICKS:]
    return summary(live, data_date)


def summary(live: dict | None, data_date: date | None) -> dict:
    """报告、钉钉里写的：done {档: [出结果的个数, 卖爆的个数]}（不含老品换链接，同回测）、relist（老品换链接单算的 [个数, 卖爆]）、
    还在等的、到期了还没拉到第 56 天日数据的（overdue，最多再等 OUT_WAIT 天）、到期 OUT_WAIT 天还没有、不再等的（stop）、
    第 2 个月有数的不到 20 天没算的（short）、下一批出结果的日期（next_due）、出了结果的明细（rows，判定日新的在前，含老品换链接）。"""
    picks = (live or {}).get("picks") or []
    every = [p for p in picks if p.get("f") is not None]
    done = [p for p in every if RELIST not in (p.get("flags") or [])]
    relist = [p for p in every if RELIST in (p.get("flags") or [])]
    waiting = [p for p in picks if "f" not in p]
    overdue = [p for p in waiting if data_date and _due(p) <= data_date]
    later = [_due(p) for p in waiting if not (data_date and _due(p) <= data_date)]
    return {"since": (live or {}).get("since"), "total": len(picks),
            "done": {t: [sum(1 for p in done if tier_name(p["tier"]) == t),
                         sum(1 for p in done if tier_name(p["tier"]) == t and p["boom"])] for t in (TOP, MID)},
            "relist": [len(relist), sum(1 for p in relist if p["boom"])],
            "waiting": len(waiting), "overdue": len(overdue),
            "short": sum(1 for p in picks if "f" in p and p["f"] is None and not p.get("stop")),
            "stop": sum(1 for p in picks if p.get("stop")),
            "next_due": min(later).isoformat() if later else None,
            "rows": sorted(every, key=lambda p: (p["day"], p["asin"]), reverse=True)}


def waiting(state: dict) -> set[str]:
    """实盘还在等结果的 ASIN（状态里不整条删它们，见 pipeline 的清理）。"""
    return {p["asin"] for p in (state.get(LIVE_KEY) or {}).get("picks") or [] if "f" not in p}


def due(state: dict, today: date | None) -> list[str]:
    """到期（判定后满 56 天）还没出结果的 ASIN：下期必看（tracking.watchlist），拉到日数据才算得出。"""
    if today is None:
        return []
    picks = (state.get(LIVE_KEY) or {}).get("picks") or []
    return list(dict.fromkeys(p["asin"] for p in picks if "f" not in p and _due(p) <= today))

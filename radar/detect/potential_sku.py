"""潜力SKU（2026-11-17 领导要求“抓一抓潜力SKU”）：全市场不分店铺，本期增长商品里最容易后续爆火的一份清单。
用户定的口径：潜力SKU 是“本期增长的产品里最容易后续爆火的产品”；
“卖爆” = 判定后第 2 个月的 30 天销量 ≥ 现在近 30 天的 2 倍，而且 ≥300 件。
  “现在近 30 天” = 近 28 天在售日的日均 × 30（base_avg：断货的日子不算，同对比期的口径）；
  “第 2 个月” = 判定后第 27~56 天这 30 天（去掉大促日，有 ≥20 天的数才算，按 30 天折算）。

  潜力SKU = 本期增长商品（突然爆火、新品潜力、上升中，同 analyze.growth_kind）里，
            加速倍数 ≥ min_a7（最近一周在加速）的，分两档：
    一周翻倍   = 加速倍数 ≥ top_a7；
    一周涨五成 = 其余（min_a7 ~ top_a7 倍），但不收带“月度增长尚未验证”“降价驱动”标签的（WEAK_MID）。
  加速倍数 = 近 7 天日均 ÷ 近 28 天在售日的日均，所在子类目这周整体也在加速的先扣掉整体的（除以子类目的倍数，只除大于 1 的）。
  子类目的倍数（node_pace）= 本期追踪的同子类目商品（有日数据、不陈旧、近 28 天在售日均 × 30 ≥ node_min_units 件）
  各自近 7 天 ÷ 近 28 天在售日均的中位数，不到 node_min 个的子类目不算（不扣）。
日均都去掉大促日（同 analyze），最近几天按修订幅度校正过的就用校正后的。

2026-11-19 复核后改的三处（用户：先改前三项）：
  ① 近 28 天日均以前把断货的日子按 0 算进去：断货恢复的商品基数被压低、加速倍数虚高，“卖爆”很多只是回到原来的量
     （回测里断货恢复的一周翻倍 51% 卖爆、一周涨五成 22%）→ 基数改按在售的日子算（analyze 的 instock_avg28）。
     一周翻倍从 359 次降到 245 次。改了以后断货恢复的还是高一截（一周翻倍 43% 对 25%、一周涨五成 21% 对 10%）：
     会断货的多半本来就好卖，补货后排名回升的那几天也还在基数里，分不开，照样算进两档。
  ② 第 2 个月以前没去掉大促日（判定时的近 28 天去掉了，前后口径不一致）：结果窗口里有大促日的一周涨五成卖爆 19.3%，去掉后 14.8%。
  ③ 一周涨五成里“月度增长尚未验证”（新品完整月份不足）的 6.7%（149 次）、“降价驱动”的 9.4%（214 次），
     和不列的加速 1.2~1.5 倍（6.3%）差不多 → 不收。一周翻倍里的照收，表格风险里标“月度增长尚未验证”（15.8% 对 28.8%）。
  老品换链接（新链接还在起量，基数低）的单算，不算进两档的比例：一周翻倍 42.9%（28 次）、一周涨五成 27.0%（111 次），
  多半是回到老链接原来的量；表格风险里标“老品换链接”写明。
同一天又改的三处（用户：再改剩下几项）：
  ④ 一档一个数盖住了档里的差别：表格每行写“回测卖爆”（band）= 回测里加速倍数（扣过子类目）在同一段的后续卖爆，前段~后段：
     ×1.5~1.75 718 次 10.7%（10%~11%）、×1.75~2 276 次 16.7%（15%~18%）、×2~2.5 176 次 23.3%（21%~26%）、
     ×2.5 以上 41 次 46.3%（32%~63%）；老品换链接的写单算的比例。分段每段 ≥40 次，单调。
     这一行还符合 ⑤ 里分得出的条件时，括号里另写那一类的比例（同一段加速里量大的也低：×2~2.5 ≥600 件 12% 对 26%）。
  ⑤ 提示里原来写“评分、评论数、上架天数、价格都分不出谁更容易爆”，说过头了：同一档里分得出的是
     一周涨五成里上架一年以上的 9.2%（423 次，其他 14.7%）、一周翻倍里近 30 天 ≥600 件的 12.8%（39 次，其他 30.9%，
     卖爆要翻倍，量大的难翻；近 30 天同表格那一列 = 近 28 天日均 × 30，断货的日子按 0）、一周翻倍里评论 500 条以上的 41.3%
     （46 次，其他 24.0%），前后两段都对得上；评分、价格分组后高低不一，分不出。
  ⑥ 实盘每档出结果不到 live_min 个时比例旁写“样本少”：按判定周整周重抽，单周 0%~80% 都有，30 个时 95% 区间还有 ±16 个百分点。

为什么扣子类目整体（2026-11-18 复核回测发现）：整个子类目一起涨的那几周（年底节日这类），单个商品的加速不是它自己的，
很快回落——子类目中位数 ≥1.25 时一周翻倍只有 6.3% 后续卖爆（79 次），子类目没在涨（<1.0）时 38.8%（183 次）；
一周涨五成 5.7% 对 15.7%。现有的“跑赢大盘”（universe.node_tide）比的是近 30 天和约 30 天前的整月，管不到这一周。
子类目的倍数用本期追踪的商品算（全市场名单没有日数据）：11-16 那期各子类目 0.97~1.14；回测里 2025-11 各子类目周的中位 1.08
（P10~P90 1.00~1.16），同一个季节对得上，追踪商品偏向候选、把中位数抬高的影响看不出来。

回测（10-23 全市场试跑 6,458 个商品各 418 天的日数据，2025-11-15~2026-09-26 每周用现在的规则倒推判一次，看之后的真实销量；
第 2 个月的结果要到判定后 57 天才有，所以算进比例的只到 2026-08-22 判的），只看增长商品 13,145 次：后续卖爆 5.1%（全部商品 2.8%）。
2026-11-19 的口径（不含老品换链接）：
  一周翻倍 217 次（181 个商品）：卖爆 27.7%（2025-11~2026-03 判的 22.8%、2026-04~08 的 33.0%），2 个月内新进子类目销量前 10% 16.1%。
  一周涨五成 994 次（756 个商品）：卖爆 12.4%（前段 11.3%、后段 13.3%），新进前 10% 6.1%。
  不收的：一周涨五成里月度增长尚未验证 / 降价驱动的 8.3%（363 次）；加速 1.2~1.5 倍的 6.3%；不到 1.2 倍的 2.2%。
  按判定周整周重抽，95% 区间一周翻倍 21%~36%、一周涨五成 10%~15%。抓到的增长商品后续卖爆 34%。
  以前的口径：11-18 一周翻倍 359 次 32.0%、一周涨五成 1,495 次 13.3%；扣子类目整体以前 486 次 26.5%、1,927 次 11.8%。
  评分、价格、是爆火还是上升中，叠加上去都提不高多少，所以只按这一条（不收的两种标签见上面 ③）。
“卖爆”的标准（第 2 个月 ≥ 现在近 30 天的 2 倍）和一周翻倍有一半是重合的：一周翻倍的最近一周已经是近 28 天的 2 倍，
只要把这个速度保持两个月就够标准；在这周的速度上再翻一倍的只有 5.5%（不到 1.2 倍的 1.7%）。报告里写明这一点。
样本是 10-23 那天还在全市场名单里的商品（幸存者偏差：越靠近 10-23 判的比例越高，后段比前段高一截），实际会低一些，
报告和钉钉里写前后两段的范围，不只写平均。
这些数写在 config.yaml thresholds.potential_sku.backtest，报告提示、钉钉里照抄；改了门槛要重新回测再改这些数。

之前试过、用户否掉的（都在 2026-11-17）：“有空间没发力”（回测起量不比基准高）、“把握大 / 待观察”（按会不会接着涨分，不是按会不会爆）、
“爆发苗头 + 稳步在涨”（稳步在涨卖爆只有 6.5%，而且“稳步在涨·突然爆火”名字自相矛盾；不在增长商品里的也收了）。

不进清单：蓝海产品（只在蓝海产品里列出，2026-11-07 用户要求）、数据陈旧、只有月度名单数据的（没有日数据，看不出最近一周）。
评分偏低、异常信号本来就不是增长商品。原有板块都不动，商品照样在原板块里。

风险标签“近7天含大促日”（PROMO，2026-11-18 复核时加）：近 7 天（数据截止日往前 7 天）里有大促日（events.detect 从本期日数据测出的）。
加速倍数是去掉大促日算的，剩下的是大促前后几天，回测里明显不准（11-19 的口径）：一周翻倍的后续卖爆 9.6%（52 次），
不含的 33.3%（165 次），黑五（2025-11-29、12-06 两个判定日）和 Prime Day（2026-06-27）都低；一周涨五成 10.3%（116 次）对 12.6%。
只打标签、不剔除（还是比不列的加速 1.2~1.5 倍的 6.3% 高）。现有的“大促拉动”管不到：它只标去掉大促日就不是增长商品的，潜力SKU 都是增长商品。
复核时还查过“近 7 天靠一两天冲出来的”，不是风险，没加标签：同样的加速倍数下，最高两天占近 7 天 ≥40% 的后续卖爆不比其他低
（1.5~1.75 倍 24% 对 11%、1.75~2 倍 25% 对 18%、2~2.5 倍 32% 对 29%）；最高一天占 ≥35% 的 1,772 次里只有 6 次；
近 7 天缺数据的 6 次。先前看到的“最高一天占一半以上、后续很少卖爆”是缺了几天数据的，不是冲量。

实盘命中率（track / LIVE_KEY，2026-11-18 加）：回测有幸存者偏差，只能靠实盘纠正。每期把判定的潜力SKU（含简单款，同回测）记进
state["sku_live"]：判定时的数据截止日、档、类型、加速倍数、近 30 天销量（近 28 天在售日均 × 30，去掉大促日、校正过修订的）；
同一个商品还在等结果时再入选不另记（回测按商品去重后比例几乎不变）。到期（第 2 个月结束再过 OUT_SETTLE 天，最近几天的数改完了）
以后拉到它的日数据就算结果，口径同回测：第 2 个月 = 判定后第 27~56 天这 30 天的销量（去掉那期测出的大促日，有 ≥20 天的数才算，
按 30 天折算），≥ 判定时近 30 天的 boom_ratio 倍且 ≥ boom_units 件算卖爆。到期还没出结果的下期必看（tracking.watchlist）；
到期后 OUT_WAIT 天还没有第 56 天日数据的（断货、下架后卖家精灵的日数据停在最后在售那天，或一直没拉到）不再等，
记成停更、不算进比例（回测样本的日数据都到了第 56 天以后），报告里写个数。
重新生成本期时先去掉本期记的再按现在的规则重记。报告潜力SKU 小窗、钉钉里写已出结果的个数和比例，和回测的范围放在一起。

2026-11-19 边界复核（用户：不要为了改而改，多一些边界代码）改的三处：按显示的两位小数分档（classify）、
实盘等修订完再算、停更的不再一直等（OUT_SETTLE / OUT_WAIT）；“近30天≥600件”按表格那一列的近 30 天算（report.render.sku_band_text）。
查过、不用改的：同一父体的几个子体都在追踪里的 0 个（池子按父体去重，实盘也没有同父体两条）；
在售日均算不出、近 28 天却有量的增长商品 0 个；日数据停更超过 stale_days 的本来就不判增长（不会把正在断货的列进来）；
近 7 天去掉大促日后只剩 ≤3 天的只有 Prime Day 那一周（回测一周翻倍 11 次 9.1%、一周涨五成 55 次 16.4%），已有“近7天含大促日”标签。"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

from .. import clock
from ..series import Daily

TOP, MID = "一周翻倍", "一周涨五成"
KIND_CN = {"surge": "突然爆火", "potential": "新品潜力", "rising": "上升中"}
DEFAULTS = {"top_a7": 2.0, "min_a7": 1.5, "node_min": 10, "node_min_units": 60, "live_min": 30}
PROMO = "近7天含大促日"  # 风险标签的短名（表格、下载的 Excel、钉钉）
UNVER, PRICE, RELIST = "月度增长尚未验证", "降价驱动", "老品换链接"
WEAK_MID = (UNVER, PRICE)  # 一周涨五成不收：回测里和不列的一样（文件说明 ③）
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
    """近 28 天在售日的日均（断货的日子不算，去掉大促日；analyze 的 instock_avg28）：加速倍数和卖爆的基数。
    没有这个字段的（旧数据、测试里的商品）用近 28 天日均。"""
    return item["instock_avg28"] if "instock_avg28" in item else item.get("recent_avg28")


def _tagged(item: dict, word: str) -> bool:
    return any(word in tag for tag in item.get("tags") or [])


def node_pace(items: list[dict], s: dict) -> dict[str, float]:
    """{子类目: 这周整体加速多少}：本期追踪的同子类目商品（有日数据、不陈旧、近 28 天在售日均 × 30 ≥ node_min_units 件）
    各自近 7 天 ÷ 近 28 天在售日均的中位数；不到 node_min 个的子类目不算。量的下限同回测样本（判定时近 30 天 ≥60 件）。"""
    by: dict[str, list[float]] = {}
    for item in items:
        a28 = base_avg(item)
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
    """这个商品算不算潜力SKU：返回 {tier, kind, a7, net, pace, a14, promo, flags}，不算的返回 None。s = settings(cfg)。
    a7 = 近 7 天 ÷ 近 28 天在售日均（表格里显示的）；pace = 所在子类目这周整体的倍数（大于 1 才有，见 node_pace）；
    net = a7 ÷ pace（没有 pace 时就是 a7），按它分档、排序。promo = 近 7 天里的大促日（风险标签 PROMO），没有的是 None。
    一周涨五成里带 WEAK_MID 标签的不收；flags = 照收但要写明的：一周翻倍里的“月度增长尚未验证”、老品换链接。"""
    growth = growth_kind(item)
    if not growth or item.get("ocean") or item.get("stale") or item.get("daily") is False:
        return None
    a28 = base_avg(item)
    a7 = _ratio(item.get("last7_avg"), a28)
    if a7 is None:
        return None
    tide = (pace or {}).get(item.get("node") or "")
    tide = tide if tide and tide > 1 + 1e-9 else None
    # 按显示的两位小数分档、对段。以前按没取整的分：×1.996 显示 ×2.00 却在一周涨五成、对的是 ×2~2.5 那段的回测数，
    # ×1.4952 显示 ×1.50 却不收（回测两档里 0.58% 跨线）
    net = round(a7 / tide if tide else a7, 2)
    if net < float(s["min_a7"]) - 1e-9:
        return None
    tier = TOP if net >= float(s["top_a7"]) - 1e-9 else MID
    if tier == MID and any(_tagged(item, w) for w in WEAK_MID):
        return None
    a14 = _ratio(item.get("recent_avg14"), a28)
    flags = [UNVER] if _tagged(item, UNVER) else []
    if item.get("relist"):
        flags.append(RELIST)
    return {"tier": tier, "kind": KIND_CN[growth],
            "a7": round(a7, 2), "net": net, "pace": tide, "a14": None if a14 is None else round(a14, 2),
            "promo": promo_days(item.get("as_of"), events) or None, "flags": flags}


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
    return f"{sku.get('kind', '')}；近 7 天日均是近 28 天在售日均的 ×{sku['a7']:.2f}{pace}{tail}"


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


def band(sku: dict, cfg: dict) -> tuple[float, float] | None:
    """回测里加速倍数（扣过子类目，net）在同一段的后续卖爆：(前段, 后段)，取自 backtest.bands（[下限, 前段, 后段]，从小到大）。"""
    net = sku.get("net", sku.get("a7"))
    hit = None
    for low, front, back in (settings(cfg).get("backtest") or {}).get("bands") or []:
        if net is not None and net >= float(low) - 1e-9:
            hit = (float(front), float(back))
    return hit


def risk_notes(sku: dict, cfg: dict) -> list[str]:
    """表格风险格的说明（鼠标停留）：近7天含大促日、月度增长尚未验证（一周翻倍里的）、老品换链接，各写回测数。"""
    bt = settings(cfg).get("backtest") or {}
    out = [promo_note(sku, cfg)] if sku.get("promo") else []
    flags = sku.get("flags") or []
    if UNVER in flags:
        u = bt.get("unver_top")
        out.append("月度增长尚未验证：新品完整月份还不够，看不出月度在涨"
                   + (f"；回测里一周翻倍的这类后续卖爆 {u[0]:.0%}，其他 {u[1]:.0%}" if u else ""))
    if RELIST in flags:
        r = bt.get("relist")
        out.append("老品换链接：新链接还在起量、基数低，后续卖爆多半是回到老链接原来的量"
                   + (f"；回测里这类一周翻倍的 {r[0]:.0%}、一周涨五成的 {r[1]:.0%} 后续卖爆，不算进两档的比例" if r else ""))
    return out


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
    到了但有数的不到 20 天的 f 是 None（数据不全，不算进比例）。units = 判定时的近 30 天销量（在售日均 × 30）。
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
            "done": {t: [sum(1 for p in done if p["tier"] == t), sum(1 for p in done if p["tier"] == t and p["boom"])]
                     for t in (TOP, MID)},
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

"""品类季节：用卖家精灵的子类目月度销量（market_research，整个子类目所有商品的合计，3 年历史，见 category_history.py）
识别哪些品类有固定的季节、旺季在几月，按往年规律推算接下来几个月，并把季节从“增长”里分出来。

以前用追踪商品的日数据（只有 13 个月、都是现在还在追踪的商品）当季节曲线，季节和品类自己的涨跌分不开；
拿它调整持续热销，不含旺季的窗口平白少了 12 个、含旺季的窗口只找回 3~4 个，所以换成类目历史。

所有数字都来自卖家精灵：类目月度销量、追踪商品的日数据，不用人工填的日期或系数。
  （试过按公开的往年 Prime Day / 黑五日期把大促从月销量里扣掉，检验误差只从 0.106 降到 0.103，不值得为它引入非卖家精灵的数据；
  卖家精灵的 keepa_info 每日 BSR 历史也认不准往年大促日：圣诞后一周、1 月中旬的排名波动和大促一样大。
  所以季节指数里含着每年固定时间的大促；Prime Day 的月份会变（2023~2025 年 7 月、2026 年 6 月），7 月的指数可能偏高。）
季节指数（每个子类目、每个月份一个数，12 个月平均 = 1）：
  每个月的日均销量 ÷ 以它为中心的 12 个月移动平均（2×12 中心化，去掉品类自己这几年的涨跌），
  同一个月份各年的比值取中位数，再归一到 12 个月平均为 1。
季节性品类（三条都满足）：
  ① 类目历史 ≥ min_months 个月；
  ② 每年一样：从最近一个月往前每 12 个月切一段，每段除以自己的平均，各段两两的相关系数平均 ≥ min_consistency
     （实测梳妆台 0.96、衣帽架 0.95；计算机工作站、文件柜三年的起伏互不相关，算不出季节）；
  ③ 起伏够大：季节指数最高的月份 ≥ 最低月份的 min_amplitude 倍；
  ④ 能用来预测：按这个规律回测（见下面“预测”）的误差中位数 ≤ max_error（实测办公货架每年 8 月都冲到 4.5 倍、形状相关 0.94，
     但回测误差 54%，规律靠不住）。
  不是季节性品类的子类目不做任何季节判断、不调整。
旺季月 = 季节指数 ≥ peak_month；淡季月 = 季节指数 ≤ low_month。
某一天的指数 = 相邻两个月中点之间线性插值；一段日子的指数 = 这些天的平均。
按往年规律的倍数（28 天这样的短窗口用）= (这段日子的指数 ÷ 那段日子的指数) ^ k（只是季节，不含品类自己的涨跌）。
  月度指数摊到 28 天会夸大，k 每期用卖家精灵数据现算（calibrate）：过去一年每 14 天一个日期，季节性品类的指数之比 vs
  该子类目追踪商品实际的 28 天变化（中位数），k = ln(实际) ÷ ln(指数之比) 的中位数；样本不够时用 ratio_shrink。
  2026-10 的数据：前半年拟合 k = 0.60，后半年检验对数误差中位数 不打折 0.116 → 0.106（不考虑季节 0.111）。
  按月的预测（开发方向的“到货前高峰”“上架后”、持续热销的调整）不打折，那是按月回测过的。
预测 = 最近 3 个月按季节指数还原后的平均水平 × 目标月份的季节指数。回测（2026-10，28 个子类目，
  预测起点 2025-09~2026-03、提前 1~6 个月）误差中位数 10~12%，按现在水平外推是 12~16%；每个子类目给出自己的回测误差。
"""
from __future__ import annotations

import math
import statistics
from datetime import date, timedelta


def month_key(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def _add_months(key: str, n: int) -> str:
    y, m = int(key[:4]), int(key[5:]) - 1 + n
    return f"{y + m // 12}-{m % 12 + 1:02d}"


def _month_days(key: str) -> int:
    y, m = int(key[:4]), int(key[5:])
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return (nxt - date(y, m, 1)).days


def _daily(units: dict[str, float]) -> dict[str, float]:
    """月销量 → 日均（各月天数不同）。"""
    return {k: v / _month_days(k) for k, v in units.items() if v is not None and v > 0}


def seasonal_index(units: dict[str, float]) -> dict[int, float] | None:
    """比率移动平均法（模块说明）。12 个月份都至少有一个比值才返回。"""
    x = _daily(units)
    ratios: dict[int, list[float]] = {}
    for key in sorted(x):
        window = [_add_months(key, k) for k in range(-6, 7)]
        if not all(w in x for w in window):
            continue
        ma = (0.5 * x[window[0]] + sum(x[w] for w in window[1:12]) + 0.5 * x[window[12]]) / 12
        ratios.setdefault(int(key[5:]), []).append(x[key] / ma)
    if len(ratios) < 12:
        return None
    s = {m: statistics.median(v) for m, v in ratios.items()}
    mu = statistics.mean(s.values())
    return {m: round(v / mu, 4) for m, v in sorted(s.items())}


def consistency(units: dict[str, float]) -> tuple[float | None, int]:
    """(各年 12 个月形状两两相关系数的平均, 完整年数)。从最近一个月往前每 12 个月切一段。"""
    x = _daily(units)
    if not x:
        return None, 0
    end = max(x)
    years = []
    while True:
        keys = [_add_months(end, -k) for k in range(11, -1, -1)]
        if not all(k in x for k in keys):
            break
        values = [x[k] for k in keys]
        mu = statistics.mean(values)
        # 按月份对齐：同一段的 12 个月从同一个月份开始，所以直接按位置比
        years.append([v / mu for v in values])
        end = _add_months(end, -12)
    if len(years) < 2:
        return None, len(years)
    pairs = [statistics.correlation(years[i], years[j]) for i in range(len(years)) for j in range(i + 1, len(years))
             if statistics.pstdev(years[i]) > 0 and statistics.pstdev(years[j]) > 0]
    return (round(statistics.mean(pairs), 3) if pairs else None), len(years)


def profile(units: dict[str, float] | None, cfg: dict) -> dict:
    """一个子类目的季节画像。cfg = thresholds.seasonality。"""
    units = {k: v for k, v in (units or {}).items() if v}
    out = {"months": len(units), "seasonal": False}
    if len(units) < int(cfg["min_months"]):
        return out
    idx = seasonal_index(units)
    cons, years = consistency(units)
    if not idx:
        return out
    amp = max(idx.values()) / min(idx.values()) if min(idx.values()) > 0 else None
    error = forecast_error(units)
    seasonal = bool(cons is not None and cons >= float(cfg["min_consistency"]) - 1e-9
                    and amp is not None and amp >= float(cfg["min_amplitude"]) - 1e-9
                    and error is not None and error <= float(cfg["max_error"]) + 1e-9)
    out.update({
        "index": idx, "consistency": cons, "years": years, "amplitude": None if amp is None else round(amp, 2),
        "seasonal": seasonal,
        "peak_months": [m for m, v in idx.items() if v >= float(cfg["peak_month"]) - 1e-9],
        "low_months": [m for m, v in idx.items() if v <= float(cfg["low_month"]) + 1e-9],
        "latest": max(units),
        "error": error,
        "shrink": float(cfg.get("ratio_shrink", 1.0)),
    })
    return out


def calibrate(histories: dict[str, dict[str, float]], dailies: dict[str, list], as_of: date, cfg: dict,
              steps: int = 26, every: int = 14) -> dict:
    """现算 28 天季节倍数的折扣 k（模块说明）。histories：子类目 → {月份: 月销量}；dailies：子类目 → 追踪商品的日数据
    （已去掉大促日）。过去 steps 个日期（每 every 天一个），每个日期只用它之前 history_months 个月的类目历史判断季节性品类；
    实际 = 该子类目追踪商品 近 28 天日均 ÷ 之前 28 天日均 的中位数（之前日均 ≥3 件、≥4 个商品）。
    k = 各样本 ln(实际) ÷ ln(指数之比) 的中位数（只用指数之比 ≥1.05 或 ≤1/1.05 的样本），限制在 0~1。
    返回 {"k", "n"（样本数）, "fitted"（样本够不够、是不是现算的）}。"""
    pairs = []
    # 只用每个日期以前的类目历史（不偷看未来）；线上只保留 history_months 个月，一年前的日期以前只有 24~35 个月，
    # 所以校准时季节性品类的门槛放到 calibrate_min_months（报告里判季节性品类仍要 min_months）
    cal_cfg = {**cfg, "min_months": int(cfg.get("calibrate_min_months", 24))}
    for j in range(1, steps + 1):
        d0 = as_of - timedelta(days=every * j)
        first = date(d0.year, d0.month, 1)
        keep = {_add_months(month_key(first), -k) for k in range(1, int(cfg["history_months"]) + 1)}
        for path, units in histories.items():
            items = dailies.get(path) or []
            if len(items) < 4:
                continue
            prof = profile({m: v for m, v in units.items() if m in keep}, cal_cfg)
            if not prof.get("seasonal"):
                continue
            recent = (d0 - timedelta(days=27), d0)
            base = (d0 - timedelta(days=55), d0 - timedelta(days=28))
            e = window_index(prof["index"], *recent) / window_index(prof["index"], *base)
            ratios = []
            for d in items:
                i = d.index_of(d0)
                if i < 55 or i >= len(d.sales):
                    continue
                now = [v for v in d.sales[i - 27:i + 1] if v is not None]
                before = [v for v in d.sales[i - 55:i - 27] if v is not None]
                if now and before and statistics.mean(before) >= 3:
                    ratios.append(statistics.mean(now) / statistics.mean(before))
            if len(ratios) >= 4 and e > 0:
                pairs.append((e, statistics.median(ratios)))
    # 只用季节确实有起伏的样本（指数之比 ≥1.05 或 ≤1/1.05）：大半年指数之比≈1，那些样本对任何 k 误差都≈0，拟合不出东西
    slopes = [math.log(a) / math.log(e) for e, a in pairs if a > 0 and abs(math.log(e)) >= math.log(1.05)]
    if len(slopes) < int(cfg.get("calibrate_min_pairs", 30)):
        return {"k": float(cfg["ratio_shrink"]), "n": len(slopes), "fitted": False}
    k = min(1.0, max(0.0, statistics.median(slopes)))
    return {"k": round(k, 2), "n": len(slopes), "fitted": True}


def day_index(idx: dict[int, float], day: date) -> float:
    """某一天的季节指数：相邻两个月中点之间线性插值（跨年循环）。"""
    mid = date(day.year, day.month, 1) + timedelta(days=_month_days(month_key(day)) / 2)
    if day >= mid:
        other = date(day.year + (day.month == 12), day.month % 12 + 1, 1)
    else:
        prev_last = date(day.year, day.month, 1) - timedelta(days=1)
        other = date(prev_last.year, prev_last.month, 1)
    other_mid = other + timedelta(days=_month_days(month_key(other)) / 2)
    span = abs((other_mid - mid).days) or 1
    w = abs((day - mid).days) / span
    return idx[day.month] * (1 - w) + idx[other.month] * w


def window_index(idx: dict[int, float], lo: date, hi: date) -> float:
    days = (hi - lo).days + 1
    return statistics.mean(day_index(idx, lo + timedelta(days=k)) for k in range(days))


def expected_ratio(prof: dict | None, recent: tuple[date, date], base: tuple[date, date]) -> float | None:
    """按往年规律，recent 这段比 base 那段品类会涨几倍（只看季节，按 ratio_shrink 打折）。不是季节性品类返回 None。"""
    if not prof or not prof.get("seasonal"):
        return None
    idx = prof["index"]
    raw = window_index(idx, *recent) / window_index(idx, *base)
    return round(raw ** prof.get("shrink", 1.0), 3)


def forecast(units: dict[str, float], idx: dict[int, float], target: str, upto: str | None = None) -> float | None:
    """target 月的日均销量预测：最近 3 个月（截至 upto）按季节指数还原后的平均水平 × target 月份的指数。"""
    x = _daily(units)
    hist = sorted(k for k in x if upto is None or k <= upto)[-3:]
    if len(hist) < 3:
        return None
    level = statistics.mean(x[k] / idx[int(k[5:])] for k in hist)
    return level * idx[int(target[5:])]


def forecast_error(units: dict[str, float], origins: int = 7, horizon: int = 6) -> float | None:
    """回测：在最近 origins 个月的每个月底，只用当时以前的数据（季节指数也重算）预测之后 1~horizon 个月，
    误差 = |预测 − 实际| ÷ 实际，取中位数。数据不够时返回 None。"""
    x = _daily(units)
    keys = sorted(x)
    errs = []
    for back in range(1, origins + 1):
        if len(keys) <= back:
            break
        origin = keys[-1 - back]
        idx = seasonal_index({k: units[k] for k in keys if k <= origin})
        if not idx:
            continue
        for h in range(1, horizon + 1):
            target = _add_months(origin, h)
            if target not in x:
                continue
            f = forecast(units, idx, target, upto=origin)
            if f:
                errs.append(abs(f - x[target]) / x[target])
    return round(statistics.median(errs), 3) if errs else None


def outlook(prof: dict | None, as_of: date, cfg: dict) -> dict | None:
    """开发方向用：现在（近 28 天）的季节位置、本期增长里的季节倍数、到货前的高峰、上架后的水平、接下来各月。
    不是季节性品类返回 {"seasonal": False, ...}。"""
    if not prof or not prof.get("index"):
        return {"seasonal": False, "months": (prof or {}).get("months", 0), "ready": False} if prof is not None else None
    if not prof.get("seasonal"):
        cons, amp, err = prof.get("consistency"), prof.get("amplitude"), prof.get("error")
        if cons is None or cons < float(cfg["min_consistency"]):
            why = f"每年的起伏不一致（形状相关 {cons:.2f}）" if cons is not None else "每年的起伏不一致"
        elif amp is None or amp < float(cfg["min_amplitude"]):
            why = f"一年里起伏不大（最高月只是最低月的 {amp:.1f} 倍）" if amp is not None else "一年里起伏不大"
        else:
            why = f"按往年规律预测不准（过去的误差中位数 {err:.0%}）" if err is not None else "历史不够回测"
        return {**{k: prof.get(k) for k in ("months", "consistency", "amplitude", "years", "error")}, "seasonal": False,
                "ready": True, "why": why}
    idx = prof["index"]
    now_lo, now_hi = as_of - timedelta(days=27), as_of
    now = window_index(idx, now_lo, now_hi)
    lead, launch_days = int(cfg["lead_days"]), int(cfg["launch_days"])
    launch_lo = as_of + timedelta(days=lead)
    launch_hi = launch_lo + timedelta(days=launch_days - 1)
    curve = []
    key = month_key(as_of)
    for k in range(1, 7):
        m = _add_months(key, k)
        curve.append([m, round(idx[int(m[5:])] / now, 2)])
    before = []
    m = _add_months(key, 1)
    while date(int(m[:4]), int(m[5:]), 1) + timedelta(days=_month_days(m) - 1) < launch_lo:
        before.append([m, round(idx[int(m[5:])] / now, 2)])
        m = _add_months(m, 1)
    peak = max(before, key=lambda r: r[1]) if before else None
    launch = window_index(idx, launch_lo, launch_hi) / now
    growth = expected_ratio(prof, (now_lo, now_hi), (now_lo - timedelta(days=28), now_lo - timedelta(days=1)))
    return {
        "seasonal": True, "ready": True,
        **{k: prof.get(k) for k in ("months", "consistency", "amplitude", "years", "peak_months", "low_months", "error")},
        "now": round(now, 3), "growth": growth,
        "growth_seasonal": growth is not None and growth >= float(cfg["tag_ratio"]) - 1e-9,
        "peak": {"month": peak[0], "ratio": peak[1]} if peak else None,
        "peak_before": bool(peak and peak[1] >= float(cfg["peak_ratio"]) - 1e-9),
        "launch": round(launch, 2),
        "launch_state": ("busier" if launch >= float(cfg["busier"]) - 1e-9
                         else "quieter" if launch <= float(cfg["quieter"]) + 1e-9 else "same"),
        "curve": curve, "lead_days": lead, "launch_days": launch_days,
    }


def hot_levels(prof: dict | None, as_of: date, months: int = 8) -> dict | None:
    """持续热销按季节调整用（rules.evaluate_hot 的 season 参数）：最近 months 个已结束月份各自的季节指数，
    和近 28 天的指数。不是季节性品类返回 None（不调整）。"""
    if not prof or not prof.get("seasonal"):
        return None
    idx = prof["index"]
    first = date(as_of.year, as_of.month, 1)
    levels = {}
    key = month_key(first)
    for k in range(1, months + 1):
        m = _add_months(key, -k)
        levels[m] = idx[int(m[5:])]
    return {"months": levels, "recent": window_index(idx, as_of - timedelta(days=27), as_of),
            "n": prof.get("years")}


def month_cn(key: str) -> str:
    """“2025-12” → “12 月”。"""
    return f"{int(key[5:7])} 月"


def months_text(months: list[int]) -> str:
    """[1, 3, 4, 5, 6, 8, 9, 10] → “1、3~6、8~10 月”；跨年的连续段写成 “11~2 月”。"""
    if not months:
        return ""
    have = set(months)
    if len(have) == 12:
        return "全年"
    # 从一个“前一个月不在里面”的月份开始，按月往后连成段（跨年循环）
    start = next(m for m in range(1, 13) if m in have and ((m - 2) % 12) + 1 not in have)
    runs, run = [], []
    for k in range(12):
        m = (start - 1 + k) % 12 + 1
        if m in have:
            run.append(m)
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    return "、".join(f"{r[0]}" if len(r) == 1 else f"{r[0]}~{r[-1]}" for r in runs) + " 月"


def lead_text(days: int) -> str:
    months = days / 30
    return f"{months:.0f} 个月" if abs(months - round(months)) < 0.15 else f"{days} 天"


def verdicts(o: dict | None) -> list[str]:
    """一个子类目的季节结论（报告和文字简报共用），每条对应模块说明里的一个条件。"""
    if not o:
        return []
    if not o.get("ready"):
        return [f"类目历史只有 {o.get('months', 0)} 个月，还看不出季节"]
    if not o.get("seasonal"):
        return [f"没有固定的季节：{o.get('why') or '每年的起伏不一致'}，不做季节判断"]
    out = [f"季节性品类：旺季 {months_text(o['peak_months'])}" + (f"、淡季 {months_text(o['low_months'])}" if o["low_months"] else "")
           + f"（每年形状相关 {o['consistency']:.2f}）"]
    if o.get("growth") is not None:
        out.append(f"本期增长有季节因素：按往年规律，近 28 天品类本来就是之前 28 天的 {o['growth']:.1f} 倍" if o["growth_seasonal"]
                   else f"近 28 天季节变化不大：按往年规律，品类是之前 28 天的 {o['growth']:.1f} 倍")
    if o.get("peak_before"):
        out.append(f"到货前品类会先冲高：按往年规律 {month_cn(o['peak']['month'])}是现在的 {o['peak']['ratio']:.1f} 倍，"
                   f"按开发周期 {lead_text(o['lead_days'])}赶不上")
    launch = {"busier": "比现在更旺", "quieter": "转淡", "same": "和现在差不多"}[o["launch_state"]]
    out.append(f"上架后 {lead_text(o['launch_days'])}品类{launch}（按往年规律是现在的 {o['launch']:.1f} 倍）")
    if o.get("error") is not None:
        out.append(f"按这个规律预测 1~6 个月后，过去的误差中位数 {o['error']:.0%}")
    return out

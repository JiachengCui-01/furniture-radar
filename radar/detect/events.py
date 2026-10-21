"""大促日：全类目一起冲高的日子。它们会把前后几周的判断带偏，所以日数据比较时去掉这些天。

实测（卖家精灵日数据，追踪中的约 150 个商品）：2025-10-07/08 七成商品、2026-06-23~26 六成商品当天销量翻倍，
全部商品的销量合计也翻倍，第二天就回到原来水平；回放当时的判定，大促刚过时冒出一批“上升中”，
之后两三周每期有 12~26 个商品被判成“最近回落”（平时约 3 个，去掉大促日后 3~8 个），真正在涨的反而看不出来。

大促日只从卖家精灵的日数据里认（所有判定的数据都来自卖家精灵）：
  自动识别：某一天，追踪商品里 ≥ core_share 的商品当天销量达到自己前 base_days 天中位数的 jump 倍
  （只看那段中位数 ≥ min_base_daily 件的商品，而且这样的商品要有 ≥ min_items 个）；
  和这样的日子相连、比例 ≥ edge_share 的日子也算（大促前后常各带一天）。
平时这个比例中位数约 4%，最高约 26%（黑五前一周），所以 core_share 默认 30%。
config.yaml 的 deal_windows 只用来给认出来的日子起名字，以及数据里那天也有 ≥ edge_share 的商品冲高时补上
（实测 2026-10-06/07 Prime Big Deal Days 当天冲高的商品不到 6%，和平时一样，不算大促日）；
数据看不出来的日子（够格的商品不足 min_items 个）不按登记的算。
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from .. import clock
from ..series import Daily


def daily_shares(dailies: list[Daily], cfg: dict) -> dict[date, float]:
    """每一天：当天销量 ≥ 自己前 base_days 天中位数 jump 倍的商品占比（够格的商品不足 min_items 个的日子不算）。"""
    base_days, jump = int(cfg["base_days"]), float(cfg["jump"])
    min_base, min_items = float(cfg["min_base_daily"]), int(cfg["min_items"])
    need = max(1, int(base_days * 0.7))  # 前 base_days 天至少七成有数据
    tally: dict[date, list[int]] = {}
    for d in dailies:
        sales = d.sales
        for i in range(base_days, len(sales)):
            value = sales[i]
            if value is None:
                continue
            prior = [v for v in sales[i - base_days:i] if v is not None]
            if len(prior) < need:
                continue
            base = statistics.median(prior)
            if base < min_base:
                continue
            row = tally.setdefault(d.day(i), [0, 0])
            row[0] += 1
            row[1] += value >= jump * base
    return {day: hits / total for day, (total, hits) in tally.items() if total >= min_items}


def configured_days(deal_windows: list[dict]) -> dict[date, str]:
    out: dict[date, str] = {}
    for deal in deal_windows or []:
        lo, hi = clock.parse_day(deal.get("start")), clock.parse_day(deal.get("end"))
        if not lo or not hi:
            continue
        day = lo
        while day <= hi:
            out.setdefault(day, str(deal.get("name") or "大促"))
            day += timedelta(days=1)
    return out


def detect(dailies: list[Daily], cfg: dict, deal_windows: list[dict]) -> dict:
    """返回 {"days": frozenset(大促日), "spans": [{"start", "end", "name", "auto"}]}。
    spans 只列和这些商品的日数据有重叠的段，报告“数据说明”里展示。"""
    named = configured_days(deal_windows)
    auto: set[date] = set()
    edge = float(cfg["edge_share"])
    if cfg.get("auto", True) and dailies:
        shares = daily_shares(dailies, cfg)
        # 登记的日期只在数据里那天也冲高时才算；数据看不出来（不在 shares 里）的不算
        named = {day: name for day, name in named.items() if shares.get(day, 0.0) >= edge - 1e-9}
        core = {day for day, share in shares.items() if share >= float(cfg["core_share"]) - 1e-9}
        auto = set(core)
        for day in sorted(core):
            for step in (-1, 1):
                nxt = day + timedelta(days=step)
                while nxt not in auto and shares.get(nxt, 0) >= edge - 1e-9:
                    auto.add(nxt)
                    nxt += timedelta(days=step)
    days = frozenset(auto | set(named))

    spans: list[dict] = []
    if dailies:
        first = min(d.start for d in dailies)
        last = max((d.as_of() for d in dailies if d.as_of()), default=None)
        for day in sorted(days):
            if spans and day == spans[-1]["end"] + timedelta(days=1):
                spans[-1]["end"] = day
            else:
                spans.append({"start": day, "end": day})
        spans = [s for s in spans if last is None or (s["end"] >= first and s["start"] <= last)]
        for s in spans:
            inside = [s["start"] + timedelta(days=k) for k in range((s["end"] - s["start"]).days + 1)]
            names = list(dict.fromkeys(named[day] for day in inside if day in named))
            s["name"] = "、".join(names) if names else "自动识别"
            s["auto"] = any(day in auto for day in inside)
    return {"days": days, "spans": [{**s, "start": s["start"].isoformat(), "end": s["end"].isoformat()}
                                    for s in spans]}


def span_text(span: dict) -> str:
    """2025-10-07~10-08（自动识别）。"""
    start, end = span["start"], span["end"]
    when = start if start == end else f"{start}~{end[5:] if end[:4] == start[:4] else end}"
    return f"{when}（{span['name']}）"

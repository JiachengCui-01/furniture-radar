"""把每个追踪中的 ASIN 跑一遍全部判定，得到带标签的结果列表。纯计算，不调接口。"""
from __future__ import annotations

from datetime import date, timedelta

from . import category_history, clock
from .detect import events as events_mod
from .detect import fakehot, seasonality, traits
from .detect.design import main_material
from .discovery import clean_text
from .detect.rules import (current_rating, evaluate_hot, evaluate_potential, evaluate_surge, low_rating,
                           momentum, rating_floor, relist_text, relisted)
from .series import Daily, closed_months, median

def display_name(brand: str, title: str) -> str:
    """标题通常以品牌开头，避免出现“Avenco Avenco SleepSphere”。"""
    brand, title = (brand or "").strip(), (title or "").strip()
    if not brand or title.lower().startswith(brand.lower()):
        return title
    return f"{brand} {title}"


LABEL_CN = {"surge": "突然爆火", "potential": "潜力", "hot": "持续热销", "fake": "异常信号", "watch": "观察",
            "low": "评分偏低"}
# 不进入任何榜单和外观分析样本的标签
UNLISTED = ("fake", "low")


def node_stats(disc: dict | None, cfg: dict) -> dict[str, dict]:
    rank = int(cfg["thresholds"]["hot"]["rank_in_node"])
    by_node: dict[str, list[dict]] = {}
    for p in ((disc or {}).get("products") or {}).values():
        by_node.setdefault(p["node"], []).append(p)
    stats = {}
    for node, items in by_node.items():
        per_parent: dict[str, float] = {}
        for p in items:
            if "top" in p["sources"]:
                key = p.get("parent") or p["asin"]
                per_parent[key] = max(per_parent.get(key, 0), p["units"] or 0)
        units = sorted(per_parent.values(), reverse=True)
        threshold = (units[rank - 1] if len(units) >= rank else units[-1]) if units else None
        # 留评率的中位数只算变体不多的商品：和打分时同一批（变体多的评论是父体共享的，不算留评率）
        max_vars = cfg["thresholds"]["fake"]["max_variations_for_ratio"]
        stats[node] = {
            "median_rate": median([p.get("ratings_rate") for p in items
                                   if p.get("variations") is None or p["variations"] <= max_vars]),
            "threshold": threshold,
            "count": len(items),
        }
    return stats


def _last(values: list):
    for v in reversed(values or []):
        if v is not None:
            return v
    return None


def _judge_growth(d: Daily | None, idx: int | None, as_of: date | None, rec: dict, st: dict, th: dict,
                  stale: bool, relist: dict | None = None) -> dict:
    """突然爆火（及断货恢复 / 新品起量 / 形态不稳）、潜力、上升中、回落：都在同一份日数据上判断。
    relist：老品换链接（rules.relisted）——上架天数不代表商品是新的，不算潜力、新品爆发、新品起量。"""
    oos = th.get("out_of_stock_bsr")
    surge = evaluate_surge(d, idx, th["surge"], oos) if idx is not None else None
    restock = None
    if surge and surge.get("restock"):
        restock, surge = surge, None
    if surge and surge.get("from_zero") and relist:
        surge = None  # “从零起量”只是新子体开始卖，销量是从老链接转过来的
    new_ramp = None
    if surge and surge.get("from_zero"):
        sc = th["surge"]
        node_daily = (st.get("threshold") or 0) / 30
        need = max(sc["new_listing_min_daily"], sc["new_listing_node_share"] * node_daily)
        if surge["recent_avg"] < need:
            new_ramp, surge = surge, None
    unshaped = None
    if surge and not surge["kind"]:  # 不符合任何一种爆火形态
        unshaped, surge = surge, None
    potential = evaluate_potential(rec, d, as_of, th["potential"], oos) if idx is not None and not relist else None
    if stale:  # 日数据停更（下架/断货/数据延迟）时不做“正在爆/正在涨”的判断
        surge = potential = None
        new_ramp = None
    rising = None
    if idx is not None and not stale and not surge and not restock:
        rcfg = th.get("rising") or {}
        avg28, prev28 = _avg(d, idx, 28), _avg(d, idx - 28, 28, oos)
        if avg28 and prev28 and avg28 >= rcfg.get("min_daily_sales", 3) \
                and avg28 / prev28 >= rcfg.get("min_ratio", 1.3):
            rising = {"ratio": round(avg28 / prev28, 2), "avg28": avg28}
    # 短时脉冲把近 28 天日均撑高了，不是持续的增长：也不算潜力 / 上升中
    if unshaped and unshaped.get("pulse"):
        potential = rising = None
    # 有过上涨、但最近在回落的，不算爆火 / 潜力 / 上升中
    mom = momentum(d, idx, th.get("momentum") or {}) if idx is not None else None
    faded = bool(mom and mom["fading"] and (surge or potential or rising))
    if faded:
        surge = potential = rising = None
    return {"surge": surge, "restock": restock, "new_ramp": new_ramp, "unshaped": unshaped,
            "potential": potential, "rising": rising, "mom": mom, "faded": faded}


def evaluate(asin: str, rec: dict, disc_row: dict | None, stats: dict, cfg: dict, today: date, *,
             data_date: date | None = None, events=frozenset(), season: dict | None = None,
             threshold_month: str | None = None) -> dict:
    """data_date：本期日数据的最新日期（所有追踪商品里最晚的一天），“数据陈旧”和它比；
    events：大促日（detect/events.py），爆火 / 潜力 / 上升中 / 回落 / 季节性 / 持续热销都在去掉这些天的日数据上判断；
    season：所在子类目最近几个月的季节指数（seasonality.hot_levels，不是季节性品类为 None）：
            原始销量不达持续热销的，再按季节调整后判一次（调整只会多收、不会少收）；
    threshold_month：子类目门槛线（第 N 名的月销量）取自哪个月（月度名单的月份）。"""
    th = cfg["thresholds"]
    full = Daily.from_record(rec.get("series"))
    as_of = full.as_of() if full else None
    idx = full.index_of(as_of) if full and as_of else None
    node = rec.get("node") or (disc_row or {}).get("node")
    st = stats.get(node, {})
    stale = as_of is None or ((data_date or today) - as_of).days > th["stale_days"]
    d = full.without(events) if full is not None and events else full
    rating, n_ratings = current_rating(rec, disc_row)
    age = clock.days_between(clock.parse_day(rec.get("available")), as_of or today)
    relist = relisted(full, idx, n_ratings, age, th["relist"]) if th.get("relist") else None

    g = _judge_growth(d, idx, as_of, rec, st, th, stale, relist)
    # 大促拉动：近 56 天里有大促日、这个商品自己在大促日冲高了，而且算上大促日能达到爆火 / 潜力 / 上升中、去掉就达不到
    deal_lift = False
    if d is not full and idx is not None and not (g["surge"] or g["potential"] or g["rising"]) \
            and _spiked_on_events(full, idx, events, th["events"]):
        g_full = _judge_growth(full, idx, as_of, rec, st, th, stale, relist)
        deal_lift = bool(g_full["surge"] or g_full["potential"] or g_full["rising"])
    surge, restock, new_ramp, unshaped = g["surge"], g["restock"], g["new_ramp"], g["unshaped"]
    potential, rising, mom, faded = g["potential"], g["rising"], g["mom"], g["faded"]
    hot = evaluate_hot(rec.get("months") or [], d, as_of, st.get("threshold"), th["hot"])
    if not hot and season:
        hot = evaluate_hot(rec.get("months") or [], d, as_of, st.get("threshold"), th["hot"], season, threshold_month)
    fake = fakehot.score(rec, full, as_of, disc_row, st.get("median_rate"), th["fake"], events,
                         relisted=bool(relist))

    low = low_rating(rating, n_ratings, th)
    if low:  # 评分不达标：不进任何榜单，由排在后面的商品依次补位
        label = "low"
    elif fake["level"]:
        label = "fake"
    elif surge:
        label = "surge"
    elif potential:
        label = "potential"
    elif hot:
        label = "hot"
    else:
        label = "watch"

    tags: list[str] = []
    if low:
        floor = rating_floor(n_ratings, th)
        few = f"评论仅 {int(n_ratings)} 条，" if floor > th["min_rating"] else ""
        tags.append(f"{few}评分 {rating} 低于 {floor}")
    if surge:
        tags.append(surge["kind"])
        if surge["seasonal"]:
            tags.append("季节性")
        if surge["price_driven"]:
            tags.append("降价驱动")
    if rising and label in ("watch", "hot"):
        tags.append(f"上升中（近28天 ×{rising['ratio']:.1f}）")
    if faded:
        why = []
        if mom["falling"]:
            why.append(f"最近3天日均 {mom['avg3']:.1f} 件，再之前3天 {mom['prev3']:.1f} 件，近4周最高的3天 {mom['peak3']:.1f} 件")
        if mom["lower_week"]:
            why.append(f"近7天中位数 {mom['med7']:g} 件，再之前7天 {mom['prev_med7']:g} 件")
        if mom["sliding"]:
            why.append(f"近7天最高 {mom['peak_day']:g} 件，之后 {mom['slide_days']} 天没回升，最近一天 {mom['last_day']:g} 件")
        excl = "去掉大促日后，" if d is not None and any(idx - 27 <= i <= idx for i in d.masked) else ""
        tags.append(f"最近回落（{excl}{'；'.join(why)}）")
    if unshaped and not faded:
        tags.append("短时脉冲（不算爆火）" if unshaped.get("pulse") else "上涨形态不稳定（不算爆火）")
    if restock:
        tags.append("断货恢复")
    if new_ramp:
        tags.append("新品起量（未达爆火门槛）")
    if deal_lift:
        tags.append("大促拉动（去掉大促日后不达标）")
    if relist:
        tags.append(relist_text(relist))
    if hot and label != "hot":
        tags.append("长期头部")  # 符合持续热销条件，但本期归入了别的板块
    if potential and label not in ("potential",):
        tags.append("潜力")
    if label == "fake" and surge:
        tags.insert(0, "假爆火嫌疑")
    tags.extend(fake["tags"])
    if stale:
        tags.append("数据陈旧")

    obs = (rec.get("obs") or [{}])[-1]
    months = closed_months(rec.get("months") or [], as_of)
    return {
        "asin": asin,
        "title": clean_text(rec.get("title") or (disc_row or {}).get("title") or asin),
        "brand": clean_text(rec.get("brand") or (disc_row or {}).get("brand") or ""),
        "image": rec.get("image") or (disc_row or {}).get("image") or "",
        "node": node,
        "node_name": rec.get("node_name") or (disc_row or {}).get("node_name") or "",
        "node_cn": rec.get("node_cn") or (disc_row or {}).get("node_cn") or "",
        "price": (_last(full.price) if full else None) or rec.get("price"),
        "bsr": _last(full.bsr) if full else None,
        "rating": rating,
        "ratings": n_ratings,
        "variations": rec.get("variations"),
        "age_days": age,
        "as_of": as_of.isoformat() if as_of else None,
        "stale": stale,
        "label": label,
        "label_cn": LABEL_CN[label],
        "tags": tags,
        "surge": surge,
        "restock": restock,
        "rising": rising,
        "material": main_material(clean_text(rec.get("title") or (disc_row or {}).get("title") or "")),
        "hot": hot,
        "potential": potential,
        "fake": fake,
        "relist": relist,
        "spark": (full.sales[max(0, idx - 89): idx + 1] if idx is not None else []),
        "spark_start": full.day(max(0, idx - 89)).isoformat() if idx is not None else None,
        "monthly": [[k, s] for k, s in months[-12:]],
        "recent_avg7": (surge or {}).get("recent_avg") if surge else _avg(d, idx, 7),
        "recent_avg28": _avg(d, idx, 28),
        # 之前 28 天（只算在卖的日子，同“上升中”的对比期）：开发方向按增量销售额排序用
        "prev_avg28": _avg(d, idx - 28, 28, th.get("out_of_stock_bsr")) if idx is not None else None,
        "pkg": rec.get("pkg") or (disc_row or {}).get("pkg"),
        "fetched_run": rec.get("fetched_run"),
        "source": rec.get("pool_reason"),
    }


def _spiked_on_events(d: Daily, idx: int, events, cfg: dict, lookback: int = 56) -> bool:
    """近 lookback 天的大促日里，有没有哪天这个商品的销量达到自己之前 base_days 天（不含大促日）中位数的 jump 倍。"""
    base_days, jump = int(cfg["base_days"]), float(cfg["jump"])
    for day in events:
        i = d.index_of(day)
        if not 0 <= idx - i < lookback or i >= len(d.sales) or d.sales[i] is None:
            continue
        prior = [d.sales[j] for j in range(max(0, i - base_days), i)
                 if d.sales[j] is not None and d.day(j) not in events]
        if len(prior) >= base_days // 2 and d.sales[i] >= jump * max(median(prior), 1):
            return True
    return False


def _avg(d: Daily | None, idx: int | None, days: int, oos_bsr: float | None = None) -> float | None:
    """[idx-days+1, idx] 的日均销量；给了 oos_bsr 时跳过断货的日子（用于对比期）。"""
    if d is None or idx is None:
        return None
    values = d.in_stock_window("sales", idx, days, oos_bsr)
    return round(sum(values) / len(values), 2) if values else None


def analyze(state: dict, disc: dict | None, pool: list[str], cfg: dict, today: date,
            extra_rows: dict | None = None) -> dict:
    """extra_rows：发现基线以外的月度行（例如 BSR 上升榜），用于留评率等信号。
    大促日从本期追踪商品的日数据里识别（加上 config.yaml 登记的），见 detect/events.py。"""
    stats = node_stats(disc, cfg)
    products = {**(extra_rows or {}), **((disc or {}).get("products") or {})}
    recs = [(asin, state["asins"][asin]) for asin in pool
            if (state["asins"].get(asin) or {}).get("series")]
    dailies = [d for d in (Daily.from_record(rec["series"]) for _, rec in recs) if d is not None]
    data_date = max((day for day in (d.as_of() for d in dailies) if day), default=None)
    ev = events_mod.detect(dailies, cfg["thresholds"]["events"], cfg.get("deal_windows") or [])
    th = cfg["thresholds"]
    sc = th.get("seasonality")
    # 品类季节（detect/seasonality.py）：每个子类目一份画像，数据是类目月度历史（category_history.py）
    paths = {rec.get("node") for _, rec in recs} | {n["path"] for n in (disc or {}).get("nodes") or []}
    # 大促日：日数据里识别出的 + config.yaml 登记的（含往年），类目月销量先扣掉它们的拉动
    season_events = set(ev["days"]) | set(events_mod.configured_days(cfg.get("deal_windows") or []))
    profiles = ({path: seasonality.profile(category_history.series(state, path), sc, season_events)
                 for path in paths if path} if sc else {})
    period = str((disc or {}).get("period") or "")
    threshold_month = f"{period[:4]}-{period[4:6]}" if len(period) == 6 else None

    def hot_season(rec: dict) -> dict | None:
        if not (th["hot"].get("deseasonalize") and data_date):
            return None
        return seasonality.hot_levels(profiles.get(rec.get("node")), data_date)

    items = [evaluate(asin, rec, products.get(asin), stats, cfg, today, data_date=data_date, events=ev["days"],
                      season=hot_season(rec), threshold_month=threshold_month)
             for asin, rec in recs]
    for item in items:
        _season_tag(item, profiles.get(item.get("node")), sc)
    seasons = ({path: seasonality.outlook(prof, data_date, sc) for path, prof in profiles.items()}
               if sc and data_date else {})
    return {"items": items, "node_stats": stats, "events": ev, "data_date": data_date, "seasons": seasons,
            "profiles": profiles}


def _season_tag(item: dict, prof: dict | None, sc: dict | None) -> None:
    """季节性品类里的商品：item["season28"] = 按往年规律近 28 天品类是之前 28 天的几倍（开发方向扣除季节用）；
    增长商品的对比窗口里品类本来就会涨 ≥ tag_ratio 倍的，标“品类季节性”，写明扣除季节后的倍数。"""
    item["season28"] = None
    as_of = clock.parse_day(item.get("as_of"))
    if not sc or not prof or not prof.get("seasonal") or as_of is None:
        return

    def windows(days: int) -> tuple[tuple[date, date], tuple[date, date]]:
        lo = as_of - timedelta(days=days - 1)
        return (lo, as_of), (lo - timedelta(days=28), lo - timedelta(days=1))

    item["season28"] = seasonality.expected_ratio(prof, *windows(28))
    if item["label"] == "surge" and item.get("surge"):
        ratio, expect = item["surge"].get("sales_ratio"), seasonality.expected_ratio(prof, *windows(item["surge"]["window"]))
    elif item["label"] == "potential" and item.get("potential"):
        ratio, expect = item["potential"].get("recent_ratio"), item["season28"]
    elif item.get("rising") and item["label"] in ("watch", "hot"):
        ratio, expect = item["rising"].get("ratio"), item["season28"]
    else:
        return
    if expect is not None and expect >= float(sc["tag_ratio"]) - 1e-9:
        rest = f"，扣除后 ×{ratio / expect:.1f}" if ratio else ""
        item["tags"].append(f"品类季节性（按往年规律，这段时间品类本来就是之前的 {expect:.1f} 倍{rest}）")


def _growth(item: dict) -> float:
    p = item.get("potential") or {}
    if p.get("growth") is not None:
        return p["growth"]
    return (p.get("recent_ratio") or 1) - 1


def sections(items: list[dict], top_n: int) -> dict[str, list[dict]]:
    """每个板块按排序；top_n ≤ 0 时全部列出（报告小窗默认如此，免得顶部数字和小窗里的个数对不上）。"""
    def cut(rows: list[dict]) -> list[dict]:
        return rows if top_n <= 0 else rows[:top_n]

    surge = sorted((i for i in items if i["label"] == "surge"), key=lambda i: -i["surge"]["strength"])
    fake = sorted((i for i in items if i["label"] == "fake"),
                  key=lambda i: (-i["fake"]["score"], -(i.get("recent_avg28") or 0)))
    potential = sorted((i for i in items if i["label"] == "potential"), key=lambda i: -_growth(i))
    hot = sorted((i for i in items if i["label"] == "hot"), key=lambda i: -(i["hot"]["avg_monthly"] or 0))
    return {"surge": cut(surge), "fake": cut(fake), "potential": cut(potential), "hot": cut(hot),
            "counts": {"surge": len(surge), "fake": len(fake), "potential": len(potential), "hot": len(hot),
                       # 上升中：只数不在爆火/潜力里的（爆火、潜力本来就在增长，不重复计数）
                       "rising": sum(1 for i in items if i.get("rising") and i["label"] in ("watch", "hot")),
                       "low": sum(1 for i in items if i["label"] == "low"), "total": len(items)}}


def focus_items(items: list[dict]) -> list[dict]:
    """增长商品（外观分析的样本）：突然爆火 + 潜力 + 上升中（都排除异常信号和评分偏低），按势头排序。
    只看“当期”数据，每期报告独立成立，不依赖往期积累。"""
    def strength(i: dict) -> float:
        if i.get("surge"):
            return 100 + i["surge"]["strength"]
        if i.get("potential"):
            return 50 + ((i["potential"] or {}).get("recent_ratio") or 1)
        return (i.get("rising") or {}).get("ratio") or 0

    picked = [i for i in items if i["label"] not in UNLISTED
              and (i["label"] in ("surge", "potential") or i.get("rising"))]
    return sorted(picked, key=lambda i: -strength(i))


def trait_rows(items: list[dict], disc: dict | None, state: dict) -> tuple[list[dict], list[dict]]:
    """返回 (全部头部商品, 增长商品)。"""
    products = (disc or {}).get("products") or {}
    baseline = list(products.values())
    focus = []
    for item in focus_items(items):
        row = products.get(item["asin"])
        if row is None:
            rec = state["asins"].get(item["asin"]) or {}
            row = {"asin": item["asin"], "title": item["title"], "brand": item["brand"], "node": item["node"],
                   "node_cn": item["node_cn"], "node_name": item["node_name"], "price": item["price"],
                   "rating": item["rating"], "ratings": item["ratings"], "variations": rec.get("variations"),
                   "fulfillment": rec.get("fulfillment"), "seller_nation": rec.get("seller_nation"),
                   "available": rec.get("available")}
        focus.append(row)
    return baseline, focus


def compute_traits(items: list[dict], disc: dict | None, state: dict, today: date, cfg: dict | None = None) -> dict:
    baseline, focus = trait_rows(items, disc, state)
    return traits.compute(baseline, focus, today, ((cfg or {}).get("thresholds") or {}).get("appearance"))

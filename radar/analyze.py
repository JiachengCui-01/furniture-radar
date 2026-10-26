"""把每个追踪中的 ASIN 跑一遍全部判定，得到带标签的结果列表。纯计算，不调接口。"""
from __future__ import annotations

from datetime import date, timedelta

from . import category_history, clock, monthly_lists
from .detect import events as events_mod
from .detect import fakehot, seasonality, traits
from .detect.design import main_material
from .discovery import clean_text
from .detect.rules import (current_rating, evaluate_hot, evaluate_potential, evaluate_surge, low_rating,
                           momentum, rating_floor, relist_ramp, relist_text, relisted)
from .series import Daily, closed_months, mean, median

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
                  stale: bool, relist: dict | None = None, age: int | None = None) -> dict:
    """突然爆火（及断货恢复 / 新品起量 / 形态不稳）、潜力、上升中、回落：都在同一份日数据上判断。
    relist：老品换链接（rules.relisted）——上架天数不代表商品是新的，不算潜力、新品爆发、新品起量。
    age：上架天数。“从零起量”的只有上架 ≤ new_listing_max_age_days 天的叫新品爆发 / 新品起量，
         更老的叫老链接重新起量（停了很久又开始卖，不是新品；回测：之后照样在涨，所以照常算爆火，只是名字不同）。"""
    oos = th.get("out_of_stock_bsr")
    surge = evaluate_surge(d, idx, th["surge"], oos) if idx is not None else None
    restock = None
    if surge and surge.get("restock"):
        restock, surge = surge, None
    if surge and surge.get("from_zero") and relist:
        surge = None  # “从零起量”只是新子体开始卖，销量是从老链接转过来的
    max_new = th["surge"].get("new_listing_max_age_days")
    revived = bool(surge and surge.get("from_zero") and max_new is not None and (age is None or age > max_new))
    new_ramp = None
    if surge and surge.get("from_zero"):
        sc = th["surge"]
        node_daily = (st.get("threshold") or 0) / 30
        need = max(sc["new_listing_min_daily"], sc["new_listing_node_share"] * node_daily)
        if surge["recent_avg"] < need:
            new_ramp, surge = surge, None
    if surge and revived:
        surge = {**surge, "kind": "老链接重新起量"}
    unshaped = None
    if surge and not surge["kind"]:  # 不符合任何一种爆火形态
        unshaped, surge = surge, None
    potential = (evaluate_potential(rec, d, as_of, th["potential"], oos, int(th.get("out_of_stock_zero_run") or 0))
                 if idx is not None and not relist else None)
    if stale:  # 日数据停更（下架/断货/数据延迟）时不做“正在爆/正在涨”的判断
        surge = potential = None
        new_ramp = None
    rising = None
    if idx is not None and not stale and not surge and not restock:
        rcfg = th.get("rising") or {}
        zero_run = int(th.get("out_of_stock_zero_run") or 0)
        avg28, prev28 = _avg(d, idx, 28), _avg(d, idx - 28, 28, oos, zero_run)
        if avg28 and prev28 and avg28 >= rcfg.get("min_daily_sales", 3) \
                and avg28 / prev28 >= rcfg.get("min_ratio", 1.3):
            rising = {"ratio": round(avg28 / prev28, 2), "avg28": avg28, "window": 28,
                      "base_in": len(d.in_stock_window("sales", idx - 28, 28, oos, zero_run))}
    # 短时脉冲把近 28 天日均撑高了，不是持续的增长：也不算潜力 / 上升中
    if unshaped and unshaped.get("pulse"):
        potential = rising = None
    # 有过上涨、但最近在回落的，不算爆火 / 潜力 / 上升中
    mom = momentum(d, idx, th.get("momentum") or {}) if idx is not None else None
    faded = bool(mom and mom["fading"] and (surge or potential or rising))
    if faded:
        surge = potential = rising = None
    return {"surge": surge, "restock": restock, "new_ramp": new_ramp, "unshaped": unshaped,
            "potential": potential, "rising": rising, "mom": mom, "faded": faded, "revived": revived}


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

    g = _judge_growth(d, idx, as_of, rec, st, th, stale, relist, age)
    # 大促拉动：近 56 天里有大促日、这个商品自己在大促日冲高了，而且算上大促日能达到爆火 / 潜力 / 上升中、去掉就达不到
    deal_lift = False
    if d is not full and idx is not None and not (g["surge"] or g["potential"] or g["rising"]) \
            and _spiked_on_events(full, idx, events, th["events"]):
        g_full = _judge_growth(full, idx, as_of, rec, st, th, stale, relist, age)
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
        tags.append(surge["kind"] if surge["kind"] != "老链接重新起量"
                    else f"老链接重新起量（上架 {age if age is not None else '天数未知'}{' 天' if age is not None else ''}，"
                         f"之前 28 天几乎没卖，不是新品）")
        if surge["price_driven"]:
            tags.append("降价驱动")
    if rising and label in ("watch", "hot"):
        tags.append(f"上升中（近28天 ×{rising['ratio']:.1f}）")
    # 对比期在卖的天数少（刚开卖或断过货）：照样判，但写明天数（回测：这样的潜力 / 上升中之后保持得不比对比期完整的差）
    shown = surge if label == "surge" else potential if label == "potential" else rising if rising and label in ("watch", "hot") else None
    short = int(th.get("short_base_days") or 0)
    if shown and not shown.get("from_zero") and shown.get("base_in") is not None and shown["base_in"] < short:
        tags.append(f"对比期只有 {shown['base_in']} 天在卖（之前 28 天里其余日子没在卖）")
    # 老品换链接、对比期还包括它开卖的第一周：照样判（回测：之后照样卖得好），但起量里可能有老链接转过来的流量
    if relist and shown is not None and shown is not potential             and relist_ramp(d, idx, relist.get("first_day"), shown.get("window") or 28, th["relist"]):
        tags.append("对比期包括它开卖的第一周（老品换链接：起量里可能有老链接转过来的流量）")
    if faded or (hot and mom and mom["fading"]):  # 持续热销正在回落的也标出来，但不影响上榜
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
        tags.append("老链接重新起量（未达爆火门槛）" if g["revived"] else "新品起量（未达爆火门槛）")
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
        "fading": bool(mom and mom["fading"]),  # 回落规则成不成立（观察板块的“最近回落”标签只在有增长时才打）
        "spark": (full.sales[max(0, idx - 89): idx + 1] if idx is not None else []),
        "spark_start": full.day(max(0, idx - 89)).isoformat() if idx is not None else None,
        "monthly": [[k, s] for k, s in months[-12:]],
        "recent_avg7": (surge or {}).get("recent_avg") if surge else _avg(d, idx, 7),
        "recent_avg28": _avg(d, idx, 28),
        # 之前 28 天（只算在卖的日子，同“上升中”的对比期）：开发方向按增量销售额排序用
        "prev_avg28": (_avg(d, idx - 28, 28, th.get("out_of_stock_bsr"), int(th.get("out_of_stock_zero_run") or 0))
                       if idx is not None else None),
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


def _avg(d: Daily | None, idx: int | None, days: int, oos_bsr: float | None = None, zero_run: int = 0) -> float | None:
    """[idx-days+1, idx] 的日均销量；给了 oos_bsr 时跳过断货的日子（用于对比期；zero_run 见 Daily.in_stock_window）。"""
    if d is None or idx is None:
        return None
    values = d.in_stock_window("sales", idx, days, oos_bsr, zero_run)
    return round(sum(values) / len(values), 2) if values else None


def analyze(state: dict, disc: dict | None, pool: list[str], cfg: dict, today: date,
            extra_rows: dict | None = None, revision: dict | None = None) -> dict:
    """extra_rows：发现基线以外的月度行（例如 BSR 上升榜），用于留评率等信号。
    大促日从本期追踪商品的日数据里识别（加上 config.yaml 登记的），见 detect/events.py。
    revision：本期测到的日数据修订（tracking.summarize_revisions）：靠最近几天才成立的判定，按它校正后不成立的写明（不排除）。"""
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
    histories = {path: category_history.series(state, path) for path in paths if path}
    profiles = {path: seasonality.profile(units, sc) for path, units in histories.items()} if sc else {}
    calibration = None
    if sc and data_date:
        # 28 天季节倍数的折扣 k：每期用本期的卖家精灵数据现算（类目历史 vs 追踪商品的日数据，去掉大促日）
        node_dailies: dict[str, list[Daily]] = {}
        for _, rec in recs:
            d = Daily.from_record(rec["series"])
            if d is not None and rec.get("node"):
                node_dailies.setdefault(rec["node"], []).append(d.without(ev["days"]))
        calibration = seasonality.calibrate(histories, node_dailies, data_date, sc)
        for prof in profiles.values():
            prof["shrink"] = calibration["k"]
    period = str((disc or {}).get("period") or "")
    threshold_month = f"{period[:4]}-{period[4:6]}" if len(period) == 6 else None

    def hot_season(rec: dict) -> dict | None:
        if not (th["hot"].get("deseasonalize") and data_date):
            return None
        return seasonality.hot_levels(profiles.get(rec.get("node")), data_date)

    items = [evaluate(asin, rec, products.get(asin), stats, cfg, today, data_date=data_date, events=ev["days"],
                      season=hot_season(rec), threshold_month=threshold_month)
             for asin, rec in recs]
    rev = usable_revision(revision, th)
    if rev:  # 最近几天的数还会被改：按本期测到的幅度校正后重判一次，原来成立、校正后不成立的写明
        for item, (asin, rec) in zip(items, recs):
            # 开发方向的增量和月销售额用校正后的近 28 天日均（去掉大促日，和 recent_avg28 同一口径）
            fixed = Daily.from_record(revised_rec(rec, rev["window"], rev["median"])["series"]).without(ev["days"])
            item["revised_avg28"] = _avg(fixed, fixed.as_of_index(), 28)
            if revision_sensitive(item):
                alt = evaluate(asin, revised_rec(rec, rev["window"], rev["median"]), products.get(asin), stats, cfg, today,
                               data_date=data_date, events=ev["days"], season=hot_season(rec), threshold_month=threshold_month)
                _revision_tag(item, lost_judgments(item, alt), rev)
    for item, (_, rec) in zip(items, recs):
        _season_tag(item, profiles.get(item.get("node")), sc)
        _item_season(item, rec, profiles.get(item.get("node")), sc)
    seasons = ({path: seasonality.outlook(prof, data_date, sc) for path, prof in profiles.items()}
               if sc and data_date else {})
    deal = deal_factors(dailies_by_node(recs), ev["days"], data_date)
    monthly, judged, status = monthly_hot_items(state, disc, stats, cfg, data_date, items, profiles, threshold_month, deal,
                                                rev)
    items += monthly
    return {"items": items, "node_stats": stats, "events": ev, "data_date": data_date, "seasons": seasons,
            "profiles": profiles, "season_calibration": calibration, "monthly_judged": judged, "monthly_status": status}


def truncated_rec(rec: dict, upto: date) -> dict | None:
    """这个商品“截至 upto 那天”的样子：日数据、评论观测、月销量都只留到那天（月销量留到那天所在的月份）。
    那天之前没有日数据的返回 None。"""
    d = Daily.from_record(rec.get("series"))
    if d is None:
        return None
    end = d.index_of(upto)
    if end < 0:
        return None
    s = rec["series"]
    series = {**s, **{k: list(s.get(k) or [])[: end + 1] for k in ("bsr", "sales", "price")}}
    if Daily.from_record(series) is None or Daily.from_record(series).as_of() is None:
        return None
    month = clock.month_key(upto)
    obs = [o for o in rec.get("obs") or [] if (clock.parse_day(o.get("date")) or upto) <= upto]
    return {**rec, "series": series, "obs": obs, "months": [m for m in rec.get("months") or [] if str(m[0]) <= month]}


def retro_labels(state: dict, disc: dict | None, asins: list[str], cfg: dict, then: date, data_then: date,
                 extra_rows: dict | None = None, events=frozenset(), profiles: dict | None = None) -> dict[str, str]:
    """上期没追踪的商品，按本期拉到的日数据回看上期那天（then，日数据截至 data_then）会被判成什么。
    “与上期对比”用它：追踪范围扩大后，上期没追踪的商品一上榜就算“新进入”，其实可能上期就已经是了。
    判定规则、大促日、子类目门槛和本期一样，只是数据截到那天（那几天的日数据已经修订过，比上期当时拉到的更准）。"""
    stats = node_stats(disc, cfg)
    products = {**(extra_rows or {}), **((disc or {}).get("products") or {})}
    th = cfg["thresholds"]
    period = str((disc or {}).get("period") or "")
    threshold_month = f"{period[:4]}-{period[4:6]}" if len(period) == 6 else None
    out = {}
    for asin in asins:
        cut = truncated_rec(state["asins"].get(asin) or {}, data_then)
        if cut is None:
            continue
        season = None
        if th["hot"].get("deseasonalize"):
            season = seasonality.hot_levels((profiles or {}).get(cut.get("node")), data_then)
        out[asin] = evaluate(asin, cut, products.get(asin), stats, cfg, then, data_date=data_then, events=events,
                             season=season, threshold_month=threshold_month)["label"]
    return out


def usable_revision(revision: dict | None, th: dict) -> dict | None:
    """本期测到的修订够不够用：测到的商品 ≥ revision_min_items 个、最近有被改的天数、幅度 ≥ revision_min_change。"""
    if not revision or not revision.get("window") or (revision.get("items") or 0) < int(th.get("revision_min_items", 30)):
        return None
    if abs(float(revision.get("median") or 1) - 1) < float(th.get("revision_min_change", 0.02)):
        return None
    return revision


def revised_rec(rec: dict, window: int, factor: float) -> dict:
    """最近 window 天（到这个商品自己最后一天）的日销量 × factor 的副本：按本期测到的修订幅度校正后的样子。"""
    s = rec["series"]
    d = Daily.from_record(s)
    i = d.as_of_index() if d else None
    sales = list(s["sales"])
    if i is not None:
        for k in range(max(0, i - window + 1), i + 1):
            if sales[k] is not None:
                sales[k] = round(sales[k] * factor, 2)
    return {**rec, "series": {**s, "sales": sales}}


def _has_tag(item: dict, head: str) -> bool:
    return any(t.startswith(head) for t in item["tags"])


def revision_sensitive(item: dict) -> bool:
    return (item["label"] in ("surge", "potential", "hot") or bool(item.get("rising") and item["label"] in ("watch", "hot"))
            or _has_tag(item, "最近回落"))


def lost_judgments(item: dict, alt: dict) -> list[str]:
    """原来成立、按修订幅度校正后不成立的判定。"""
    lost = []
    if item["label"] == "surge" and alt["label"] != "surge":
        lost.append("突然爆火")
    if item["label"] == "potential" and not alt.get("potential"):
        lost.append("潜力")
    if item.get("rising") and item["label"] in ("watch", "hot") and not alt.get("rising"):
        lost.append("上升中")
    if item["label"] == "hot" and not alt.get("hot"):
        lost.append("持续热销")
    if _has_tag(item, "最近回落") and not alt.get("fading"):
        lost.append("最近回落")  # 比回落规则本身：校正后不再算增长的，“最近回落”标签也会消失，但它仍在往下走
    return lost


def _revision_tag(item: dict, lost: list[str], rev: dict) -> None:
    item["revision_lost"] = lost
    if lost:
        item["tags"].append(f"最近 {rev['window']} 天的数还会被改：按本期测到的幅度（改成原来的 {rev['median']:.0%}）"
                            f"校正后不算{'、'.join(lost)}")


def dailies_by_node(recs: list) -> dict[str, list[Daily]]:
    out: dict[str, list[Daily]] = {}
    for _, rec in recs:
        d = Daily.from_record(rec.get("series"))
        if d is not None:
            out.setdefault(rec.get("node") or "", []).append(d)
    return out


def deal_factors(by_node: dict[str, list[Daily]], events, data_date: date | None, days: int = 30,
                 min_items: int = 10) -> dict[str, float]:
    """近 30 天名单的销量是整段合计，去不掉大促日：用本期追踪商品的日数据现算“含大促日的近 30 天日均 ÷ 去掉大促日的”，
    每个子类目取中位数（追踪商品不到 min_items 个的用全部商品的中位数，键 "*"）。近 30 天里没有大促日时都是 1。
    实测 2026-10（含 10-06/07）：全部追踪商品中位数 1.041；同一天拉的 12 个商品，名单日均 ÷ 去掉大促日的日均 1.042，扣掉后 1.001。"""
    if not data_date or not any(0 <= (data_date - day).days < days for day in events):
        return {"*": 1.0}
    ratios: dict[str, list[float]] = {}
    for node, ds in by_node.items():
        for d in ds:
            i = d.as_of_index()
            if i is None:
                continue
            raw = mean(d.window("sales", i, days))
            masked = mean(d.without(events).window("sales", i, days))
            if raw and masked:
                ratios.setdefault(node, []).append(raw / masked)
    every = [x for v in ratios.values() for x in v]
    out = {"*": round(median(every), 3) if every else 1.0}
    out.update({node: round(median(v), 3) for node, v in ratios.items() if len(v) >= min_items})
    return out


def monthly_hot_items(state: dict, disc: dict | None, stats: dict, cfg: dict, data_date: date | None,
                      tracked: list[dict], profiles: dict, threshold_month: str | None,
                      deal: dict[str, float] | None = None, rev: dict | None = None) -> tuple[list[dict], list[str], dict]:
    """持续热销的月度名单补判（monthly_lists.py）：本期月度名单里、不在追踪池（同一父体也不算）的商品，
    近 list_months 个月每个月都在子类目销售额前 50（往月名单）、近 30 天名单里也有它的，按同一套持续热销条件判，
    近 28 天日均用近 30 天名单的销量 ÷ 30 ÷ 大促系数（deal_factors：名单的销量含大促日，追踪商品比较时都去掉了大促日）。
    评分不达标的不列。
    返回 (达标的商品, 判过的 ASIN, 状态 {"months": 已齐的月数, "need": 要几个月, "rolling": 近 30 天名单查了几个子类目, "judged", "listed"})。
    没有日数据：没有走势图，不判回落，异常分只有留评率一项。"""
    th = cfg["thresholds"]
    n = int(th["hot"].get("list_months", 6))
    deal = deal or {"*": 1.0}
    status = {"months": 0, "need": n, "rolling": 0, "nodes": len((disc or {}).get("nodes") or []), "judged": 0, "listed": 0,
              "deal": deal["*"]}
    if not disc or not disc.get("nodes") or not data_date or len(str(disc.get("period") or "")) != 6:
        return [], [], status
    period = str(disc["period"])
    keys = monthly_lists.month_keys(period, n)
    have = (state.get("monthly_lists") or {}).get("months") or {}
    status["months"] = sum(1 for k in keys if all(nd["path"] in (have.get(k) or {}) for nd in disc["nodes"]))
    rolling = (state.get("monthly_lists") or {}).get("rolling") or {}
    status["rolling"] = len(rolling.get("nodes") or [])
    if status["months"] < n or not rolling.get("rows"):
        return [], [], status
    products = disc.get("products") or {}

    def parent_of(asin: str) -> str:
        return (products.get(asin) or {}).get("parent") or (state["asins"].get(asin) or {}).get("parent") or asin

    taken = {i["asin"] for i in tracked} | {parent_of(i["asin"]) for i in tracked}
    by_parent = {r.get("parent") or r["asin"]: r for r in rolling["rows"].values()}
    out, judged = [], []
    for p in sorted(products.values(), key=lambda r: -(r.get("units") or 0)):
        key = p.get("parent") or p["asin"]
        if "top" not in p["sources"] or p["asin"] in taken or key in taken or p["node"] not in stats:
            continue
        taken.add(key)
        r = rolling["rows"].get(p["asin"]) or by_parent.get(key)
        months = monthly_lists.months_of(state, p["node"], p["asin"], p.get("parent"), keys)
        if r is None or months is None:
            continue  # 近 30 天或某个月跌出了子类目销售额前 50：看不到销量，不判
        judged.append(p["asin"])
        rating, n_ratings = r.get("rating") or p.get("rating"), r.get("ratings") or p.get("ratings")
        if low_rating(rating, n_ratings, th):
            continue
        threshold = stats[p["node"]].get("threshold")
        recent = (r.get("units") or 0) / 30 / deal.get(p["node"], deal["*"])
        season = seasonality.hot_levels(profiles.get(p["node"]), data_date) if th["hot"].get("deseasonalize") else None

        def judge(daily: float) -> dict | None:
            return (evaluate_hot(months, None, data_date, threshold, th["hot"], recent_daily=daily)
                    or (evaluate_hot(months, None, data_date, threshold, th["hot"], season, threshold_month,
                                     recent_daily=daily) if season else None))

        hot = judge(recent)
        if not hot:
            continue
        rec = dict(state["asins"].get(p["asin"]) or {})
        if rec.get("variations") is None:
            rec["variations"] = p.get("variations")  # 变体多的评论是父体共享的，不算留评率
        fake = fakehot.score(rec, None, None, p, stats[p["node"]].get("median_rate"), th["fake"])
        if fake["level"]:
            continue
        title = clean_text(r.get("title") or p.get("title") or p["asin"])
        out.append({
            "asin": p["asin"], "title": title, "brand": clean_text(r.get("brand") or p.get("brand") or ""),
            "image": r.get("image") or p.get("image") or "", "node": p["node"], "node_name": p.get("node_name") or "",
            "node_cn": p.get("node_cn") or "", "price": r.get("price") or p.get("price"), "bsr": r.get("bsr"),
            "rating": rating, "ratings": n_ratings, "variations": r.get("variations") or p.get("variations"),
            "age_days": clock.days_between(clock.parse_day(p.get("available")), data_date),
            "as_of": None, "stale": False, "label": "hot", "label_cn": LABEL_CN["hot"],
            "tags": ["只有月度数据（不在追踪池：没有走势图，不判回落；异常信号只看留评率）"] + fake["tags"],
            "surge": None, "restock": None, "rising": None, "material": main_material(title), "hot": hot,
            "potential": None, "fake": fake, "relist": None, "spark": [], "spark_start": None,
            "monthly": [[k, u] for k, u, *_ in months], "recent_avg7": None, "recent_avg28": round(recent, 2),
            "prev_avg28": None, "pkg": r.get("pkg") or p.get("pkg"), "fetched_run": None, "source": "monthly",
            "daily": False, "season28": None, "item_season": None,
        })
        if rev:  # 近 30 天名单里最后几天的数也还会被改：这几天按修订幅度校正后，近 30 天日均跟着变
            share = min(int(rev["window"]), 30) / 30
            _revision_tag(out[-1], [] if judge(recent * (1 - share * (1 - float(rev["median"])))) else ["持续热销"], rev)
    status.update(judged=len(judged), listed=len(out))
    return out, judged, status


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


def _item_season(item: dict, rec: dict, prof: dict | None, sc: dict | None) -> None:
    """季节性商品（seasonality.item_season）：item["item_season"] = 判断结果（季节性品类里查过商品自己往年月销量的才有，
    sales_trend.py），是的标“季节性（连续 N 年 X 月旺…）”。"""
    s = seasonality.item_season(seasonality.trend_months(rec.get("sales_trend")), prof, sc) if sc else None
    item["item_season"] = s
    if s and s["seasonal"]:
        item["tags"].append(f"季节性（连续 {s['cycles']} 年 {seasonality.months_text(s['peak_months'])}旺，"
                            f"每年旺季日均都至少是前后淡季的 {s['ratio']:.1f} 倍）")


def is_seasonal(item: dict) -> bool:
    """季节性商品清单（不是板块：不论在哪个板块、还是只在观察，都再列一遍）：seasonality.item_season 的四条都满足的，
    异常信号、评分偏低的不列（它们不进任何榜单）。"""
    return item["label"] not in UNLISTED and bool((item.get("item_season") or {}).get("seasonal"))


def _growth(item: dict) -> float:
    p = item.get("potential") or {}
    if p.get("growth") is not None:
        return p["growth"]
    return (p.get("recent_ratio") or 1) - 1


def sections(items: list[dict], top_n: int) -> dict[str, list[dict]]:
    """每个板块按排序；top_n ≤ 0 时全部列出（报告小窗默认如此，免得顶部数字和小窗里的个数对不上）。
    seasonal：季节性商品清单（is_seasonal），离旺季近的排前面（正在旺季的最前），同样近的按旺季日均从大到小。"""
    def cut(rows: list[dict]) -> list[dict]:
        return rows if top_n <= 0 else rows[:top_n]

    surge = sorted((i for i in items if i["label"] == "surge"), key=lambda i: -i["surge"]["strength"])
    fake = sorted((i for i in items if i["label"] == "fake"),
                  key=lambda i: (-i["fake"]["score"], -(i.get("recent_avg28") or 0)))
    potential = sorted((i for i in items if i["label"] == "potential"), key=lambda i: -_growth(i))
    hot = sorted((i for i in items if i["label"] == "hot"), key=lambda i: -(i["hot"]["avg_monthly"] or 0))

    def season_key(i: dict) -> tuple:
        as_of = clock.parse_day(i.get("as_of"))
        s = i["item_season"]
        return (seasonality.months_to_peak(as_of, s["peak_months"]) if as_of else 12, -s["peak_avg"])

    seasonal = sorted((i for i in items if is_seasonal(i)), key=season_key)
    return {"surge": cut(surge), "fake": cut(fake), "potential": cut(potential), "hot": cut(hot),
            "seasonal": cut(seasonal),
            "counts": {"surge": len(surge), "fake": len(fake), "potential": len(potential), "hot": len(hot),
                       # 上升中：只数不在爆火/潜力里的（爆火、潜力本来就在增长，不重复计数）
                       "rising": sum(1 for i in items if i.get("rising") and i["label"] in ("watch", "hot")),
                       "seasonal": len(seasonal),
                       "low": sum(1 for i in items if i["label"] == "low"),
                       # 追踪的商品数（月度名单补判的持续热销没有日数据，不算追踪）
                       "total": sum(1 for i in items if i.get("daily", True)),
                       "hot_monthly": sum(1 for i in hot if not i.get("daily", True))}}


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

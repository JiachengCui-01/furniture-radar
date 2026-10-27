"""把每个追踪中的 ASIN 跑一遍全部判定，得到带标签的结果列表。纯计算，不调接口。"""
from __future__ import annotations

from datetime import date

from . import clock, monthly_lists, universe
from .detect import events as events_mod
from .detect import fakehot, traits
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
            # 门槛 = 按月销量排的第 N 名：销售额前 50（"top"）+ 月销量前 50（"units"）合起来按件数排，同一父体只算一次
            if "top" in p["sources"] or "units" in p["sources"]:
                key = p.get("parent") or p["asin"]
                per_parent[key] = max(per_parent.get(key, 0), p["units"] or 0)
        units = sorted(per_parent.values(), reverse=True)
        threshold = (units[rank - 1] if len(units) >= rank else units[-1]) if units else None
        # 留评率的中位数只算变体不多的商品（变体多的评论是父体共享的，打分时比值先除以变体数，再和这个中位数比）
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
                  stale: bool, relist: dict | None = None, age: int | None = None, tide: float | None = None) -> dict:
    """突然爆火（及断货恢复 / 新品起量 / 形态不稳）、潜力、上升中、回落：都在同一份日数据上判断。
    relist：老品换链接（rules.relisted）——上架天数不代表商品是新的，不算潜力、新品爆发、新品起量。
    age：上架天数。“从零起量”的只有上架 ≤ new_listing_max_age_days 天的叫新品爆发 / 新品起量，
         更老的叫老链接重新起量（停了很久又开始卖，不是新品；回测：之后照样在涨，所以照常算爆火，只是名字不同）。
    tide：所在子类目的大盘倍数（universe.node_tide）。大于 1 时突然爆火、潜力、上升中的增长倍数先除以它再比门槛（跑赢大盘）。
          突然爆火原来不用（以为 BSR 排名提升一倍本来就是和市场比的），但 12 月整个子类目的排名会一起往上走：
          回测（2025-11~2026-09 每周回放，1,778 次突然爆火）去掉 14%，去掉的之后 28 天 78% 保持住、留下的 88%，12 月 60% / 77%。
          从零起量的（新品爆发 / 老链接重新起量）没有对比期的倍数，不扣。"""
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
    # 跑赢大盘（突然爆火）：先判，扣掉以后不算爆火的，接着看够不够上升中
    tide_lost = []
    need_surge = th["surge"]["sales_ratio"]
    if surge and tide and tide > 1 and not surge.get("from_zero") and surge.get("sales_ratio") is not None \
            and surge["sales_ratio"] / tide < need_surge - 1e-9:
        tide_lost.append(("突然爆火", surge["sales_ratio"], need_surge))
        surge = None
    potential =(evaluate_potential(rec, d, as_of, th["potential"], oos, int(th.get("out_of_stock_zero_run") or 0))
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
    # 跑赢大盘：所在子类目整体在涨时，增长倍数先除以大盘倍数（只是随大流的不算潜力 / 上升中）
    tided = None
    if tide and tide > 1:
        lost = list(tide_lost)
        if potential and potential.get("recent_ratio") is not None \
                and potential["recent_ratio"] / tide < th["potential"]["min_recent_ratio"] - 1e-9:
            lost.append(("潜力", potential["recent_ratio"], th["potential"]["min_recent_ratio"]))
            potential = None
        if rising and rising["ratio"] / tide < (th.get("rising") or {}).get("min_ratio", 1.3) - 1e-9:
            lost.append(("上升中", rising["ratio"], (th.get("rising") or {}).get("min_ratio", 1.3)))
            rising = None
        tided = {"tide": tide, "lost": lost} if lost else None
    # 短时脉冲把近 28 天日均撑高了，不是持续的增长：也不算潜力 / 上升中
    if unshaped and unshaped.get("pulse"):
        potential = rising = None
    # 有过上涨、但最近在回落的，不算爆火 / 潜力 / 上升中
    mom = momentum(d, idx, th.get("momentum") or {}) if idx is not None else None
    faded = bool(mom and mom["fading"] and (surge or potential or rising))
    if faded:
        surge = potential = rising = None
    return {"surge": surge, "restock": restock, "new_ramp": new_ramp, "unshaped": unshaped,
            "potential": potential, "rising": rising, "mom": mom, "faded": faded, "revived": revived, "tided": tided}


def evaluate(asin: str, rec: dict, disc_row: dict | None, stats: dict, cfg: dict, today: date, *,
             data_date: date | None = None, events=frozenset(), tide: float | None = None) -> dict:
    """data_date：本期日数据的最新日期（所有追踪商品里最晚的一天），“数据陈旧”和它比；
    events：大促日（detect/events.py），爆火 / 潜力 / 上升中 / 回落 / 持续热销都在去掉这些天的日数据上判断；
    tide：所在子类目的大盘倍数（universe.node_tide），潜力、上升中要跑赢它（_judge_growth）。"""
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

    g = _judge_growth(d, idx, as_of, rec, st, th, stale, relist, age, tide)
    # 大促拉动：近 56 天里有大促日、这个商品自己在大促日冲高了，而且算上大促日能达到爆火 / 潜力 / 上升中、去掉就达不到
    deal_lift = False
    if d is not full and idx is not None and not (g["surge"] or g["potential"] or g["rising"]) \
            and _spiked_on_events(full, idx, events, th["events"]):
        g_full = _judge_growth(full, idx, as_of, rec, st, th, stale, relist, age, tide)
        deal_lift = bool(g_full["surge"] or g_full["potential"] or g_full["rising"])
    surge, restock, new_ramp, unshaped = g["surge"], g["restock"], g["new_ramp"], g["unshaped"]
    potential, rising, mom, faded = g["potential"], g["rising"], g["mom"], g["faded"]
    hot = evaluate_hot(rec.get("months") or [], d, as_of, st.get("threshold"), th["hot"])
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
    if g["tided"] and label not in ("surge", "potential") and not rising:
        t = g["tided"]
        tags.append(f"随大盘（所在子类目整体 ×{t['tide']:.2f}，" + "；".join(
            f"{name} ×{ratio:.2f} 扣除后 ×{ratio / t['tide']:.2f}，不到 {need:g} 倍" for name, ratio, need in t["lost"]) + "）")
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
        "tide": tide,
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
    revision：本期测到的日数据修订（tracking.summarize_revisions）。thresholds.revision_mode：
      correct（默认）= 最近几天按测到的幅度校正后再判，按没修订的数才成立的在卡片上写明（不上榜）；
      tag = 按当期拉到的数判，校正后不成立的写明（不排除）。"""
    stats = node_stats(disc, cfg)
    products = {**(extra_rows or {}), **((disc or {}).get("products") or {})}
    recs = [(asin, state["asins"][asin]) for asin in pool
            if (state["asins"].get(asin) or {}).get("series")]
    dailies = [d for d in (Daily.from_record(rec["series"]) for _, rec in recs) if d is not None]
    data_date = max((day for day in (d.as_of() for d in dailies) if day), default=None)
    ev = events_mod.detect(dailies, cfg["thresholds"]["events"], cfg.get("deal_windows") or [])
    th = cfg["thresholds"]
    rev = usable_revision(revision, th)
    inflation = recent_inflation(dailies, ev["days"], int(rev["window"])) if rev else None
    correct = corrects(rev, th, inflation)
    if rev and str(th.get("revision_mode") or "correct") == "correct" and not correct:
        rev = None  # 当期拉到的数并不偏高：按拉到的数判，也不标“还会被改”
    deal = deal_factors(dailies_by_node(recs), ev["days"], data_date)
    tides = universe.node_tide(state, today, cfg, deal, rev)  # 品类大盘：突然爆火、潜力、上升中要跑赢它

    def judge(asin: str, rec: dict) -> dict:
        return evaluate(asin, rec, products.get(asin), stats, cfg, today, data_date=data_date, events=ev["days"],
                        tide=tides.get(rec.get("node")))

    # 最近几天的数还会被卖家精灵下调：默认按本期测到的幅度校正后再判（回测：准确率 突然爆火 70%→86%、潜力 69%→85%、上升中 57%→82%，
    # 召回少 7~11 个百分点），按当期拉到的数才成立的写明
    items = [judge(asin, revised_rec(rec, rev["window"], rev["median"]) if correct else rec) for asin, rec in recs]
    if correct:
        for item, (asin, rec) in zip(items, recs):
            item["revised_avg28"] = item["recent_avg28"]  # 已经是校正后的（开发方向的增量和月销售额用它）
            _raw_only_tag(item, lost_judgments(judge(asin, rec), item, with_fading=False), rev)
    elif rev:  # tag：按当期拉到的数判，校正后重判一次，原来成立、校正后不成立的写明
        for item, (asin, rec) in zip(items, recs):
            # 开发方向的增量和月销售额用校正后的近 28 天日均（去掉大促日，和 recent_avg28 同一口径）
            fixed = Daily.from_record(revised_rec(rec, rev["window"], rev["median"])["series"]).without(ev["days"])
            item["revised_avg28"] = _avg(fixed, fixed.as_of_index(), 28)
            if revision_sensitive(item):
                alt = evaluate(asin, revised_rec(rec, rev["window"], rev["median"]), products.get(asin), stats, cfg, today,
                               data_date=data_date, events=ev["days"], tide=tides.get(rec.get("node")))
                _revision_tag(item, lost_judgments(item, alt), rev)
    month_rows = (disc or {}).get("products") or {}
    month_by_family = {universe.family_key(p, a): p for a, p in month_rows.items()}  # 换了代表子体也对得上
    for item, (asin, rec) in zip(items, recs):
        now_row = (extra_rows or {}).get(asin)
        month_row = month_rows.get(asin) or month_by_family.get(universe.family_key(now_row or rec, asin))
        data_tags(item, rec, now_row, month_row, state, today, th.get("data_checks") or {})
    monthly, judged, status = monthly_hot_items(state, disc, stats, cfg, data_date, items, deal, rev, today)
    items += monthly
    check = None
    if usable_revision(revision, th):  # 数据说明里写：当期拉到的数偏不偏高、校正了没有
        need = 1 + (1 / float(revision["median"]) - 1) * float(th.get("revision_inflation_share", 0.5))
        check = {"inflation": inflation, "need": round(need, 3), "applied": correct}
    return {"items": items, "node_stats": stats, "events": ev, "data_date": data_date,
            "monthly_judged": judged, "monthly_status": status, "tides": tides, "revision_check": check}


def data_tags(item: dict, rec: dict, now_row: dict | None, month_row: dict | None, state: dict, today: date,
              cfg: dict) -> None:
    """数据本身可能有问题的，在卡片上写明（不影响上榜，人工看的时候心里有数）：
    ① 变体变多：增长商品的变体数比约一个月前（自己存的全市场名单快照，没有时用上个月的月度名单）多了 ≥ var_jump 且 ≥ var_min 个——
       增长里可能有新挂进来的变体的销量（10-23 全覆盖试跑：同一 ASIN 9 月名单和 10-23 名单比，537 个里 24 个变体多了三成以上）；
    ② 名单和日数据对不上：本期名单的近 30 天销量和日数据最近 30 天合计差 ≥ mismatch 倍（两边都 ≥ mismatch_min 件），
       日数据可能估错了（试跑：7,533 个商品中位数 0.99 倍，差 2 倍以上的 0.4%）；
    ③ 日数据有缺：最近 28 天里 ≥ gap_days 天没有日数据（断货或数据缺失，判断不准）。"""
    growth = item["label"] in ("surge", "potential") or bool(item.get("rising") and item["label"] in ("watch", "hot"))
    now_vars = (now_row or {}).get("variations")
    then = universe.variations_then(state, item["asin"], today, row=now_row or rec)
    old_vars, when = (then[1], f"{then[0]} 天前") if then else ((month_row or {}).get("variations"), "上个月的名单里")
    if growth and now_vars and old_vars and now_vars >= old_vars * (1 + float(cfg.get("var_jump", 0.3))) - 1e-9 \
            and now_vars - old_vars >= float(cfg.get("var_min", 2)):
        item["tags"].append(f"变体变多（{when} {int(old_vars)} 个，现在 {int(now_vars)} 个）：增长里可能有新挂进来的变体的销量")
    d = Daily.from_record(rec.get("series"))
    i = d.as_of_index() if d else None
    if i is None:
        return
    daily30 = sum(v for v in d.sales[max(0, i - 29): i + 1] if v)
    listed = (now_row or {}).get("units") or 0
    low = float(cfg.get("mismatch_min", 60))
    if listed >= low and daily30 >= low and max(listed, daily30) >= float(cfg.get("mismatch", 2.0)) * min(listed, daily30):
        item["tags"].append(f"名单和日数据对不上（名单近 30 天 {listed:,.0f} 件，日数据合计 {daily30:,.0f} 件）：数据可能有误")
    first = next((k for k, v in enumerate(d.sales) if v is not None), i)  # 开卖以前的日子不算缺
    gaps = sum(1 for v in d.sales[max(first, i - 27): i + 1] if v is None)
    if gaps >= int(cfg.get("gap_days", 7)):
        item["tags"].append(f"最近 28 天有 {gaps} 天没有日数据（断货或数据缺失，判断可能不准）")


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
                 extra_rows: dict | None = None, events=frozenset()) -> dict[str, str]:
    """上期没追踪的商品，按本期拉到的日数据回看上期那天（then，日数据截至 data_then）会被判成什么。
    “与上期对比”用它：追踪范围扩大后，上期没追踪的商品一上榜就算“新进入”，其实可能上期就已经是了。
    判定规则、大促日、子类目门槛和本期一样，只是数据截到那天（那几天的日数据已经修订过，比上期当时拉到的更准）。"""
    stats = node_stats(disc, cfg)
    products = {**(extra_rows or {}), **((disc or {}).get("products") or {})}
    out = {}
    for asin in asins:
        cut = truncated_rec(state["asins"].get(asin) or {}, data_then)
        if cut is None:
            continue
        out[asin] = evaluate(asin, cut, products.get(asin), stats, cfg, then, data_date=data_then, events=events)["label"]
    return out


def corrects(rev: dict | None, th: dict, inflation: float | None = None) -> bool:
    """本期按校正后的数判：有可用的修订测量、thresholds.revision_mode 是 correct，而且当期拉到的数最近几天确实偏高
    （recent_inflation ≥ 1 + 测到的修订幅度对应的偏高 × revision_inflation_share；inflation 为空时不看这一条）。
    为什么要看当期偏不偏高：测到的修订说的是上期拉到的数被下调了，不一定说明这期的也会被下调。实测 10-20 拉的数（修订前，倒推）
    最近 9 天比之前高 ×1.17，被下调到 0.82；修订后 10-22 拉的数 ×0.93，并不偏高——那次很可能是卖家精灵一次性的重新估算，
    再拿 0.82 去校正 10-22 拉的数，会凭空造出“下台阶”（10-23 那期 9 个潜力 / 上升中因此被判成最近回落）。"""
    if not rev or str(th.get("revision_mode") or "correct") != "correct":
        return False
    if inflation is None:
        return True
    need = 1 + (1 / float(rev["median"]) - 1) * float(th.get("revision_inflation_share", 0.5))
    return inflation >= need - 1e-9


def recent_inflation(dailies: list[Daily], events, window: int, back: int = 14) -> float | None:
    """当期拉到的数最近 window 天偏高多少：每个商品“最近 window 天日均 ÷ 再之前 18 天日均”（去掉大促日）取中位数，
    除以同一批商品 back 天前那个截面的同样比值（那时的最近几天现在都已修订完；两个截面相除，去掉了大盘本身的走势）。
    实测：修订前（10-20，倒推）×1.17，修订后（10-22）×0.93。商品不到 30 个时返回 None。"""
    def ratio(d: Daily, i: int) -> float | None:
        a, b = d.window("sales", i, window), d.window("sales", i - window, 18)
        if len(a) < max(3, window // 2) or len(b) < 9 or sum(b) / len(b) < 3:
            return None
        return (sum(a) / len(a)) / (sum(b) / len(b))

    masked = [d.without(events) for d in dailies]
    now = [r for d in masked if (i := d.as_of_index()) is not None and (r := ratio(d, i)) is not None]
    then = [r for d in masked if (i := d.as_of_index()) is not None and (r := ratio(d, i - back)) is not None]
    if len(now) < 30 or len(then) < 30:
        return None
    return round(median(now) / median(then), 3)


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


def lost_judgments(item: dict, alt: dict, with_fading: bool = True) -> list[str]:
    """item 里成立、alt 里不成立的判定（tag 模式：item = 当期拉到的数，alt = 校正后的；correct 模式反过来，
    且不比“最近回落”——判定按校正后的数，按没修订的数在回落不影响什么）。"""
    lost = []
    if item["label"] == "surge" and alt["label"] != "surge":
        lost.append("突然爆火")
    if item["label"] == "potential" and not alt.get("potential"):
        lost.append("潜力")
    if item.get("rising") and item["label"] in ("watch", "hot") and not alt.get("rising"):
        lost.append("上升中")
    if item["label"] == "hot" and not alt.get("hot"):
        lost.append("持续热销")
    if with_fading and _has_tag(item, "最近回落") and not alt.get("fading"):
        lost.append("最近回落")  # 比回落规则本身：校正后不再算增长的，“最近回落”标签也会消失，但它仍在往下走
    return lost


def _raw_only_tag(item: dict, raw_only: list[str], rev: dict) -> None:
    """correct 模式：按当期拉到的数（没修订）才成立、按校正后的数不成立的判定，卡片上写明（不上榜）。"""
    item["revision_raw_only"] = raw_only
    if raw_only:
        item["tags"].append(f"按当期拉到的数算{'、'.join(raw_only)}，但最近 {rev['window']} 天的数还会被下调"
                            f"（本期测到改成原来的 {rev['median']:.0%}），校正后不算")


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
                      tracked: list[dict], deal: dict[str, float] | None = None,
                      rev: dict | None = None, today: date | None = None) -> tuple[list[dict], list[str], dict]:
    """持续热销的月度名单补判（monthly_lists.py）：本期月度名单里、不在追踪池（同一父体也不算）的商品，
    近 list_months 个月每个月都在子类目销售额前 50 或月销量前 50（往月名单，两份都看：便宜、走量的头部进不了销售额前 50）、
    当期有它的近 30 天销量的（monthly_lists.recent_rows：
    全市场名单 + 小子类目的近 30 天名单，都是当期的），按同一套持续热销条件判，
    近 28 天日均用近 30 天销量 ÷ 30 ÷ 大促系数（deal_factors：名单的销量含大促日，追踪商品比较时都去掉了大促日）。
    评分不达标的不列。
    返回 (达标的商品, 判过的 ASIN, 状态 {"months": 已齐的月数, "units_months": 按件数的往月名单已齐的月数, "need": 要几个月,
    "rolling": 几个子类目有当期的近 30 天销量,
    "rolling_date": 近 30 天名单是哪天查的, "judged", "listed"})。没有日数据：没有走势图，不判回落，异常分只有留评率一项。"""
    th = cfg["thresholds"]
    n = int(th["hot"].get("list_months", 6))
    deal = deal or {"*": 1.0}
    correct = corrects(rev, th)
    # 近 30 天里最后 window 天按修订幅度校正后，近 30 天日均变成原来的几成
    shrink = 1 - min(int(rev["window"]), 30) / 30 * (1 - float(rev["median"])) if rev else 1.0
    status = {"months": 0, "need": n, "rolling": 0, "nodes": len((disc or {}).get("nodes") or []), "judged": 0, "listed": 0,
              "deal": deal["*"]}
    if not disc or not disc.get("nodes") or not data_date or len(str(disc.get("period") or "")) != 6:
        return [], [], status
    period = str(disc["period"])
    keys = monthly_lists.month_keys(period, n)
    have = (state.get("monthly_lists") or {}).get("months") or {}
    status["months"] = sum(1 for k in keys if all(nd["path"] in (have.get(k) or {}) for nd in disc["nodes"]))
    units_have = (state.get("monthly_lists") or {}).get("units_months") or {}
    status["units_months"] = sum(1 for k in keys if all(nd["path"] in (units_have.get(k) or {}) for nd in disc["nodes"]))
    recent_rows, covered = monthly_lists.recent_rows(state, disc["nodes"], today)
    status["rolling"] = len(covered & {nd["path"] for nd in disc["nodes"]})
    status["rolling_date"] = ((state.get("monthly_lists") or {}).get("rolling") or {}).get("date")
    if status["months"] < n or not recent_rows:
        return [], [], status
    products = disc.get("products") or {}

    def parent_of(asin: str) -> str:
        return (products.get(asin) or {}).get("parent") or (state["asins"].get(asin) or {}).get("parent") or asin

    taken = {i["asin"] for i in tracked} | {parent_of(i["asin"]) for i in tracked}
    by_parent = {r.get("parent") or r["asin"]: r for r in recent_rows.values()}
    out, judged = [], []
    # 销售额前 50 里的先判：同一父体两份名单里的代表子体不一样时，用销售额名单里那个 ASIN（和以前一样，“与上期对比”不会当成一出一进）
    for p in sorted(products.values(), key=lambda r: ("top" not in r["sources"], -(r.get("units") or 0))):
        key = p.get("parent") or p["asin"]
        if not ({"top", "units"} & set(p["sources"])) or p["asin"] in taken or key in taken or p["node"] not in stats:
            continue
        taken.add(key)
        r = recent_rows.get(p["asin"]) or by_parent.get(key)
        months = monthly_lists.months_of(state, p["node"], p["asin"], p.get("parent"), keys)
        if r is None or months is None:
            continue  # 近 30 天或某个月跌出了子类目的头部（销售额前 50 和月销量前 50 都不在）：看不到销量，不判
        judged.append(p["asin"])
        rating, n_ratings = r.get("rating") or p.get("rating"), r.get("ratings") or p.get("ratings")
        if low_rating(rating, n_ratings, th):
            continue
        threshold = stats[p["node"]].get("threshold")
        recent = (r.get("units") or 0) / 30 / deal.get(p["node"], deal["*"])
        if correct:  # 近 30 天里最后几天的数也还会被下调：按本期测到的修订幅度校正后再判（和追踪商品一样）
            recent *= shrink

        def judge(daily: float) -> dict | None:
            return evaluate_hot(months, None, data_date, threshold, th["hot"], recent_daily=daily)

        hot = judge(recent)
        if not hot:
            continue
        rec = dict(state["asins"].get(p["asin"]) or {})
        if rec.get("variations") is None:
            rec["variations"] = p.get("variations")  # 变体多的评论是父体共享的，留评率先除以变体数
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
            "daily": False,
        })
        if rev and not correct:  # tag 模式：近 30 天名单里最后几天的数也还会被改，校正后不达标的写明
            _revision_tag(out[-1], [] if judge(recent * shrink) else ["持续热销"], rev)
    status.update(judged=len(judged), listed=len(out))
    return out, judged, status


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

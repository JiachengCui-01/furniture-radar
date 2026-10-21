"""判定规则的单元测试：用合成的 120 天日序列覆盖各种形态。"""
from datetime import date, timedelta

from radar import analyze
from radar.detect import fakehot
from radar.detect.rules import (current_rating, evaluate_hot, evaluate_potential, evaluate_surge, low_rating,
                                rating_floor)
from radar.series import Daily, find_pulses
from radar.verify import summarize_reviews

END = date(2026, 10, 6)


def bsr_of(sales):
    return int(250000 / (sales + 0.3) ** 0.9)


def make(sales, prices=None, end=END):
    start = end - timedelta(days=len(sales) - 1)
    prices = prices or [100.0] * len(sales)
    return Daily(start, [bsr_of(s) for s in sales], list(sales), list(prices))


def surge_cfg(cfg):
    return cfg["thresholds"]["surge"]


def test_sustained_surge(cfg):
    d = make([5] * 110 + [18] * 10)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg))
    assert s and s["kind"] == "稳在高位" and s["sales_ratio"] > 2.5 and not s["price_driven"]


def test_short_pulse_is_not_a_surge_but_scores_as_anomaly(cfg):
    sales = [6] * 120
    sales[-9] = sales[-8] = 40
    d = make(sales)
    assert evaluate_surge(d, len(d) - 1, surge_cfg(cfg)) is None  # BSR 中位数没动
    f = cfg["thresholds"]["fake"]
    assert find_pulses(d, len(d) - 1, 30, f["pulse_mult"], f["pulse_min_sales"], f["pulse_revert"])


def test_small_base_noise_is_ignored(cfg):
    d = make([0, 1] * 55 + [2, 3] * 5)  # 从 0.5 涨到 2.5，但绝对量太小
    assert evaluate_surge(d, len(d) - 1, surge_cfg(cfg)) is None


def test_price_driven_surge(cfg):
    d = make([5] * 110 + [16] * 10, prices=[200.0] * 110 + [140.0] * 10)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg))
    assert s and s["price_driven"]


def _yearly(level, days=420):
    """以 END 结尾的 days 天日销量，level(那一天) = 当天销量。"""
    start = END - timedelta(days=days - 1)
    return [level(start + timedelta(days=k)) for k in range(days)]


WINTER = {"seasonal": True, "peak_months": [11, 12]}  # 品类旺季 11~12 月


def _seasonal(d, cfg) -> bool:
    from radar.detect import seasonality

    s = seasonality.item_season(d, WINTER, cfg["thresholds"]["seasonality"], cfg["thresholds"]["out_of_stock_bsr"])
    return bool(s and s["seasonal"])


def test_seasonal_product_sells_well_only_in_its_season(cfg):
    """季节性商品：每年固定一段时间卖得特别好、其他时间反而不行（每段旺季 ≥ 前后淡季的 2 倍、淡季没有冲高过）。"""
    from radar.detect import seasonality

    sc, oos = cfg["thresholds"]["seasonality"], cfg["thresholds"]["out_of_stock_bsr"]
    winter = make(_yearly(lambda d: 30 if d.month in (11, 12) else 10))
    s = seasonality.item_season(winter, WINTER, sc, oos)
    assert s["seasonal"] and s["ratio"] == 3.0 and s["peak_avg"] == 30 and s["off_avg"] == 10
    assert s["months"][0][0] == "2025-08" and s["months"][-1][0] == "2026-09"  # 10 月只有 6 天，不算
    # 一直在跌：去年 11~12 月比之后高，但和之前的 8~10 月一样高 → 只有一边低，不算
    assert not _seasonal(make(_yearly(lambda d: 30 if (d.year, d.month) <= (2025, 12) else 10)), cfg)
    # 一直在涨：今年的淡季比去年旺季还高
    assert not _seasonal(make(_yearly(lambda d: 5 + 2 * ((d.year - 2025) * 12 + d.month))), cfg)
    # 淡季的 8 月冲到和旺季一样高：“其他时间反而不行”不成立
    assert not _seasonal(make(_yearly(lambda d: 30 if d.month in (11, 12) or (d.year, d.month) == (2026, 8) else 10)),
                         cfg)
    # 旺季只比淡季高 50%：不够“特别好”
    assert not _seasonal(make(_yearly(lambda d: 15 if d.month in (11, 12) else 10)), cfg)
    # 不是季节性品类、或没有一整年数据（上架才半年）：不判断
    assert seasonality.item_season(winter, {"seasonal": False}, sc, oos) is None
    young = make(_yearly(lambda d: 30 if d.month in (11, 12) else 10, days=200))
    assert seasonality.item_season(young, WINTER, sc, oos) is None


def test_event_days_are_detected_from_many_items_jumping_together(cfg):
    """大促日：同一天 ≥30% 的商品销量翻到自己前 14 天中位数的 1.8 倍；相连的、≥15% 的日子也算。"""
    from radar.detect import events

    ev_cfg = cfg["thresholds"]["events"]
    e = 100  # 第 100、101 天：35/50 个商品翻倍；第 99 天：10/50 个；第 95 天：5/50 个（平时的波动）
    dailies = []
    for k in range(50):
        sales = [10] * 120
        if k < 35:
            sales[e] = sales[e + 1] = 25
        elif k < 45:
            sales[e - 1] = 20
        if k < 5:
            sales[95] = 20
        dailies.append(make(sales))
    day = dailies[0].day
    found = events.detect(dailies, ev_cfg, [])
    assert found["days"] == {day(e - 1), day(e), day(e + 1)}
    assert found["spans"] == [{"start": day(e - 1).isoformat(), "end": day(e + 1).isoformat(),
                               "name": "自动识别", "auto": True}]
    # 登记的大促日期：数据里那天也冲高（≥15%）的算；数据显示没冲高的不算；够格的商品不足 min_items 个、数据看不出来时也不算
    deals = [{"name": "黑五", "start": day(e).isoformat(), "end": day(e + 1).isoformat()},
             {"name": "没体现的大促", "start": day(30).isoformat(), "end": day(31).isoformat()}]
    named = events.detect(dailies, ev_cfg, deals)
    assert named["days"] == {day(e - 1), day(e), day(e + 1)} and named["spans"][0]["name"] == "黑五"
    few = events.detect(dailies[:20], ev_cfg, deals)
    assert few["days"] == frozenset() and few["spans"] == []  # 只认卖家精灵数据，不按登记的日期硬算


def test_growth_made_of_event_days_is_not_a_surge(cfg):
    """大促 4 天销量翻 8 倍、之后回到原样：算上大促日像“仍在上涨”，去掉后没有增长 → 不算爆火，标“大促拉动”。
    大促日没冲高的商品不标；大促日也不让平稳的商品显得在回落。"""
    lifted = [5] * 115 + [40, 40, 40, 40, 5]
    assert evaluate_surge(make(lifted), 119, surge_cfg(cfg))["kind"] == "仍在上涨"  # 不去掉大促日时
    state = {"asins": {"B0": _rec(lifted, 4.5), "C0": _rec([10] * 120, 4.5)}}
    for k in range(50):  # 其他追踪商品在同样 4 天里翻倍
        state["asins"][f"X{k}"] = _rec([10] * 115 + [20, 20, 20, 20, 10], 4.5)
    result = analyze.analyze(state, None, list(state["asins"]), cfg, END)
    assert result["events"]["days"] == {END - timedelta(days=k) for k in (1, 2, 3, 4)}
    by_asin = {i["asin"]: i for i in result["items"]}
    assert by_asin["B0"]["label"] == "watch" and "大促拉动（去掉大促日后不达标）" in by_asin["B0"]["tags"]
    assert by_asin["C0"]["label"] == "watch" and by_asin["C0"]["tags"] == []
    assert not any(t.startswith("最近回落") for i in result["items"] for t in i["tags"])


def test_out_of_stock_days_are_not_the_baseline(cfg):
    """实测形态：上架多年的商品停售一年（销量 0、BSR 850 万），重新开卖两周后翻了一倍。对比期一半是停售的日子，
    旧规则拿它当“之前的水平”，判成“稳在高位”；断货的日子不算进对比期后，只和开卖后的两周比，不算爆火。"""
    sales = [0] * 92 + [22] * 14 + [38] * 14
    bsr = [8_500_000] * 92 + [50_000] * 14 + [29_000] * 14
    d = Daily(END - timedelta(days=119), bsr, sales, [68.0] * 120)
    assert evaluate_surge(d, 119, surge_cfg(cfg))["kind"] == "稳在高位"  # 不排除断货日时
    assert evaluate_surge(d, 119, surge_cfg(cfg), cfg["thresholds"]["out_of_stock_bsr"]) is None
    rec = {"series": {"start": d.start.isoformat(), "bsr": bsr, "sales": sales, "price": d.price},
           "available": "2020-08-30", "obs": [{"date": END.isoformat(), "ratings": 154, "rating": 4.6}]}
    item = analyze.analyze({"asins": {"B1": rec}}, None, ["B1"], cfg, END)["items"][0]
    assert item["label"] == "watch" and not item["rising"]


def test_stale_is_measured_from_the_latest_data_day(cfg):
    """数据陈旧：比本期所有商品里最新的一天晚 stale_days 天以上（不和运行时的日期比，卖家精灵的日期按北京时间）。"""
    surge = [5] * 110 + [18] * 10
    late = _rec(surge[:-4], 4.5)
    late["series"]["start"] = (END - timedelta(days=119)).isoformat()  # 最后 4 天没有数据
    state = {"asins": {"A1": _rec(surge, 4.5), "A2": late}}
    items = analyze.analyze(state, None, ["A1", "A2"], cfg, END - timedelta(days=1))["items"]
    by_asin = {i["asin"]: i for i in items}
    assert by_asin["A1"]["label"] == "surge" and not by_asin["A1"]["stale"]
    assert by_asin["A2"]["stale"] and by_asin["A2"]["label"] != "surge" and "数据陈旧" in by_asin["A2"]["tags"]


def test_new_listing_ramp_is_from_zero(cfg):
    d = make([0] * 113 + [6] * 7)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg))
    assert s and s["kind"] == "新品爆发"


def _months(values, last="2026-09"):
    y, m = int(last[:4]), int(last[5:])
    out = []
    for v in reversed(values):
        out.append([f"{y}-{m:02d}", v, v * 100, 100])
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def test_sustained_hot(cfg):
    d = make([40] * 120)
    hot = evaluate_hot(_months([1200, 1210, 1190, 1205, 1220, 1215]), d, END, 900, cfg["thresholds"]["hot"])
    assert hot and hot["months_hit"] == 4


def test_volatile_or_collapsing_seller_is_not_hot(cfg):
    th = cfg["thresholds"]["hot"]
    assert evaluate_hot(_months([300, 2500, 400, 2600, 300, 2400]), make([40] * 120), END, 200, th) is None
    assert evaluate_hot(_months([1200] * 6), make([40] * 90 + [8] * 30), END, 900, th) is None


def test_potential(cfg):
    sales = [round(1.2 * 2.718 ** (i / 38), 1) for i in range(115)]
    d = make(sales)
    rec = {"available": (END - timedelta(days=114)).isoformat(),
           "obs": [{"ratings": 30, "rating": 4.5}],
           "months": _months([90, 200, 420])}
    p = evaluate_potential(rec, d, END, cfg["thresholds"]["potential"])
    assert p and p["recent_ratio"] > 1.5 and p["growth"] > 0.5


def _rec(sales, rating, available="2024-01-01", ratings=400):
    d = make(sales)
    return {"series": {"start": d.start.isoformat(), "bsr": d.bsr, "sales": d.sales, "price": d.price},
            "available": available, "obs": [{"date": END.isoformat(), "ratings": ratings, "rating": rating}]}


def test_low_rating_is_dropped_and_next_one_fills_in(cfg):
    """评分低于 4.0 的爆火商品不上榜、不进外观分析样本，排在后面的依次补位。"""
    surge = [5] * 110 + [18] * 10
    state = {"asins": {"B1": _rec(surge, 3.9), "B2": _rec(surge, 4.0), "B3": _rec(surge, 4.5)}}
    items = analyze.analyze(state, None, ["B1", "B2", "B3"], cfg, END)["items"]
    by_asin = {i["asin"]: i for i in items}
    assert by_asin["B1"]["label"] == "low" and by_asin["B1"]["tags"][0] == "评分 3.9 低于 4.0"
    assert by_asin["B2"]["label"] == by_asin["B3"]["label"] == "surge"
    sec = analyze.sections(items, 2)
    assert [i["asin"] for i in sec["surge"]] == ["B2", "B3"]
    assert sec["counts"]["surge"] == 2 and sec["counts"]["low"] == 1
    assert "B1" not in {i["asin"] for i in analyze.focus_items(items)}


def test_potential_past_its_peak_is_dropped(cfg):
    """实测形态：9 月初才开卖、9 月中旬到峰值、最近一周跌到峰值的三分之一。28 天对比仍是 ×5、月环比 3→379，
    但最近在回落，不应算潜力。"""
    sales = [0] * 85 + [1, 2, 5, 11, 13, 19, 21, 19, 20, 13, 12, 13, 17, 16, 17, 21, 15, 16, 14, 15, 18,
                        19, 21, 17, 13, 11, 9, 9, 9, 7, 7, 5, 6, 5, 4]
    rec = _rec(sales, 4.3, available=(END - timedelta(days=115)).isoformat(), ratings=52)
    rec["months"] = _months([3, 379])
    assert evaluate_potential(rec, Daily.from_record(rec["series"]), END, cfg["thresholds"]["potential"])
    item = analyze.analyze({"asins": {"B1": rec}}, None, ["B1"], cfg, END)["items"][0]
    assert item["label"] == "watch" and not item["potential"] and not item["rising"]
    assert item["tags"][0] == "最近回落（最近3天日均 5.0 件，再之前3天 6.3 件，近4周最高的3天 19.3 件；近7天中位数 6 件，再之前7天 13 件）"
    growing = _rec([round(1.2 * 2.718 ** (i / 38), 1) for i in range(120)], 4.5,
                   available=(END - timedelta(days=114)).isoformat(), ratings=150)
    growing["months"] = _months([90, 200, 420])
    assert analyze.analyze({"asins": {"B2": growing}}, None, ["B2"], cfg, END)["items"][0]["label"] == "potential"


def _surge_item(cfg, sales):
    rec = _rec(sales, 4.5, ratings=400)
    return analyze.analyze({"asins": {"B1": rec}}, None, ["B1"], cfg, END)["items"][0]


def test_peaked_and_falling_is_not_sustained(cfg):
    """实测形态：峰值周日均 76~94，最近一周每天在掉（76→32）。近 7 天仍有 5 天 ≥ 基线 1.5 倍，
    旧规则判成“持续”；现在是回落，不算爆火。"""
    sales = [26] * 100 + [24, 39, 32, 29, 28, 33, 31, 28, 27, 35, 30, 25, 20, 27, 24, 27, 24, 22, 20, 23, 19,
                          23, 28, 20, 21, 23, 19, 16, 30, 55, 64, 67, 94, 90, 94, 76, 65, 58, 48, 39, 32, 32]
    item = _surge_item(cfg, sales)
    assert item["label"] == "watch" and item["surge"] is None
    assert item["tags"][0] == "最近回落（最近3天日均 34.3 件，再之前3天 57.0 件，近4周最高的3天 92.7 件；近7天中位数 48 件，再之前7天 67 件；近7天最高 76 件，之后 6 天没回升，最近一天 32 件）"


def test_surge_shapes_match_their_definitions(cfg):
    climbing = _surge_item(cfg, [14] * 113 + [25, 28, 30, 33, 36, 38, 40])
    assert climbing["label"] == "surge" and climbing["surge"]["kind"] == "仍在上涨"
    assert climbing["surge"]["week_ratio"] >= 1.15 and climbing["surge"]["last3_ratio"] > 1
    steady = _surge_item(cfg, [5] * 106 + [18] * 14)
    assert steady["surge"]["kind"] == "稳在高位" and abs(steady["surge"]["last3_ratio"] - 1) <= 0.15
    # 增量集中在 2 天：短时脉冲，不算爆火
    spike = _surge_item(cfg, [5] * 113 + [5, 5, 5, 12, 12, 80, 90])
    assert spike["label"] == "watch" and spike["tags"] == ["短时脉冲（不算爆火）"]  # 也不算上升中
    # 在高位但最近 3 天比近 7 天低 20% 以上：回落
    dipping = _surge_item(cfg, [5] * 113 + [20, 21, 20, 19, 13, 12, 12])
    assert dipping["label"] == "watch" and dipping["tags"][0] == "最近回落（最近3天日均 12.3 件，再之前3天 20.0 件，近4周最高的3天 20.3 件；近7天最高 21 件，之后 5 天没回升，最近一天 12 件）"


def test_fading_means_past_the_peak_and_still_going_down(cfg):
    """回落 = ① 最近 3 天比近 4 周最高的 3 天低 25% 以上，且比再之前 3 天低 10% 以上；或 ② 近 7 天中位数比再之前 7 天低 15% 以上；
    或 ③ 近 7 天最高那天之后至少 3 天没回升，且最近一天比最高那天低 30% 以上。
    实测形态（梳妆台，10-12 那期）：峰值就在近 7 天里，46 件后每天往下掉到 17 件，旧规则（比最高一周 / 比近 7 天）抓不到；
    实测形态（衣帽架）：两周里从 60 件慢慢降到 35 件，最近 3 天略有回升，① 抓不到，② 抓到。
    冲高两天后回到原来水平的、跌下来以后已经走平的，旧规则算回落，回测里它们之后两周并不比别人差，不算。"""
    peak_inside_week = _surge_item(cfg, [12] * 110 + [13, 12, 16, 13, 13, 32, 46, 37, 26, 17])
    assert peak_inside_week["label"] == "watch" and peak_inside_week["surge"] is None
    assert peak_inside_week["tags"][0] == "最近回落（最近3天日均 26.7 件，再之前3天 30.3 件，近4周最高的3天 38.3 件；近7天最高 46 件，之后 3 天没回升，最近一天 17 件）"
    back_to_level = _surge_item(cfg, [5] * 90 + [12] * 20 + [12, 12, 40, 38, 13, 12, 13, 12, 12, 13])
    assert not any(t.startswith("最近回落") for t in back_to_level["tags"])
    flat_after_drop = _surge_item(cfg, [5] * 92 + [30] * 14 + [12] * 14)
    assert not any(t.startswith("最近回落") for t in flat_after_drop["tags"])
    # 实测形态（餐边柜，10-12 那期）：4 天前冲到 405 件，之后 338、232、164，旧规则判“仍在上涨”（最近 3 天日均高于近 7 天）
    spike = _surge_item(cfg, [48] * 106 + [119, 79, 54, 49, 49, 49, 45, 45, 43, 237, 405, 338, 232, 164])
    assert spike["label"] == "watch" and spike["surge"] is None
    assert spike["tags"][0] == "最近回落（近7天最高 405 件，之后 3 天没回升，最近一天 164 件）"
    slow = _surge_item(cfg, [20] * 106 + [60, 59, 50, 48, 47, 48, 43, 42, 42, 33, 30, 36, 37, 35])
    assert slow["label"] == "watch" and not slow["rising"]
    assert slow["tags"][0] == "最近回落（近7天中位数 36 件，再之前7天 48 件）"


def test_low_rated_potential_is_dropped(cfg):
    potential = [round(1.2 * 2.718 ** (i / 38), 1) for i in range(120)]
    rec = _rec(potential, 3.6, available=(END - timedelta(days=114)).isoformat(), ratings=30)
    rec["months"] = _months([90, 200, 420])
    assert evaluate_potential(rec, Daily.from_record(rec["series"]), END, cfg["thresholds"]["potential"])
    item = analyze.analyze({"asins": {"B1": rec}}, None, ["B1"], cfg, END)["items"][0]
    assert item["label"] == "low" and "潜力" in item["tags"]


def test_rating_prefers_latest_daily_data():
    assert current_rating({"obs": [{"rating": 4.6, "ratings": 80}]}, {"rating": 3.6, "ratings": 70}) == (4.6, 80)
    assert current_rating({"obs": []}, {"rating": 3.6, "ratings": 70}) == (3.6, 70)  # 月度快照可能过时，只作后备


def test_few_reviews_need_a_higher_rating(cfg):
    """评论少时评分不稳：实测卖家精灵 4.1 分 30 条，亚马逊已是 3.9 分 33 条。"""
    th = cfg["thresholds"]
    assert rating_floor(500, th) == 4.0 and rating_floor(30, th) == 4.2 and rating_floor(None, th) == 4.0
    assert low_rating(3.9, 500, th) and not low_rating(4.0, 500, th)
    assert low_rating(4.1, 30, th) and low_rating(4.0, 99, th) and not low_rating(4.2, 30, th)
    assert not low_rating(None, 0, th) and not low_rating(0, 0, th)  # 还没有评论的新品不受限
    surge = [5] * 110 + [18] * 10
    state = {"asins": {"B1": _rec(surge, 4.1, ratings=30), "B2": _rec(surge, 4.1, ratings=300)}}
    by_asin = {i["asin"]: i for i in analyze.analyze(state, None, ["B1", "B2"], cfg, END)["items"]}
    assert by_asin["B1"]["label"] == "low" and by_asin["B1"]["tags"][0] == "评论仅 30 条，评分 4.1 低于 4.2"
    assert by_asin["B2"]["label"] == "surge"


# ---------------------------------------------------------------- 假爆火 ----

def test_review_rate_signal_uses_node_median(cfg):
    f = cfg["thresholds"]["fake"]
    row = {"ratings_rate": 14.0, "ratings_cv": 30}
    result = fakehot.score({"obs": []}, None, None, row, 1.8, f, [])
    assert [s["code"] for s in result["signals"]] == ["review_rate"]
    normal = fakehot.score({"obs": []}, None, None, {"ratings_rate": 3.0, "ratings_cv": 30}, 1.8, f, [])
    assert normal["score"] == 0


def test_review_velocity_between_runs(cfg):
    f = cfg["thresholds"]["fake"]
    d = make([10] * 120)
    rec = {"variations": 2, "available": "2024-01-01",
           "obs": [{"date": (END - timedelta(days=3)).isoformat(), "ratings": 500, "rating": 4.3},
                   {"date": END.isoformat(), "ratings": 580, "rating": 4.6}]}
    result = fakehot.score(rec, d, END, None, 2.0, f, [])
    codes = {s["code"] for s in result["signals"]}
    assert {"review_velocity", "rating_jump"} <= codes and result["level"] == "suspect"


def test_variation_merge_is_tagged_not_scored(cfg):
    f = cfg["thresholds"]["fake"]
    d = make([10] * 120)
    rec = {"variations": 3, "parent": "P1", "available": "2024-01-01",
           "obs": [{"date": (END - timedelta(days=3)).isoformat(), "ratings": 500, "rating": 4.4},
                   {"date": END.isoformat(), "ratings": 2900, "rating": 4.4}],
           "checks": {"detail": {"parent": "P2", "variations": 24, "checked": END.isoformat()}}}
    result = fakehot.score(rec, d, END, {"parent": "P1", "variations": 3}, 2.0, f, [])
    assert "变体合并" in result["tags"] and result["score"] == 0


def test_many_variations_skip_review_ratio(cfg):
    f = cfg["thresholds"]["fake"]
    d = make([10] * 120)
    rec = {"variations": 30, "available": "2024-01-01",
           "obs": [{"date": (END - timedelta(days=3)).isoformat(), "ratings": 500, "rating": 4.4},
                   {"date": END.isoformat(), "ratings": 560, "rating": 4.4}]}
    assert fakehot.score(rec, d, END, None, 2.0, f, [])["score"] == 0


def test_review_burst_summary():
    def ms(day):
        from radar.demo import _ms
        return str(_ms(day))

    rows = [{"date": ms(END - timedelta(days=2)), "verified": "True", "vine": "False", "star": "5"}] * 12 + \
           [{"date": ms(END - timedelta(days=10 + i)), "verified": "False", "vine": "False", "star": "5"} for i in range(8)]
    summary = summarize_reviews(rows, END)
    assert summary["n"] == 20 and summary["burst_share"] >= 0.7 and summary["unverified_share"] == 0.4


def test_restock_is_not_a_surge(cfg):
    """断货（日销 0、BSR 几百万）后恢复供货：看起来像暴涨，其实只是回到原来的水平。"""
    d = make([80] * 80 + [0] * 26 + [80] * 14)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg))
    assert s and s["restock"] and s["kind"] == "断货恢复"


def test_many_variations_skip_new_listing_ratio(cfg):
    """24 个变体共享父体评论：新品评论/销量比没有意义，不应计分。"""
    f = cfg["thresholds"]["fake"]
    d = make([0] * 50 + [80] * 70)
    rec = {"variations": 24, "available": (END - timedelta(days=60)).isoformat(),
           "obs": [{"date": END.isoformat(), "ratings": 1393, "rating": 4.4}],
           "months": _months([2482, 352])}
    result = fakehot.score(rec, d, END, None, 2.0, f, [])
    assert "new_listing_reviews" not in {s["code"] for s in result["signals"]}


def test_period_diff_lists_each_product_once():
    from radar.detect import diff
    prev = {"id": "p", "labels": {"A": ["surge", 0], "B": ["surge", 0], "C": ["surge", 0], "D": ["potential", 0],
                                  "E": ["watch", 0], "F": ["surge", 0]}}
    now = {"A": "surge", "B": "hot", "C": "watch", "D": "surge", "E": "potential", "G": "surge"}
    d = diff.compare(prev, now)
    assert d["still"] == ["A"]                                        # 依旧爆火
    assert d["new"]["surge"] == ["D", "G"] and d["new"]["hot"] == ["B"] and d["new"]["potential"] == ["E"]
    assert d["moved_from"] == {"B": "surge", "D": "potential"}        # 上期在别的板块的注明来源
    assert d["cooled"] == ["C"]                                       # 转去持续热销的 B 不算不再爆火；F 已不在追踪
    listed = d["still"] + [a for v in d["new"].values() for a in v] + d["cooled"]
    assert len(listed) == len(set(listed))


def test_section_popups_list_every_item_by_default(cfg):
    """顶部数字和小窗里的个数要一致：report.top_n 默认 0 = 全部列出（实测持续热销 31 个，小窗只列了前 15 个）。"""
    assert cfg["report"]["top_n"] == 0
    items = [{"label": "hot", "hot": {"avg_monthly": float(k)}, "asin": f"H{k}"} for k in range(31)]
    sec = analyze.sections(items, cfg["report"]["top_n"])
    assert len(sec["hot"]) == sec["counts"]["hot"] == 31
    assert len(analyze.sections(items, 15)["hot"]) == 15


def test_relisted_listing_needs_inherited_reviews(cfg):
    """老品换链接：评论 ≥ 自己累计销量的 50%，或 ≥15% 且第一周就满速。只是第一周卖得多、评论正常的
    （大卖家投广告推起来的真新品）不算；上架超过 180 天的不看。"""
    from radar.detect.rules import relisted

    th = cfg["thresholds"]["relist"]
    fast = make([0] * 70 + [150] * 50)               # 第一天就满速，累计 7500 件
    assert relisted(fast, len(fast) - 1, 2315, 50, th)["fast"]       # 31% 且第一周满速
    assert relisted(fast, len(fast) - 1, 200, 50, th) is None         # 2.7%：正常留评率
    assert relisted(fast, len(fast) - 1, 5000, 200, th) is None       # 上架超过 180 天
    ramp_sales = [0] * 70 + [1 + i * 0.6 for i in range(50)]
    ramp = make(ramp_sales)
    total = sum(ramp_sales)
    r = relisted(ramp, len(ramp) - 1, int(total * 0.6), 50, th)
    assert r and not r["fast"] and r["share"] >= 0.5
    assert relisted(ramp, len(ramp) - 1, int(total * 0.2), 50, th) is None  # 20%，慢慢起量


def test_relisted_listing_is_not_potential_or_a_new_listing_surge(cfg):
    """老品换链接不算潜力、不算新品爆发（也不标新品起量），卡片上写明依据；评论不多的同款照常判。"""
    ramp = [round(0.6 * 2.718 ** (i / 38), 1) for i in range(115)]
    new = [0] * 113 + [60] * 7
    state = {"asins": {
        "P1": {**_rec(ramp, 4.5, available=(END - timedelta(days=114)).isoformat(), ratings=250),
               "months": _months([45, 100, 210])},
        "P2": {**_rec(ramp, 4.5, available=(END - timedelta(days=114)).isoformat(), ratings=20),
               "months": _months([45, 100, 210])},
        "N1": _rec(new, 4.6, available=(END - timedelta(days=6)).isoformat(), ratings=300),
        "N2": _rec(new, 4.6, available=(END - timedelta(days=6)).isoformat(), ratings=0),
    }}
    by = {i["asin"]: i for i in analyze.analyze(state, None, list(state["asins"]), cfg, END)["items"]}
    assert by["P2"]["label"] == "potential" and by["P1"]["label"] != "potential" and by["P1"]["potential"] is None
    assert by["P1"]["relist"]["share"] >= 0.5 and any(t.startswith("老品换链接（上架 114 天已有 250 条评论") for t in by["P1"]["tags"])
    assert by["N2"]["label"] == "surge" and by["N2"]["surge"]["kind"] == "新品爆发"
    assert by["N1"]["label"] != "surge" and not any(t.startswith("新品起量") for t in by["N1"]["tags"])


def test_relisted_listing_skips_new_listing_review_ratio(cfg):
    f = cfg["thresholds"]["fake"]
    d = make([0] * 50 + [80] * 70)
    rec = {"variations": 2, "available": (END - timedelta(days=60)).isoformat(),
           "obs": [{"date": END.isoformat(), "ratings": 1393, "rating": 4.4}], "months": _months([2482, 352])}
    assert "new_listing_reviews" in {s["code"] for s in fakehot.score(rec, d, END, None, 2.0, f, [])["signals"]}
    assert fakehot.score(rec, d, END, None, 2.0, f, [], relisted=True)["score"] == 0


def test_review_rate_skips_many_variations(cfg):
    """留评率用的评论数是父体共享的：变体多的商品不算（和两种评论/销量比一致）。"""
    f = cfg["thresholds"]["fake"]
    row = {"ratings_rate": 14.0, "ratings_cv": 30}
    assert fakehot.score({"obs": [], "variations": 2}, None, None, row, 1.8, f, [])["score"] == 2
    assert fakehot.score({"obs": [], "variations": 15}, None, None, row, 1.8, f, [])["score"] == 0


def test_node_median_review_rate_ignores_many_variations(cfg):
    products = {f"A{k}": {"asin": f"A{k}", "node": "N", "sources": ["top"], "units": 100, "ratings_rate": rate,
                          "variations": vars_} for k, (rate, vars_) in enumerate([(1.0, 1), (2.0, 2), (3.0, 3), (40.0, 20), (50.0, 30)])}
    assert analyze.node_stats({"products": products}, cfg)["N"]["median_rate"] == 2.0


def test_relisted_increment_is_zero_without_earlier_sales():
    from radar.detect import directions

    item = {"recent_avg28": 100, "prev_avg28": None, "price": 50}
    assert directions.increment(item) == 100 * 50 * 30
    assert directions.increment({**item, "relist": {"share": 0.6}}) == 0
    assert directions.increment({**item, "prev_avg28": 40, "relist": {"share": 0.6}}) == 60 * 50 * 30


def _category_history(level, months=36, end=(2026, 9)):
    """state["category_history"] 里一个子类目的 36 个月销量，level(月份) = 当月日均。"""
    import calendar
    out = {}
    y, m = end
    for _ in range(months):
        out[f"{y}-{m:02d}"] = level(m) * calendar.monthrange(y, m)[1]
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def _with_history(state, path, units):
    hist = state.setdefault("category_history", {"months": {}, "scanned": {}})
    for month, v in units.items():
        hist["months"].setdefault(month, {})[path] = v
    return state


def test_growth_in_a_seasonal_category_is_tagged(cfg):
    """季节性品类里，按往年规律近 28 天品类本来就是之前的 ≥1.15 倍：上升中标“品类季节性”，写明季节倍数和扣除后的倍数；
    没有类目历史的子类目不标。"""
    early_autumn = lambda m: 100 * {9: 1.6, 10: 2.2}.get(m, 1.0)
    state = _with_history({"asins": {}}, "N1", _category_history(early_autumn))
    rising = [10] * 92 + [20] * 28
    for k in range(3):
        state["asins"][f"S{k}"] = {**_rec(rising, 4.5), "node": "N1", "node_cn": "衣帽架"}
        state["asins"][f"F{k}"] = {**_rec(rising, 4.5), "node": "N2", "node_cn": "书架"}
    result = analyze.analyze(state, None, list(state["asins"]), cfg, END)
    by = {i["asin"]: i for i in result["items"]}
    assert result["seasons"]["N1"]["seasonal"] and not result["seasons"]["N2"]["ready"]
    s = by["S0"]
    assert s["rising"] and s["season28"] > 1.15
    tag = next(t for t in s["tags"] if t.startswith("品类季节性"))
    assert tag == (f"品类季节性（按往年规律，这段时间品类本来就是之前的 {s['season28']:.1f} 倍，"
                   f"扣除后 ×{s['rising']['ratio'] / s['season28']:.1f}）")
    assert by["F0"]["rising"] and by["F0"]["season28"] is None
    assert not any(t.startswith("品类季节性") for t in by["F0"]["tags"])


def test_hot_counts_raw_or_seasonally_adjusted_sales(cfg):
    """持续热销：原始销量达标的照样算；季节性品类里原始销量因为旺季波动不达标的，按季节指数调整后达标也算（卡片注明）。"""
    from radar.detect import seasonality

    th = cfg["thresholds"]
    peak = lambda m: 100 * {7: 1.6, 8: 2.6, 9: 1.8}.get(m, 1.0)
    prof = seasonality.profile(_category_history(peak), th["seasonality"])
    assert prof["seasonal"]
    season = seasonality.hot_levels(prof, END)
    seasonal_months = _months([1000 * x for x in (1.0, 1.0, 1.0, 1.6, 2.6, 1.8)])
    d = make([60] * 120)
    assert evaluate_hot(seasonal_months, d, END, 900, th["hot"]) is None  # 直接算波动 0.39 > 0.35
    hot = evaluate_hot(seasonal_months, d, END, 900, th["hot"], season, "2026-09")
    assert hot and hot["adjusted"] and hot["cv"] < 0.05 and hot["season_n"] == 3
    assert evaluate_hot(seasonal_months, d, END, 900, {**th["hot"], "deseasonalize": False}, season, "2026-09") is None

    stats = {"N1": {"threshold": 900}}
    flat = {**_rec([40] * 120, 4.5), "node": "N1", "months": _months([1200] * 6)}
    wavy = {**_rec([60] * 120, 4.5), "node": "N1", "months": seasonal_months}
    a = analyze.evaluate("A", flat, None, stats, cfg, END, data_date=END, season=season, threshold_month="2026-09")
    b = analyze.evaluate("B", wavy, None, stats, cfg, END, data_date=END, season=season, threshold_month="2026-09")
    assert a["label"] == "hot" and not a["hot"]["adjusted"]  # 原始销量就稳：不调整（调整反而会显得 8 月掉下去）
    assert b["label"] == "hot" and b["hot"]["adjusted"]


def test_seasonal_products_are_listed_whatever_their_section(cfg):
    """季节性商品清单：所在季节性品类每年 11~12 月高、商品自己这一年也是 → 标“季节性（每年 11~12 月旺…）”并列出，
    不论在哪个板块（淡季里多半只在观察）；离旺季近的排前面、同样近的按旺季日均；评分不达标的照样标、但不列；
    没有类目历史的子类目里同样形状的商品不判断。"""
    from radar.report import render

    winter = lambda m: 100 * {11: 2.2, 12: 2.8}.get(m, 1.0)
    state = _with_history({"asins": {}}, "N1", _category_history(winter))
    shape = lambda base: _yearly(lambda d: 3 * base if d.month in (11, 12) else base)
    state["asins"] = {"SMALL": {**_rec(shape(10), 4.5), "node": "N1"}, "BIG": {**_rec(shape(40), 4.5), "node": "N1"},
                      "LOW": {**_rec(shape(20), 3.5), "node": "N1"}, "NOHIST": {**_rec(shape(20), 4.5), "node": "N2"},
                      "FLAT": {**_rec([20] * 420, 4.5), "node": "N1"}}
    result = analyze.analyze(state, None, list(state["asins"]), cfg, END)
    assert result["profiles"]["N1"]["seasonal"] and result["profiles"]["N1"]["peak_months"] == [11, 12]
    by = {i["asin"]: i for i in result["items"]}
    sec = analyze.sections(result["items"], 0)
    assert [i["asin"] for i in sec["seasonal"]] == ["BIG", "SMALL"] and sec["counts"]["seasonal"] == 2
    assert by["BIG"]["label"] == "watch" and by["LOW"]["label"] == "low"
    ratio = by["SMALL"]["item_season"]["ratio"]
    assert f"季节性（每年 11~12 月旺，旺季日均至少是前后淡季的 {ratio:.1f} 倍）" in by["SMALL"]["tags"]
    assert any(t.startswith("季节性（") for t in by["LOW"]["tags"])
    assert by["NOHIST"]["item_season"] is None and not by["FLAT"]["item_season"]["seasonal"]
    assert render.season_text(by["SMALL"]) == f"每年 11~12 月旺，旺季日均至少是前后淡季的 {ratio:.1f} 倍，离旺季还有 1 个月"

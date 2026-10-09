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
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg), [])
    assert s and s["kind"] == "持续型" and s["sales_ratio"] > 2.5 and not s["price_driven"]


def test_short_pulse_is_not_a_surge_but_scores_as_anomaly(cfg):
    sales = [6] * 120
    sales[-9] = sales[-8] = 40
    d = make(sales)
    assert evaluate_surge(d, len(d) - 1, surge_cfg(cfg), []) is None  # BSR 中位数没动
    f = cfg["thresholds"]["fake"]
    assert find_pulses(d, len(d) - 1, 30, f["pulse_mult"], f["pulse_min_sales"], f["pulse_revert"])


def test_small_base_noise_is_ignored(cfg):
    d = make([0, 1] * 55 + [2, 3] * 5)  # 从 0.5 涨到 2.5，但绝对量太小
    assert evaluate_surge(d, len(d) - 1, surge_cfg(cfg), []) is None


def test_price_driven_surge(cfg):
    d = make([5] * 110 + [16] * 10, prices=[200.0] * 110 + [140.0] * 10)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg), [])
    assert s and s["price_driven"]


def test_seasonal_surge(cfg):
    sales = [5] * 420
    for i in list(range(420 - 10, 420)) + list(range(420 - 10 - 364, 420 - 364)):
        sales[i] = 18
    d = make(sales)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg), [])
    assert s and s["seasonal"]


def test_deal_window_is_tagged(cfg):
    d = make([5] * 110 + [18] * 10)
    deals = [{"name": "黑五", "start": (END - timedelta(days=3)).isoformat(), "end": END.isoformat()}]
    assert evaluate_surge(d, len(d) - 1, surge_cfg(cfg), deals)["deal"] == "黑五"


def test_new_listing_ramp_is_from_zero(cfg):
    d = make([0] * 113 + [6] * 7)
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg), [])
    assert s and s["kind"] == "新品起量"


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
    assert item["tags"][0].startswith("最近回落（近7天日均为近28天的 47%")
    growing = _rec([round(1.2 * 2.718 ** (i / 38), 1) for i in range(120)], 4.5,
                   available=(END - timedelta(days=114)).isoformat(), ratings=150)
    growing["months"] = _months([90, 200, 420])
    assert analyze.analyze({"asins": {"B2": growing}}, None, ["B2"], cfg, END)["items"][0]["label"] == "potential"


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
    s = evaluate_surge(d, len(d) - 1, surge_cfg(cfg), [])
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

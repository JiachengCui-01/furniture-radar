"""业务规则回归：自然月窗口、近期销量证据、缺失与断货、从零脉冲。"""
from datetime import timedelta

import pytest

from radar import analyze
from radar.detect.rules import evaluate_hot, evaluate_surge
from radar.series import closed_months
from tests.test_detection import END, _rec, make


def months(keys):
    return [[f"2026-{month:02d}", 1200, 120000, 100] for month in keys]


def test_hot_uses_calendar_months_instead_of_last_available_rows(cfg):
    # 当前是 10 月：只有 6、7 月属于近四个月，4、5 月不能凑数。
    assert evaluate_hot(months([4, 5, 6, 7]), make([40] * 120), END, 900, cfg["thresholds"]["hot"]) is None
    hot = evaluate_hot(months([4, 5, 7, 8, 9]), make([40] * 120), END, 900, cfg["thresholds"]["hot"])
    assert hot and hot["months_hit"] == 3 and hot["lookback"] == 4


def test_hot_ignores_ancient_months_for_variation_and_future_months(cfg):
    data = [["2025-12", 20000, 2000000, 100], *months(range(4, 10)), ["2026-11", 20000, 2000000, 100]]
    hot = evaluate_hot(data, make([40] * 120), END, 900, cfg["thresholds"]["hot"])
    assert hot and hot["cv"] == 0 and hot["avg_monthly"] == 1200
    assert all(key < "2026-10" for key, _ in closed_months(data, END))


def test_hot_monthly_trend_preserves_missing_month_distance(cfg):
    # 7 月 1200、8 月缺失、9 月 900：每月约跌 13.4%，不能当成一个月跌 25%。
    data = [["2026-06", 1600, 160000, 100], ["2026-07", 1200, 120000, 100], ["2026-09", 900, 90000, 100]]
    hot = evaluate_hot(data, make([40] * 120), END, 800, cfg["thresholds"]["hot"])
    assert hot and -0.15 < hot["trend"] < -0.13


def test_hot_requires_recent_sales_even_when_bsr_is_fresh(cfg):
    d = make([40] * 120)
    d.sales[-28:] = [None] * 28
    data = months(range(4, 10))
    assert evaluate_hot(data, d, END, 900, cfg["thresholds"]["hot"]) is None
    assert evaluate_hot(data, None, END, 900, cfg["thresholds"]["hot"]) is None
    assert evaluate_hot(data, None, END, 900, cfg["thresholds"]["hot"], recent_daily=40)


def test_potential_uses_recent_calendar_months_and_preserves_gaps(cfg):
    rec = _rec([10] * 92 + [15] * 28, 4.5, available=(END - timedelta(days=170)).isoformat(), ratings=40)
    rec["months"] = [["2026-06", 144, 14400, 100], ["2026-07", 100, 10000, 100], ["2026-09", 144, 14400, 100]]
    item = analyze.evaluate("MONTH_GAP", rec, None, {}, cfg, END)
    assert item["label"] == "potential" and 0.19 < item["potential"]["growth"] < 0.21


def test_missing_baseline_days_do_not_prove_restock(cfg):
    rec = _rec([20] * 85 + [5] * 7 + [0] * 21 + [40] * 7, 4.5)
    rec["series"]["sales"][92:113] = [None] * 21
    rec["series"]["bsr"][92:113] = [None] * 21
    item = analyze.evaluate("GAP", rec, None, {}, cfg, END)
    assert not item["restock"] and item["label"] == "surge"
    assert item["surge"]["kind"] == "稳在高位"


def test_high_bsr_still_proves_restock_when_sales_are_missing(cfg):
    d = make([20] * 85 + [5] * 7 + [0] * 21 + [40] * 7)
    d.sales[92:113] = [None] * 21
    d.bsr[92:113] = [8000000] * 21
    s = evaluate_surge(d, 119, cfg["thresholds"]["surge"], cfg["thresholds"]["out_of_stock_bsr"])
    assert s and s["restock"] and s["zero_days"] >= 7


@pytest.mark.parametrize("age", [60, 1000])
@pytest.mark.parametrize("peak", [100, 350])
def test_from_zero_pulse_is_not_a_surge_even_for_revived_old_links(cfg, age, peak):
    rec = _rec([0] * 119 + [peak], 4.5, available=(END - timedelta(days=age)).isoformat(), ratings=0)
    rec["series"]["bsr"][:113] = [None] * 113
    item = analyze.evaluate("PULSE", rec, None, {}, cfg, END)
    assert item["label"] == "watch" and not item["surge"] and not item["rising"]
    assert "短时脉冲（不算爆火）" in item["tags"]
    assert not any(t.startswith(("新品起量", "老链接重新起量")) for t in item["tags"])


def test_sustained_from_zero_launch_still_qualifies(cfg):
    rec = _rec([0] * 113 + [40] * 7, 4.5, available=(END - timedelta(days=60)).isoformat(), ratings=0)
    rec["series"]["bsr"][:113] = [None] * 113
    item = analyze.evaluate("LAUNCH", rec, None, {}, cfg, END)
    assert item["label"] == "surge" and item["surge"]["kind"] == "新品爆发"

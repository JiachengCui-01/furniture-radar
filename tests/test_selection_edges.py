"""真实复现的组合边界：窗口独立筛选、整批时效、未知品牌证据。"""
from copy import deepcopy
from datetime import timedelta

import pytest

from radar import analyze, tracking, vision
from radar.detect import design, directions, traits
from tests.test_detection import END, _rec
from tests.test_design import _dir_item, _head_row


@pytest.mark.parametrize("windows", [[7, 14], [14, 7]])
def test_qualified_fourteen_day_window_survives_stronger_raw_seven_day_window(cfg, windows):
    rec = _rec([5] * 78 + [1] * 7 + [20 / 3] * 21 + [12] * 7 + [31] * 7, 4.5)
    cfg["thresholds"]["surge"]["windows"] = windows
    item = analyze.evaluate("WINDOWS", rec, None, {}, cfg, END, tide=2)
    assert item["label"] == "surge" and item["surge"]["window"] == 14
    assert item["surge"]["sales_ratio"] / 2 > 2
    assert not any(t.startswith(("随大盘", "断货恢复", "上涨形态")) for t in item["tags"])
    seven = deepcopy(cfg)
    seven["thresholds"]["surge"]["windows"] = [7]
    assert analyze.evaluate("WINDOWS", rec, None, {}, seven, END, tide=2)["label"] != "surge"


def test_a_whole_old_batch_is_stale_but_the_original_report_date_is_valid(cfg):
    state = {"asins": {"A": _rec([5] * 110 + [18] * 10, 4.5)}}
    live = analyze.analyze(state, None, ["A"], cfg, END + timedelta(days=14))
    old = live["items"][0]
    assert old["stale"] and old["label"] == "watch" and not old["rising"]
    assert "数据陈旧" in old["tags"]
    historical = analyze.analyze(state, None, ["A"], cfg, END)
    assert historical["items"][0]["label"] == "surge" and not historical["items"][0]["stale"]


@pytest.mark.parametrize("days,stale", [(2, False), (3, True)])
def test_absolute_freshness_reuses_the_existing_stale_days_limit(cfg, days, stale):
    item = analyze.evaluate("A", _rec([5] * 110 + [18] * 10, 4.5), None, {}, cfg,
                            END + timedelta(days=days), data_date=END)
    assert item["stale"] is stale


def test_an_expired_fetch_is_retried_even_in_the_same_unfinished_run(cfg):
    cfg["budget"]["stable_refresh_every"] = 99
    rec = {**_rec([5] * 120, 4.5), "fetched_run": 5, "stale_fetch": True}
    state = {"asins": {"A": rec}}
    assert tracking.plan_fetch(["A"], state, 5, cfg, 10) == ["A"]


@pytest.mark.parametrize("brands,expected", [
    ([None, "", " "], 0), (["A", "", None], 1), (["A", " a ", None], 1), (["A", "B", None], 2),
])
def test_title_image_and_direction_agree_on_known_brand_support(cfg, brands, expected):
    focus = [_dir_item(f"U{k}", "书架", "Fluted Bookshelf", brand) for k, brand in enumerate(brands)]
    heads = [_head_row(f"H{k}", "书架", "Bookshelf", 1000 - k) for k in range(20)]
    tr = traits.compute(heads, focus, END, cfg["thresholds"]["appearance"])
    assert tr["title_shares"]["工艺"]["凹槽竖纹"]["brands"] == expected
    state = {"asins": {i["asin"]: {"vision": {"tags": {"工艺": ["凹槽竖纹"]}}} for i in focus}}
    state["asins"].update({i["asin"]: {"vision": {"tags": {}}} for i in heads})
    images = vision.summarize(focus, heads, state)
    assert images["dimensions"]["工艺"][0]["brands"] == expected
    appearance = traits.merge_appearance(tr, images, cfg)
    assert bool(appearance.get("工艺", {}).get("more")) is (expected >= 2)
    assert bool(directions.ranked(focus, cfg)) is (expected >= 2)
    # 缺品牌的商品仍保留在样本，不能把未知误当成新品牌。
    assert images["n_focus"] == 3 and tr["n_focus"] == 3
    assert design.known_brand_key({"brand": " "}) is None

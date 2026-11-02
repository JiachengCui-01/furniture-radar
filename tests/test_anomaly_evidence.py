"""异常证据完整性、时间窗口与商品身份的组合回归，不调用真实服务。"""
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
import json

import pytest

from radar import analyze, evidence, tracking, verify
from radar.detect import fakehot
from radar.series import Daily, sum_sales_between
from radar.vendor import Budget, Vendor

END = date(2026, 10, 6)
START = END - timedelta(days=119)


def daily(tail=None):
    values = [100] * 120 if tail is None else [100] * (120 - len(tail)) + tail
    return Daily(START, [50000] * 120, values, [50] * 120)


def record(d=None):
    d = d or daily()
    return {"available": "2024-01-01", "parent": "P1", "variations": 1,
            "series": {"start": START.isoformat(), "bsr": d.bsr, "sales": d.sales, "price": d.price},
            "obs": [{"date": (END - timedelta(days=14)).isoformat(), "ratings": 100, "rating": 4.3, "parent": "P1"},
                    {"date": END.isoformat(), "ratings": 120, "rating": 4.5, "parent": "P1"}]}


def codes(rec, cfg, row=None, d=None):
    d = d or Daily.from_record(rec["series"])
    return fakehot.score(rec, d, END, row, 2.0, cfg["thresholds"]["fake"], today=END)


def review_check(day=END, **extra):
    return {"n": 20, "top3": 15, "span": 20, "total": 20, "verified_n": 20, "vine_n": 20,
            "unverified_share": 0.5, "vine_share": 0, "checked": day.isoformat(), "as_of": day.isoformat(),
            "parent": "P1", **extra}


@pytest.mark.parametrize("tail,expected", [([None] * 14, 1), ([50] + [None] * 13, 1), ([0] * 14, 3), ([100] * 14, 1)])
def test_missing_sales_cannot_prove_no_sales_or_a_high_review_ratio(cfg, tail, expected):
    rec = record(daily(tail))
    got = codes(rec, cfg)
    assert got["score"] == expected
    if any(v is None for v in tail):
        assert [s["code"] for s in got["signals"]] == ["rating_jump"]
        assert "销量区间不完整，评论增速暂不计分" in got["tags"]


def test_sales_interval_requires_both_boundaries_and_every_day():
    d = daily([0] * 14)
    assert sum_sales_between(d, END - timedelta(days=14), END) == 0
    assert sum_sales_between(d, START - timedelta(days=2), END) is None
    assert sum_sales_between(d, START, END + timedelta(days=1)) is None


@pytest.mark.parametrize("age", [-1, 0, 13, 14, 180, None])
def test_review_cache_needs_a_known_recent_nonfuture_date(cfg, age):
    rec = record()
    rec["obs"] = rec["obs"][-1:]
    check = review_check(END - timedelta(days=age or 0))
    if age is None:
        check.pop("checked")
    rec["checks"] = {"review": check}
    assert codes(rec, cfg)["score"] == (3 if age in (0, 13) else 0)


@pytest.mark.parametrize("check", [{"n": 4, "total": 4, "vine_share": 0}, {"n": 20, "total": 300, "vine_share": 1}])
def test_expired_samples_cannot_exempt_current_review_rates(cfg, check):
    rec = record()
    rec["obs"] = [{"date": END.isoformat(), "ratings": 500, "rating": 4.5}]
    row = {"ratings": 500, "ratings_rate": 14, "ratings_cv": 30}
    rec["checks"] = {"review": {**check, "verified_n": check["n"], "vine_n": check["n"], "checked": END.isoformat()}}
    assert not any(s["code"] == "review_rate" for s in codes(rec, cfg, row)["signals"])
    rec["checks"]["review"]["checked"] = (END - timedelta(days=14)).isoformat()
    assert [s["code"] for s in codes(rec, cfg, row)["signals"]] == ["review_rate"]


def test_future_checks_and_family_cannot_leak_into_history(cfg):
    rec = record()
    then = END - timedelta(days=2)
    rec["obs"] = [{"date": then.isoformat(), "ratings": 120, "rating": 4.5, "parent": "OLD"},
                  {"date": END.isoformat(), "ratings": 500, "rating": 4.5, "parent": "NEW"}]
    rec["parent"] = "NEW"
    rec["checks"] = {"review": review_check(parent="NEW")}
    cut = analyze.truncated_rec(rec, then)
    assert cut["parent"] == "OLD" and not cut["checks"]
    # 当时已经有的旧父体证据应保留，不能被当前父体误排除。
    rec["checks"]["review"] = review_check(day=then, parent="OLD")
    cut = analyze.truncated_rec(rec, then)
    assert cut["checks"]["review"]["parent"] == "OLD"


def test_cumulative_sales_ignores_future_months_and_falls_back_only_to_complete_daily(cfg):
    launch = date(2026, 9, 1)
    d = daily()
    d.sales = [None if START + timedelta(days=k) < launch else 16 for k in range(120)]
    rec = {"available": launch.isoformat(), "obs": [{"date": END.isoformat(), "ratings": 50, "rating": 4.5}],
           "months": [["2026-09", 480], ["2026-10", 96]]}
    f = cfg["thresholds"]["fake"]
    assert fakehot.score(rec, d, END, None, 2, f)["score"] == 0
    missing = deepcopy(rec)
    missing["months"][0][1] = None
    assert fakehot.cumulative_sales(missing, d, launch, END) == 576
    assert fakehot.score(missing, d, END, None, 2, f)["score"] == 0
    d.sales[-2] = None
    got = fakehot.score(missing, d, END, None, 2, f)
    assert got["score"] == 0 and "累计销量数据不足，新品评论比暂不计分" in got["tags"]
    rec["obs"][0]["ratings"] = 100
    before = fakehot.score(rec, d, END, None, 2, f)
    rec["months"].append(["2026-11", 5000])
    assert fakehot.score(rec, d, END, None, 2, f) == before


def ms(day):
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.mark.parametrize("known,flag,expected", [(0, None, None), (9, False, None), (10, False, 1.0), (10, True, 0.0)])
def test_missing_comment_flags_remain_unknown(cfg, known, flag, expected):
    rows = [{"date": ms(END - timedelta(days=k + 1)), "star": 5,
             **({"verified": flag} if k < known else {})} for k in range(20)]
    check = verify.summarize_reviews(rows, END, total=20)
    assert check["verified_n"] == known and check["unverified_share"] == expected and check["vine_share"] is None
    check["checked"] = END.isoformat()
    got = fakehot.score({"checks": {"review": check}}, None, None,
                        {"ratings_rate": 14, "ratings_cv": 30}, 2, cfg["thresholds"]["fake"], today=END)
    assert got["score"] == (3 if expected == 1 else 2)


def test_review_query_and_filter_share_the_same_end_date(cfg):
    calls = []
    rows = [{"date": ms(END - timedelta(days=k % 3)), "verified": True, "vine": False} for k in range(20)]
    def transport(tool, args):
        calls.append(args.get("request") or args)
        return json.dumps({"code": "OK", "data": {"items": rows, "total": 100}})
    state = {"asins": {"M": {"obs": [{"date": (END - timedelta(days=20)).isoformat(), "ratings": 100}]}}}
    done = verify.run_checks(Vendor(transport, Budget(5), live=False), [("M", "review")], state, cfg, END)
    check = state["asins"]["M"]["checks"]["review"]
    assert done == 1 and check["n"] == 20 and check["as_of"] == END.isoformat()
    assert calls[0]["endTimestamp"] == ms(END + timedelta(days=1)) - 1
    # 即便上游忽略结束时间，也不能把全部窗口外结果误保存为本期有效核查。
    state = {"asins": {"D": {}}}
    done = verify.run_checks(Vendor(transport, Budget(5), live=False), [("D", "review")], state, cfg, END,
                            as_ofs={"D": END - timedelta(days=3)})
    assert done == 0 and "review" not in state["asins"]["D"]["checks"]


def prediction(rating=4.5, ratings=120):
    data = {"asinDetail": {"rating": rating, "ratings": ratings},
            "dailyItemList": [{"date": (START + timedelta(days=k)).isoformat(), "bsr": 50000, "sales": 3, "price": 50}
                              for k in range(120)]}
    return Vendor(lambda tool, args: json.dumps({"code": "OK", "data": data}), Budget(5), live=False)


def test_same_day_rating_updates_and_partial_fields_are_preserved(cfg):
    rec = record()
    rec["obs"] = rec["obs"][:1]
    state = {"asins": {"A": rec}}
    tracking.fetch_one(prediction(4.3), "A", state, cfg, 1, None)
    assert codes(rec, cfg)["score"] == 2
    tracking.fetch_one(prediction(4.5), "A", state, cfg, 1, None)
    assert rec["obs"][-1]["rating"] == 4.5 and codes(rec, cfg)["score"] == 3
    tracking.fetch_one(prediction(4.6, None), "A", state, cfg, 1, None)
    assert rec["obs"][-1]["ratings"] == 120
    tracking.fetch_one(prediction(None, 125), "A", state, cfg, 1, None)
    assert rec["obs"][-1]["rating"] == 4.6
    count = len(rec["obs"])
    tracking.fetch_one(prediction(None, 125), "A", state, cfg, 1, None)
    assert len(rec["obs"]) == count


def test_parent_snapshot_survives_fetch_and_invalidates_old_family_review(cfg):
    rec = record()
    rec["obs"] = [{"date": (END - timedelta(days=14)).isoformat(), "ratings": 500, "rating": 4.5}]
    rec["checks"] = {"review": review_check(day=END - timedelta(days=2), parent="P1")}
    state = {"asins": {"A": rec}}
    row = {"parent": "P2", "variations": 4, "ratings_rate": 14, "ratings_cv": 80}
    tracking.fetch_one(prediction(ratings=2900), "A", state, cfg, 1, row)
    assert rec["obs"][0]["parent"] == "P1" and rec["obs"][-1]["parent"] == "P2"
    assert evidence.usable_check(rec, "review", END) is None
    def transport(tool, args):
        return json.dumps({"code": "OK", "data": {"parent": "P2", "variations": 4, "ratings": 2900}})
    verify.run_checks(Vendor(transport, Budget(5), live=False), [("A", "detail")], state, cfg, END)
    got = codes(rec, cfg, row)
    assert "变体合并" in got["tags"] and got["score"] == 2
    items = [{"asin": "A", "label": "watch", "fake": got, "as_of": END.isoformat()}]
    assert ("A", "review") in verify.select(items, state, cfg, END)


@pytest.mark.parametrize("age,expected", [(0, 2), (14, 3), (-1, 3)])
def test_old_or_future_detail_cannot_hide_current_comment_growth(cfg, age, expected):
    rec = record(daily([3] * 14))
    rec["obs"][0]["rating"] = 4.5
    rec["checks"] = {"detail": {"parent": "P2", "variations": 4, "checked": (END - timedelta(days=age)).isoformat()}}
    got = codes(rec, cfg, {"parent": "P1", "ratings_rate": 14, "ratings_cv": 30})
    assert got["score"] == expected


def test_a_past_merge_does_not_exempt_growth_after_the_family_is_stable(cfg):
    rec = record(daily([3] * 14))
    rec["obs"][0]["rating"] = 4.5
    rec["checks"] = {"detail": {"parent": "P1", "before_parent": "OLD", "variations": 1,
                                 "checked": END.isoformat(), "as_of": END.isoformat()}}
    got = codes(rec, cfg, {"parent": "P1", "ratings_rate": 14, "ratings_cv": 30})
    assert got["score"] == 3 and "变体合并" not in got["tags"]


def test_data_date_and_business_timezone_do_not_drop_the_latest_real_rating(cfg):
    rec = record()
    rec["obs"][-1].update(ratings=28, rating=4.0)
    item = analyze.evaluate("TZ", rec, None, {}, cfg, END - timedelta(days=1))
    assert item["rating"] == 4.0 and item["label"] == "low"
    business_date = END - timedelta(days=1)
    rec["checks"] = {"review": review_check(day=business_date, as_of=END.isoformat())}
    assert evidence.usable_check(rec, "review", business_date) is not None
    rec["checks"]["review"]["checked"] = END.isoformat()
    assert evidence.usable_check(rec, "review", business_date) is None  # 尚未发生的核查仍不能使用。


def test_history_keeps_checks_known_on_judgment_day_when_daily_data_lags(cfg):
    rec = record()
    rec["obs"] = rec["obs"][-1:]
    judged_on = END + timedelta(days=2)
    rec["checks"] = {"review": review_check(day=judged_on, as_of=END.isoformat())}
    assert not analyze.truncated_rec(rec, END)["checks"]
    assert analyze.truncated_rec(rec, END, judged_on=judged_on)["checks"]
    current = analyze.evaluate("A", rec, None, {}, cfg, judged_on)
    replay = analyze.retro_labels({"asins": {"A": rec}}, None, ["A"], cfg, judged_on, END)
    assert current["label"] == "fake" and replay == {"A": "fake"}


def test_legacy_cache_does_not_turn_unproven_boolean_fields_into_evidence(cfg):
    check = {"n": 20, "top3": 3, "span": 60, "total": 100, "checked": END.isoformat(),
             "unverified_share": 1, "vine_share": 1}
    row = {"ratings": 500, "ratings_rate": 14, "ratings_cv": 30}
    old = {"checks": {"review": check}}
    got = fakehot.score(old, None, None, row, 2, cfg["thresholds"]["fake"], today=END)
    assert got["score"] == 2 and got["level"] is None
    assert old["checks"]["review"]["vine_share"] == 1  # 原始证据保留，内存筛选不改历史数据。
    old["checks"]["review"].update(top3=15, span=20)
    got = fakehot.score(old, None, None, row, 2, cfg["thresholds"]["fake"], today=END)
    assert [s["code"] for s in got["signals"]] == ["review_rate", "review_burst"]

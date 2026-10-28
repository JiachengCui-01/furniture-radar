"""全市场名单的初筛（radar/universe.py）：用自己存的往期名单算增长，只给可能达标的商品拉日数据。"""
import statistics
from datetime import date, timedelta

from radar import universe

TODAY = date(2026, 12, 1)


def _state(snaps: dict[int, dict], baseline: dict | None = None) -> dict:
    """snaps：{几天前: {asin: 近 30 天销量}}。"""
    return {"universe": {"snaps": [{"date": (TODAY - timedelta(days=age)).isoformat(), "units": u} for age, u in snaps.items()],
                         **({"baseline": baseline} if baseline else {})}}


def _row(units, available="2024-01-01", bsr_cr=0):
    return {"units": units, "available": available, "bsr_cr": bsr_cr}


def test_steady_screen_uses_own_snapshot_from_about_28_days_ago(cfg):
    """每周一期、稳定以后：比 28 天前自己存的那份高 ≥15%、半年内上架且没跌、近 7 / 14 天比之前高 1.3 倍，满足一个就拉；
    新进名单的都拉。近 7 天排名改善只在基准是上个月月度名单（头三周）时用。"""
    snaps = {28: {"UP": 1000, "FLAT": 1000, "YOUNG": 1000, "JUMP": 1000, "BSR": 1000},
             7: {"UP": 1100, "FLAT": 1000, "YOUNG": 1000, "JUMP": 1000, "BSR": 1000},
             14: {"UP": 1100, "FLAT": 1000, "YOUNG": 1000, "JUMP": 1000, "BSR": 1000}}
    rows = {"UP": _row(1200), "FLAT": _row(1050), "YOUNG": _row(1000, "2026-08-01"),
            "JUMP": _row(1100), "BSR": _row(1000, bsr_cr=60), "NEW": _row(400)}
    # JUMP：28 天只 +10%，但近 7 天：(1100 − 1000 + 1000 / 30 × 7) / 7 ÷ (1000 / 30) ≈ 1.43 倍
    picked = {a: why for a, _s, why in universe.screen(_state(snaps), rows, TODAY, cfg)}
    assert set(picked) == {"UP", "YOUNG", "JUMP", "NEW"}
    assert picked["NEW"].startswith("新进名单") and "比 28 天前 +20%" in picked["UP"]
    f = universe.features(_state(snaps), "JUMP", rows["JUMP"], TODAY)
    assert f["base"] == "own" and f["age"] == 28 and round(f["short"][7], 2) == 1.43
    # 快照隔的天数和 7 不一样（例如隔了 8 天）：按实际的 8 天算
    f8 = universe.features(_state({28: {"J": 1000}, 8: {"J": 1000}}), "J", _row(1100), TODAY)
    assert round(f8["short"][7], 3) == round(((1100 - 1000 + 1000 / 30 * 8) / 8) / (1000 / 30), 3)


def test_first_month_uses_last_months_list_and_looser_thresholds(cfg):
    """头一个月：没有 30 天前的快照，用结束于 21~45 天前的那个月的月度名单（按天数折成 30 天）当基准，门槛按基准的新旧放宽，
    再加上近 7 天排名改善；结束不到 21 天的月份比不出 30 天的增长，不用。"""
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 10, 26)) == "202609"
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 11, 5)) == "202609"   # 10 月才结束 5 天
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 11, 16)) == "202609"  # 每周一期第三周：9 月名单 47 天
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 11, 25)) == "202610"
    assert universe.baseline_period(_state({24: {"A": 1}}), TODAY) is None                        # 有 21 天以上的快照了
    base = {"period": "202610", "end": "2026-10-31", "days": 31, "units": {"A": 310, "B": 310, "C": 310}}
    state = _state({3: {"A": 300, "B": 300, "C": 300}}, base)  # 只有 3 天前的快照：基准是 10 月（结束 31 天）
    rows = {"A": _row(330), "B": _row(240), "C": _row(240, bsr_cr=25)}
    f = universe.features(state, "A", rows["A"], TODAY)
    assert f["base"] == "month" and f["age"] == 31 and round(f["g"], 2) == 0.1  # 310 件 / 31 天 × 30 = 300
    picked = {a for a, _s, _w in universe.screen(state, rows, TODAY, cfg)}
    assert picked == {"C"}  # A 只 +10%（基准 31 天前要 15%）；B 跌了；C 近 7 天排名改善 25%


def test_snapshots_match_by_parent_when_the_representative_child_changes(cfg):
    """名单按父体一行，但代表子体常常换（实测相邻两个月 29%~39%）：快照和基准按父体记、按父体对，
    换了代表子体的照样有基准，不会被当成“新进名单”抢先拉日数据；没有父体的单品按 ASIN 对。"""
    state = {"universe": {"snaps": []}}
    old = {"A1": {"parent": "P", "units": 1000, "variations": 4}, "S": {"units": 500}}
    universe.record(state, old, TODAY - timedelta(days=28), pages=1)
    assert state["universe"]["snaps"][0]["units"] == {"P": 1000, "S": 500}
    rows = {"A2": {**_row(1050), "parent": "P", "variations": 6}, "S": _row(800)}
    f = universe.features(state, "A2", rows["A2"], TODAY)
    assert f["base"] == "own" and f["age"] == 28 and round(f["g"], 2) == 0.05
    picked = {a: why for a, _s, why in universe.screen(state, rows, TODAY, cfg)}
    assert set(picked) == {"S"} and not picked["S"].startswith("新进名单")  # A2 只 +5%，不拉
    assert universe.variations_then(state, "A2", TODAY, row=rows["A2"]) == (28, 4.0)
    base_state = {"universe": {"snaps": []}}
    universe.record_baseline(base_state, "202610", {"A1": {"parent": "P", "units": 930}})
    f = universe.features(base_state, "A2", rows["A2"], date(2026, 11, 30))
    assert f["base"] == "month" and round(f["g"], 2) == round(1050 / (930 / 31 * 30) - 1, 2)


def test_node_tide_is_the_median_change_of_the_node(cfg):
    """品类大盘：子类目里量够的商品近 30 天比约 30 天前的中位数；扣大促系数；基准是上个月的月度名单（已修订完）时，
    近 30 天里最后几天还会被下调，按修订幅度扣；商品太少的子类目不算；本期没翻名单时不算。"""
    snaps = {28: {f"A{i}": 100 for i in range(12)} | {f"B{i}": 100 for i in range(3)}}
    rows = {f"A{i}": {**_row(100 + 10 * i), "node": "N:A"} for i in range(12)}
    rows.update({f"B{i}": {**_row(300), "node": "N:B"} for i in range(3)})
    state = _state(snaps)
    state["universe"].update(date=TODAY.isoformat(), rows=rows)
    tides = universe.node_tide(state, TODAY, cfg)
    assert tides == {"N:A": 1.55}  # 1.00~2.10 的中位数；N:B 只有 3 个商品
    assert universe.node_tide(state, TODAY, cfg, deal={"*": 1.0, "N:A": 1.24}) == {"N:A": 1.25}
    month = {"period": "202610", "end": "2026-10-31", "days": 30, "units": {f"A{i}": 100 for i in range(12)}}
    state2 = _state({}, month)
    state2["universe"].update(date=TODAY.isoformat(), rows=rows)
    revised = universe.node_tide(state2, TODAY, cfg, revision={"window": 9, "median": 0.8})
    shrink = 1 - 9 / 30 * 0.2  # 基准 10 月结束于 31 天前：每个商品的倍数换算到 28 天
    assert revised == {"N:A": round(statistics.median([((1 + i / 10) * shrink) ** (28 / 31) for i in range(12)]), 3)}
    state["universe"]["date"] = "2026-11-28"
    assert universe.node_tide(state, TODAY, cfg) == {}


def test_node_tide_is_converted_to_a_28_day_span(cfg):
    """上升中 / 潜力比的是近 28 天和再之前 28 天；大盘的基准是 46 天前时（头一个月的第三周用 9 月名单），
    同样的涨幅按 ^(28/46) 换算：×1.57 → ×1.32。span_days = 0 时不换算。"""
    rows = {f"A{i}": {**_row(157), "node": "N:A"} for i in range(10)}
    state = _state({46: {f"A{i}": 100 for i in range(10)}})
    state["universe"].update(date=TODAY.isoformat(), rows=rows)
    assert universe.node_tide(state, TODAY, cfg) == {"N:A": round(1.57 ** (28 / 46), 3)}
    assert universe.node_tide(state, TODAY, cfg) == {"N:A": 1.316}
    cfg["thresholds"]["tide"]["span_days"] = 0
    assert universe.node_tide(state, TODAY, cfg) == {"N:A": 1.57}


def test_young_list_finds_fresh_listings_below_the_floor(cfg):
    """早期名单：上架半年内、量还不够全市场门槛的商品（已在全市场名单里的、标题不在范围内的、同一父体的不要），
    近 7 天排名改善够多的才拉日数据。"""
    import json
    from radar.vendor import Budget, Vendor

    def row(asin, units, bsr_cr, title="Wood Nightstand", parent=None, revenue=None):
        return {"asin": asin, "parent": parent or asin, "title": title, "units": units, "revenue": revenue or units * 60,
                "bsrCr": bsr_cr, "availableDate": 1785542400000}

    seen = []

    def transport(tool, args):
        req = args["request"]
        seen.append(req)
        if req.get("availableMonth") != 6 or req.get("page", 1) > 1:
            return json.dumps({"code": "OK", "data": {"items": []}})
        if req["order"]["field"] == "total_units":
            items = [row("Y1", 250, 70), row("Y2", 120, 10), row("IN", 280, 90), row("SOFA", 150, 80, "Velvet Bench"),
                     row("Y1B", 200, 75, parent="Y1")]
        else:
            items = [row("R1", 60, 55, revenue=20000)]
        return json.dumps({"code": "OK", "data": {"items": items}})

    cfg["pool"]["young"] = {**cfg["pool"]["young"], "enabled": True}
    young = universe.fetch_young(Vendor(transport, Budget(20), live=False), cfg, [{"path": "N", "name": "Nightstands"}], {"IN"})
    assert set(young) == {"Y1", "Y2", "R1"}
    assert seen[0]["minUnits"] == 100 and seen[0]["maxUnits"] == 299 and seen[0]["availableMonth"] == 6
    assert [a for a, _s, _w in universe.screen_young(young, cfg)] == ["Y1", "R1"]  # 近 7 天排名改善 ≥50%

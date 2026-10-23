"""全市场名单的初筛（radar/universe.py）：用自己存的往期名单算增长，只给可能达标的商品拉日数据。"""
from datetime import date, timedelta

from radar import universe

TODAY = date(2026, 12, 1)


def _state(snaps: dict[int, dict], baseline: dict | None = None) -> dict:
    """snaps：{几天前: {asin: 近 30 天销量}}。"""
    return {"universe": {"snaps": [{"date": (TODAY - timedelta(days=age)).isoformat(), "units": u} for age, u in snaps.items()],
                         **({"baseline": baseline} if baseline else {})}}


def _row(units, available="2024-01-01", bsr_cr=0):
    return {"units": units, "available": available, "bsr_cr": bsr_cr}


def test_steady_screen_uses_own_snapshot_from_about_30_days_ago(cfg):
    """稳定以后：比 30 天前自己存的那份高 ≥15%、半年内上架且没跌、近 6 / 12 天比之前高 1.3 倍，满足一个就拉；新进名单的都拉。
    近 7 天排名改善只在头一个月用。"""
    snaps = {30: {"UP": 1000, "FLAT": 1000, "YOUNG": 1000, "JUMP": 1000, "BSR": 1000},
             6: {"UP": 1100, "FLAT": 1000, "YOUNG": 1000, "JUMP": 1000, "BSR": 1000},
             12: {"UP": 1100, "FLAT": 1000, "YOUNG": 1000, "JUMP": 1000, "BSR": 1000}}
    rows = {"UP": _row(1200), "FLAT": _row(1050), "YOUNG": _row(1000, "2026-08-01"),
            "JUMP": _row(1300), "BSR": _row(1000, bsr_cr=60), "NEW": _row(400)}
    rows["JUMP"]["units"] = 1100  # 30 天只 +10%，但近 6 天：(1100 − 1000 + 200) / 6 ÷ (1000 / 30) = 1.5 倍
    picked = {a: why for a, _s, why in universe.screen(_state(snaps), rows, TODAY, cfg)}
    assert set(picked) == {"UP", "YOUNG", "JUMP", "NEW"}
    assert picked["NEW"].startswith("新进名单") and "比 30 天前 +20%" in picked["UP"]
    f = universe.features(_state(snaps), "JUMP", rows["JUMP"], TODAY)
    assert f["base"] == "own" and f["age"] == 30 and round(f["r6"], 2) == 1.5


def test_first_month_uses_last_months_list_and_looser_thresholds(cfg):
    """头一个月：没有 30 天前的快照，用结束于 21~45 天前的那个月的月度名单（按天数折成 30 天）当基准，门槛按基准的新旧放宽，
    再加上近 7 天排名改善；结束不到 21 天的月份比不出 30 天的增长，不用。"""
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 10, 26)) == "202609"
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 11, 5)) == "202609"   # 10 月才结束 5 天
    assert universe.baseline_period({"universe": {"snaps": []}}, date(2026, 11, 25)) == "202610"
    assert universe.baseline_period(_state({24: {"A": 1}}), TODAY) is None                        # 有 21 天以上的快照了
    base = {"period": "202610", "end": "2026-10-31", "days": 31, "units": {"A": 310, "B": 310, "C": 310}}
    state = _state({3: {"A": 300, "B": 300, "C": 300}}, base)  # 只有 3 天前的快照：基准是 10 月（结束 31 天）
    rows = {"A": _row(330), "B": _row(240), "C": _row(240, bsr_cr=25)}
    f = universe.features(state, "A", rows["A"], TODAY)
    assert f["base"] == "month" and f["age"] == 31 and round(f["g"], 2) == 0.1  # 310 件 / 31 天 × 30 = 300
    picked = {a for a, _s, _w in universe.screen(state, rows, TODAY, cfg)}
    assert picked == {"C"}  # A 只 +10%（基准 31 天前要 15%）；B 跌了；C 近 7 天排名改善 25%

from datetime import date

from radar import discovery, tracking
from radar.series import Daily, build_daily
from radar.vendor import Budget, Vendor
from tests.conftest import fixture, fixture_text


def _node(path, label, products=5000, units=1000, root="1064954:1069102"):
    return {"path": path, "label": label, "name": label.split(":")[-1], "name_cn": "", "root": root,
            "root_label": "", "products": products, "units": units, "revenue": 0, "avg_price": None}


def test_scope_keeps_panel_wood_metal_case_goods_only(cfg):
    """只留柜类、桌类、床架、置物架；沙发、床垫、椅子、推车等剔除。只看子类目自己的名字。"""
    raw = [
        _node("1", "Office Products:Office Furniture & Lighting:Desks & Workstations:Computer Workstations"),
        _node("2", "Office Products:Office Furniture & Lighting:Chairs & Sofas"),
        _node("3", "Office Products:Office Furniture & Lighting:Carts & Stands:Utility Carts"),
        _node("4", "H:Bedroom Furniture:Beds, Frames & Bases:Bed Frames", root="H"),
        _node("5", "H:Bedroom Furniture:Mattresses & Box Springs:Mattresses", root="H"),
        _node("6", "H:Living Room Furniture:Tables:Sofa & Console Tables", root="H"),
        _node("7", "H:Living Room Furniture:Sofas & Couches", root="H"),
        _node("8", "H:Kitchen Furniture:Storage Islands & Carts:Kitchen Islands", root="H"),
        _node("9", "H:Bedroom Furniture:Dressers", root="H", products=10),
    ]
    picked = {n["path"] for n in discovery.select_nodes(raw, cfg)}
    assert picked == {"1", "4", "6", "8"}


def test_overlapping_nodes_keep_the_deeper_one(cfg):
    raw = [
        _node("H:1", "H:Living Room Furniture:Tables", units=9000, root="H"),
        _node("H:1:2", "H:Living Room Furniture:Tables:Coffee Tables", units=4000, root="H"),
        _node("H:3", "H:Bedroom Furniture:Dressers", units=7000, root="H"),
    ]
    picked = [n["path"] for n in discovery.select_nodes(raw, cfg)]
    assert picked == ["H:3", "H:1:2"]


def test_product_filter_drops_upholstered_and_plastic(cfg):
    keep = [discovery.keep_product({"title": t}, cfg) for t in (
        "Upholstered Platform Bed Frame", "Dresser with 6 Fabric Drawers", "Plastic Storage Cabinet",
        "Console Table Behind Sofa", "Industrial Metal and Wood Bookcase")]
    assert keep == [False, False, False, True, True]


def test_real_product_research_row_normalizes():
    row = fixture("product_research")["data"]["items"][0]
    node = _node("1055398:1063306:3733781:3733831", "x:y:Buffets & Sideboards")
    item = discovery.normalize_product(row, node, "top", 1)
    assert item["asin"] == "B0TEST0001" and item["parent"] == "B0TEST0002"
    assert item["ratings_rate"] == 3.38 and item["ratings_cv"] == 36 and item["units"] == 1064
    assert item["available"] == "2025-04-23"


def test_discovery_against_real_fixtures(cfg):
    def transport(tool, args):
        return fixture_text(tool)

    cfg["scope"]["roots"] = [{"path": "1055398:1063306", "label": "家具"}]
    disc = discovery.discover(Vendor(transport, Budget(50), live=False), cfg, "202609")
    assert disc["complete"] and [n["name_cn"] for n in disc["nodes"]] == ["餐边柜、餐具柜"]
    assert "B0TEST0001" in disc["products"]


def test_asin_prediction_fixture_is_tracked(cfg):
    def transport(tool, args):
        assert tool == "asin_prediction" and args == {"marketplace": "US", "asin": "B0TEST0001"}
        return fixture_text(tool)

    state = {"asins": {}, "runs": []}
    vendor = Vendor(transport, Budget(5), live=False)
    assert tracking.fetch_one(vendor, "B0TEST0001", state, cfg, 1, None)
    rec = state["asins"]["B0TEST0001"]
    assert rec["series"]["start"] == "2025-08-01" and rec["series"]["sales"] == [17, 17, 14]
    assert rec["obs"][-1] == {"date": "2025-08-03", "run": 1, "ratings": 366, "rating": 4.3}
    assert rec["months"][0][:2] == ["2025-08", 632]


def test_build_daily_fills_gaps():
    series = build_daily([{"date": "2026-01-01", "bsr": 10, "sales": 5, "price": 9},
                          {"date": "2026-01-04", "bsr": 12, "sales": 4, "price": 9}])
    d = Daily.from_record(series)
    assert d.sales == [5, None, None, 4] and d.as_of() == date(2026, 1, 4)


def _opportunities(n=30):
    return {f"A{i}": {"asin": f"A{i}", "parent": f"P{i}", "node": f"N{i % 2}", "units": 1000 - i,
                      "units_gr": 100 - i, "bsr_cr": 30 - i, "available": "2025-01-01", "sources": ["top"]}
            for i in range(n)}


def test_pool_dedupes_parents(cfg):
    products = {}
    for i in range(30):
        products[f"A{i}"] = {"asin": f"A{i}", "parent": f"P{i // 3}", "node": f"N{i % 2}", "units": 1000 - i,
                             "units_gr": 50, "bsr_cr": i, "available": "2025-01-01", "sources": ["top"]}
    cfg["pool"].update(per_node_top=2, max_size=50)
    pool, _ = tracking.select_pool({"products": products}, {"asins": {}, "runs": []}, cfg, date(2026, 10, 1))
    parents = [products[a]["parent"] for a in pool]
    assert len(parents) == len(set(parents)) == 10  # 同一父体只出现一次


def test_pool_rotates_opportunities(cfg):
    """机会候选比名额多：机会分最高的每期都看，其余按“最久没查”轮流。"""
    products = _opportunities()
    cfg["pool"].update(per_node_top=0, core_max=0, similar_max=0, opportunity_every_run=2, max_size=8)
    state = {"asins": {}, "runs": []}
    pool, reasons = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1))
    assert [a for a in pool if reasons[a] == "opportunity"] == ["A0", "A1"]
    first = [a for a in pool if reasons[a] == "rotate"]
    assert first == ["A2", "A3", "A4", "A5", "A6", "A7"]
    for a in pool:
        state["asins"][a] = {"last_pool_run": 1}
    pool2, reasons2 = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 4))
    assert [a for a in pool2 if reasons2[a] == "opportunity"] == ["A0", "A1"]
    assert [a for a in pool2 if reasons2[a] == "rotate"] == ["A8", "A9", "A10", "A11", "A12", "A13"]


def test_pool_order_core_similar_then_opportunities(cfg):
    """必看先占名额，再给相似款，剩下的给机会候选；池子不超过本期预算。"""
    products = _opportunities(10)
    state = {"asins": {"W1": {"node": "N0", "title": "Desk"}},
             "runs": [{"labels": {"W1": ["surge", 0]}}],
             "similar": {"items": [{"asin": "S1", "parent": None, "node": "N1", "units": 900, "units_gr": 5,
                                    "rank": 1, "run": 1, "title": "Desk", "sources": ["similar"]}]}}
    cfg["pool"].update(per_node_top=1, core_max=3, similar_max=1, opportunity_every_run=3, max_size=20)
    pool, reasons = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1), limit=6)
    assert pool[:4] == ["W1", "A0", "A1", "S1"]
    assert [reasons[a] for a in pool[:4]] == ["watch", "top", "top", "similar"]
    # 机会分前 3 是 A0、A1、A2：前两个已在必看里，不重复占名额
    assert pool[4:] == ["A2", "A3"] and reasons["A2"] == "opportunity" and reasons["A3"] == "rotate"


def test_find_risers_uses_recent_30_days_and_find_similar_parses(cfg):
    calls = []

    def transport(tool, args):
        calls.append((tool, args))
        if tool == "asin_competitor":
            return fixture_text("asin_competitor")
        return fixture_text("product_research")

    nodes = [_node("1055398:1063306:1063308:3733251", "H:Bedroom Furniture:Nightstands", root="1055398:1063306"),
             _node("1055398:1063306:3733781:3733831", "x:y:Buffets & Sideboards", root="1055398:1063306")]
    vendor = Vendor(transport, Budget(20), live=False)
    discovery.find_risers(vendor, cfg, nodes)
    research = [a["request"] for t, a in calls if t == "product_research"]
    assert research and all("month" not in r for r in research)  # 不传月份 = 截至当天的近 30 天
    assert {r["order"]["field"] for r in research} == {"total_units_growth", "bsr_rank_cr", "available_date"}

    found = discovery.find_similar(vendor, cfg, ["B0TEST0100"], nodes)
    asins = [p["asin"] for p in found]
    assert asins == ["B0TEST0101", "B0TEST0102"]  # 去掉种子自己、软包、断货恢复
    assert found[1]["units_gr"] == 41.2 and found[1]["sources"] == ["similar"] and found[1]["seed"] == "B0TEST0100"


def test_pool_skips_low_rated_and_backfills(cfg):
    """评分低于 4.0 的不占追踪名额，由同子类目排在后面的依次补位；评分优先看最新日数据。"""
    products = {f"A{i}": {"asin": f"A{i}", "node": "N", "units": 1000 - i * 100, "rating": r,
                          "available": "2025-01-01", "sources": ["top"]}
                for i, r in enumerate([3.5, 4.4, 4.2, 4.8, 3.6])}
    cfg["pool"].update(per_node_top=2, core_max=2, opportunity_every_run=0)
    state = {"asins": {"A1": {"obs": [{"rating": 3.8}]},     # 月度快照 4.4，最新 3.8
                       "A4": {"obs": [{"rating": 4.6}]}},    # 月度快照 3.6，最新 4.6
             "runs": [], "run_seq": 0}
    pool, _ = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1))
    assert pool == ["A2", "A3"]
    cfg["pool"]["core_max"] = 3  # 每个子类目取完后，按销量从全部类目里补
    pool, _ = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1))
    assert pool == ["A2", "A3", "A4"]

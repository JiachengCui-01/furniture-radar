from datetime import date

from radar import discovery, tracking
from radar.series import Daily, build_daily
from radar.vendor import Budget, Vendor
from tests.conftest import fixture, fixture_text


def _node(path, label, products=5000, units=1000, root="1064954:1069102"):
    return {"path": path, "label": label, "name": label.split(":")[-1], "name_cn": "", "root": root,
            "root_label": "", "products": products, "units": units, "revenue": 0, "avg_price": None}


def test_exclusion_only_looks_below_the_root(cfg):
    """办公根类目名里带 Lighting、庭院根类目名里带 Accessories，不能因此把整个根排除掉。"""
    raw = [
        _node("1064954:1069102:1", "Office Products:Office Furniture & Lighting:Chairs & Sofas"),
        _node("1064954:1069102:2", "Office Products:Office Furniture & Lighting:Office Lighting"),
        _node("2972638011:553824:3", "Patio, Lawn & Garden:Patio Furniture & Accessories:Umbrellas & Shade",
              root="2972638011:553824"),
        _node("2972638011:553824:4", "Patio, Lawn & Garden:Patio Furniture & Accessories:Patio Furniture Sets",
              root="2972638011:553824"),
        _node("1064954:1069102:9", "Office Products:Office Furniture & Lighting:Tiny", products=10),
    ]
    picked = {n["path"] for n in discovery.select_nodes(raw, cfg)}
    assert picked == {"1064954:1069102:1", "2972638011:553824:4"}


def test_overlapping_nodes_keep_the_deeper_one(cfg):
    raw = [
        _node("1055398:1063306:1063318", "Home & Kitchen:Furniture:Living Room Furniture", units=9000,
              root="1055398:1063306"),
        _node("1055398:1063306:1063318:3733551", "Home & Kitchen:Furniture:Living Room Furniture:Sofas & Couches",
              units=4000, root="1055398:1063306"),
        _node("1055398:1063306:1063308", "Home & Kitchen:Furniture:Bedroom Furniture", units=7000,
              root="1055398:1063306"),
    ]
    picked = [n["path"] for n in discovery.select_nodes(raw, cfg)]
    assert picked == ["1055398:1063306:1063308", "1055398:1063306:1063318:3733551"]


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


def test_pool_dedupes_parents_and_rotates_exploration(cfg):
    products = {}
    for i in range(30):
        products[f"A{i}"] = {"asin": f"A{i}", "parent": f"P{i // 3}", "node": f"N{i % 2}", "units": 100 - i,
                             "bsr_cr": i, "available": "2025-01-01", "sources": ["top"]}
    cfg["pool"].update(per_node_top=2, momentum_top=2, explore_per_run=3, max_size=50)
    state = {"asins": {}, "runs": [], "run_seq": 0}
    pool, reasons = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1))
    parents = [products[a]["parent"] for a in pool]
    assert len(parents) == len(set(parents))  # 同一父体只出现一次
    explored = [a for a in pool if reasons[a] == "explore"]
    assert len(explored) == 3
    for a in explored:
        state["asins"][a] = {"explored_run": 1}
    state["run_seq"] = 1
    pool2, reasons2 = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 4))
    assert not set(explored) & {a for a in pool2 if reasons2[a] == "explore"}

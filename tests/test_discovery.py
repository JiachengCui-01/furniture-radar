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
    cfg["pool"].update(per_node_top=1, core_max=2, similar_max=1, opportunity_every_run=3, max_size=20)
    pool, reasons = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1), limit=6)
    assert pool[:4] == ["W1", "A0", "A1", "S1"]
    assert [reasons[a] for a in pool[:4]] == ["watch", "top", "top", "similar"]
    # 机会分前 3 是 A0、A1、A2：前两个已在必看里，不重复占名额
    assert pool[4:] == ["A2", "A3"] and reasons["A2"] == "opportunity" and reasons["A3"] == "rotate"


def test_pool_watchlist_has_its_own_quota(cfg):
    """往期上榜的单独给名额（watch_max），多了也不挤掉子类目头部（core_max）。
    以前两者共用 core_max：往期上榜的一多，头部一个都排不进来。"""
    products = _opportunities(10)
    flagged = {f"W{i}": {"node": "N0", "title": "Desk"} for i in range(4)}
    state = {"asins": dict(flagged), "runs": [{"labels": {a: ["surge", 0] for a in flagged}}]}
    cfg["pool"].update(per_node_top=1, per_node_top_revenue=0, core_max=2, watch_max=3, similar_max=0,
                       opportunity_every_run=0, max_size=20)
    pool, reasons = tracking.select_pool({"products": products}, state, cfg, date(2026, 10, 1), limit=8)
    assert [reasons[a] for a in pool[:5]] == ["watch"] * 3 + ["top"] * 2  # 往期上榜最多 3 个，头部照样 2 个
    assert pool[3:5] == ["A0", "A1"]


def test_find_risers_uses_recent_30_days_and_find_similar_parses(cfg):
    cfg["discovery"].update(risers_per_root=50, risers_new_per_root=50)  # 默认关闭（全市场名单已经包括），这里测它本身
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
    # 上升榜的量：销量 ≥300 件或销售额 ≥4 万美元，接口条件是“且”，所以每个排序两种门槛各查一次
    for order in ("total_units_growth", "bsr_rank_cr"):
        floors = [{k: r[k] for k in ("minUnits", "minRevenue") if k in r} for r in research if r["order"]["field"] == order]
        assert {"minUnits": 300} in floors and {"minRevenue": 40000} in floors

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


def test_candidates_need_units_or_revenue(cfg):
    """机会候选的量：月销量 ≥300 件或月销售额 ≥4 万美元，满足一个就行（只看件数时，卖得贵的几乎都进不来）。"""
    rows = {"CHEAP": (350, 10_000), "PRICEY": (150, 80_000), "SMALL": (100, 10_000)}
    products = {a: {"asin": a, "parent": a, "node": "N", "units": u, "revenue": r, "units_gr": 50,
                    "available": "2025-01-01", "sources": ["top"]} for a, (u, r) in rows.items()}
    cands = tracking.opportunity_candidates({"products": products}, {"asins": {}, "runs": []}, cfg, date(2026, 10, 1))
    assert {p["asin"] for p in cands} == {"CHEAP", "PRICEY"}


def test_node_heads_by_units_and_by_revenue(cfg):
    """每个子类目必看：销量第一和销售额第一（不是同一个时两个都看）。"""
    rows = {"VOLUME": (900, 18_000), "VALUE": (150, 90_000), "OTHER": (400, 30_000)}
    products = {a: {"asin": a, "parent": a, "node": "N", "units": u, "revenue": r, "available": "2025-01-01",
                    "sources": ["top"]} for a, (u, r) in rows.items()}
    cfg["pool"].update(per_node_top=1, per_node_top_revenue=1, core_max=2, similar_max=0, opportunity_every_run=0)
    pool, reasons = tracking.select_pool({"products": products}, {"asins": {}, "runs": []}, cfg, date(2026, 10, 1))
    assert pool == ["VOLUME", "VALUE"] and set(reasons.values()) == {"top"}


def test_refresh_keeps_package_data_from_the_30_day_row(cfg):
    """月度名单是月初查的，加包装字段之前的那份没有包装数据；近 30 天榜单每期都查、带包装数据。
    刷新日数据时两行合并：月度行优先，月度行没有的包装数据从近 30 天行补上。"""
    from radar.demo import SyntheticWorld

    world = SyntheticWorld(date(2026, 10, 5))
    p = world.products[0]
    fresh = discovery.normalize_product(world._recent_row(p), {"path": p.node, "name": p.cn}, "riser", 1)
    monthly = {k: v for k, v in fresh.items() if k != "pkg"}
    state = {"asins": {}, "risers": {"items": [fresh]}}
    vendor = Vendor(world, Budget(5), live=False)
    assert tracking.refresh(vendor, [p.asin], state, {"products": {p.asin: monthly}}, cfg, run_seq=1) == 1
    assert state["asins"][p.asin]["pkg"] == fresh["pkg"] and fresh["pkg"]["fba"] > 0


def test_candidates_use_own_monthly_growth_and_rising_heads(cfg):
    """月度名单里的“增长快”用自己存的往月名单算（卖家精灵的 unitsGr 字段两成是 0 或空的）；
    近 30 天名单的日均 ≥ 近 6 个月名单日均 1.3 倍的头部商品也进候选（没有日数据就判不了上升中）。"""
    keys = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]
    sep = {"OWN_UP": 1300, "FIELD_ONLY": 1050, "RISING": 1000, "FLAT": 1000}
    months = {k: {"N": {a: [1000 if k != "2026-09" else u, 0, 50, None] for a, u in sep.items()}} for k in keys}
    rolling = {"RISING": {"asin": "RISING", "units": 1500, "parent": None}, "FLAT": {"asin": "FLAT", "units": 1000, "parent": None}}
    state = {"asins": {}, "runs": [], "monthly_lists": {"months": months, "rolling": {"rows": rolling}}}
    gr = {"OWN_UP": 0, "FIELD_ONLY": 50, "RISING": None, "FLAT": 0}
    products = {a: {"asin": a, "parent": None, "node": "N", "units": u, "revenue": u * 50, "units_gr": gr[a],
                    "available": "2024-01-01", "sources": ["top"]} for a, u in sep.items()}
    disc = {"period": "202609", "nodes": [{"path": "N"}], "products": products}
    cands = {p["asin"]: p for p in tracking.opportunity_candidates(disc, state, cfg, date(2026, 10, 20))}
    assert set(cands) == {"OWN_UP", "RISING"}
    assert cands["OWN_UP"]["units_gr"] == 30.0  # 字段是 0，自己算 1300 ÷ 1000
    assert cands["RISING"]["head_rising"] >= 1.3
    del state["monthly_lists"]  # 没有往月名单时照旧用字段
    assert {p["asin"] for p in tracking.opportunity_candidates(disc, state, cfg, date(2026, 10, 20))} == {"FIELD_ONLY"}


def test_deal_factor_comes_from_tracked_items():
    """近 30 天名单含大促日：系数 = 追踪商品“含大促日的近 30 天日均 ÷ 去掉大促日的”的中位数；近 30 天没有大促日时为 1。"""
    from datetime import timedelta

    from radar import analyze

    end = date(2026, 10, 20)
    sales = [10] * 28 + [30, 30] + [10] * 10  # 最后 40 天，大促在倒数第 12、11 天
    start = end - timedelta(days=len(sales) - 1)
    events = {start + timedelta(days=28), start + timedelta(days=29)}
    dailies = [Daily(start, [1000] * len(sales), list(sales), [50.0] * len(sales)) for _ in range(12)]
    f = analyze.deal_factors({"N": dailies}, events, end)
    assert f["N"] == f["*"] == round((28 * 10 + 60) / 30 / 10, 3)
    assert analyze.deal_factors({"N": dailies}, events, end + timedelta(days=40)) == {"*": 1.0}


def test_revisions_of_recent_daily_data_are_measured():
    """卖家精灵会回头改最近几天的日数据：上期存的和这期新拉的逐天比，记下改了几天、改了多少（判定还没用它）。
    实测 2026-10-22：20 个商品隔 2 天重拉，最近 9 天中位数是原来的 85%，更早的没变。"""
    from datetime import timedelta

    end = date(2026, 10, 20)
    old_sales = [100] * 30
    start = end - timedelta(days=29)
    old = {"start": start.isoformat(), "bsr": [1000] * 30, "sales": old_sales, "price": [50.0] * 30}
    revised = [100] * 21 + [85] * 9 + [90, 95]  # 最近 9 天被下调 15%，又多了 2 天新数据
    new = {"start": start.isoformat(), "bsr": [1000] * 32, "sales": revised, "price": [50.0] * 32}
    pairs = tracking.revision_pairs(old, new)
    assert pairs[0] == [0, 0.85] and pairs[8] == [8, 0.85] and pairs[9] == [9, 1.0]
    state = {"asins": {"A": {"revision": {"run": 5, "pairs": pairs}}, "B": {"revision": {"run": 5, "pairs": pairs}},
                       "C": {"revision": {"run": 4, "pairs": pairs}}}}
    out = tracking.summarize_revisions(state, 5)
    assert out["items"] == 2 and out["window"] == 9 and out["median"] == 0.85 and out["changed"] == 1.0


def test_failed_queries_are_not_taken_as_empty_lists(cfg):
    """查询失败（被拒 / 连不上）不等于“这个子类目没有商品”：发现时不记“已查”、名单不算完整，下期接着查；
    全市场名单某一页失败时重试一次，还不行就记下这个子类目这期没翻全。"""
    import json
    from radar import universe

    calls = {"n": 0}

    def transport(tool, args):
        calls["n"] += 1
        if tool == "product_research" and calls["n"] == 2:
            return json.dumps({"code": "ERROR", "message": "服务繁忙"})
        return fixture_text(tool)

    cfg["scope"]["roots"] = [{"path": "1055398:1063306", "label": "家具"}]
    disc = discovery.discover(Vendor(transport, Budget(50), live=False), cfg, "202609")
    assert disc["complete"] is False and disc["nodes"] and disc["done_top"] == []
    again = discovery.discover(Vendor(lambda t, a: fixture_text(t), Budget(50), live=False), cfg, "202609", disc)
    assert again["complete"] and "B0TEST0001" in again["products"]

    def down(tool, args):
        return json.dumps({"code": "ERROR", "message": "服务繁忙"})

    failed = []
    rows = universe.fetch(Vendor(down, Budget(50), live=False), cfg, again["nodes"], failed=failed)
    assert rows == {} and failed == [again["nodes"][0]["path"]]


def test_upholstered_seating_and_accessories_are_out_of_scope(cfg):
    """软包的床 / 床头板 / 长凳（面料词 + 这几类商品词）和配件（升降台、显示器增高架、电视支架腿、储物柜隔板）不在范围内；
    表面贴皮的柜子、带显示器架的桌子照样在。这几条是读的时候过滤的，改了不改变监控范围的指纹（不触发重新发现）。"""
    out = ["Allewie Queen Size Faux Leather Bed Frame, Adjustable Headboard",
           "King Size Headboard Only, Velvet Wall Mount Headboard, Black",
           "Blue Velvet Dining Bench with Back, Tufted Banquette Seating",
           "VIVO Black 32 in Standing Desk Converter, DESK-V000K",
           "OPNICE Desk Organizer and Accessories, 2-Tier Computer Monitor Stand Riser with Drawer",
           "PERLESMITH Universal TV Stand Legs for 32-70 inch TVs to 99lbs with Mount",
           "Svartur Adjustable Locker Shelf Pink Height and Width Adjustable",
           # 11-01 彩排全市场名单里漏过的：布艺抽屉柜、带布艺文件抽屉的书桌、收纳凳、化妆凳、电视顶置物架、收纳箱、户外
           "Sorbus Wide Fabric Closet Chests, 5 Drawer Organizer Unit for Bedroom",
           "Computer Desk with Fabric File Drawers, Home Office Writing Desk",
           "SONGMICS Storage Ottoman Bench, Folding Ottoman with Lid, Footrest for Bedroom",
           "Vanity Stool with Storage, Makeup Chair for Bedroom",
           "TV Top Shelf for Streaming Devices, Screen Top Storage Shelf",
           "Stackable Storage Bins with Lids, Collapsible Cubes for Closet",
           "Outdoor Storage Cabinet, Waterproof Patio Shed for Garden"]
    keep = ["Grey Dresser with Power Outlets and LED Lights 13 Drawers Long Dresser for Bedroom Faux Leather",
            "7.8\" W Faux Leather Narrow Nightstand, Modern Fully-Assembled Bedside Table with 2 Drawers",
            "Lufeiya Black Gaming Desk with LED Lights and Power Outlets, Home Office Desks with Monitor Stand",
            "Small Desk with Drawers for Small Spaces, 44'' Wood Desk with File Drawer & Monitor Stand",
            "Homleke Shoe Storage Bench, 4 Doors & Thickened Padded Seat Cushion",
            "Modern Nightstand with Open Space Small Desk Organizer Shelf, Bamboo Desktop Organizer",
            "Makeup Vanity Desk with Mirror and Lights, Vanity Table with Charging Station",
            "Kitchen Island with Storage, Rolling Kitchen Cart Table for Indoor and Outdoor"]
    assert not any(discovery.keep_product({"title": t}, cfg) for t in out)
    assert all(discovery.keep_product({"title": t}, cfg) for t in keep)
    plain = {**cfg, "scope": {k: v for k, v in cfg["scope"].items() if k not in discovery.READ_TIME_FILTERS}}
    assert discovery.scope_key(plain) == discovery.scope_key(cfg)
    disc = {"products": {"A": {"asin": "A", "title": out[3]}, "B": {"asin": "B", "title": keep[0]}}}
    assert set(discovery.apply_product_filter(disc, cfg)["products"]) == {"B"}

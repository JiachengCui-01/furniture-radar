"""小件、不追踪的子类目（radar/discovery.py）和蓝海产品（radar/detect/blueocean.py）。"""
import copy
from datetime import date

from radar import analyze, discovery, tracking
from radar.detect import blueocean
from radar.notify import dingtalk
from radar.report import render

TODAY = date(2026, 11, 1)
NODE_A, NODE_B = "1:2:3", "1:2:4"


def row(asin, *, node=NODE_A, revenue=50_000, ratings=1000, available="2024-01-01", brand=None, rating=4.5,
        title="Wood Nightstand with Drawers", pkg=None, name="Nightstands"):
    out = {"asin": asin, "parent": None, "node": node, "node_name": name, "node_cn": "床头柜", "title": title,
           "brand": f"B{asin}" if brand is None else brand, "price": 150.0, "revenue": revenue, "units": 400,
           "ratings": ratings, "rating": rating, "available": available, "sources": ["universe"]}
    if pkg:
        out["pkg"] = pkg
    return out


def state_with(rows, day=TODAY):
    return {"universe": {"date": day.isoformat(), "rows": {r["asin"]: r for r in rows}, "snaps": []},
            "asins": {}, "runs": []}


def test_small_items_and_coat_racks_are_out_of_scope(cfg):
    """小件 = 包装在亚马逊标准尺寸以内（三边从长到短 ≤18×14×8 英寸、≤20 磅），哪个子类目都剔除；没有包装数据的不剔除。
    衣帽架整个子类目不追踪：商品、月度名单的子类目列表、新一个月的子类目选择都去掉。两条都是读的时候过滤，不改监控范围的指纹。"""
    small = row("S", pkg={"dims": [5.0, 17.0, 13.0], "weight": 9.0})  # 三边的顺序不影响
    assert discovery.small_item(small, cfg) and not discovery.keep_product(small, cfg)
    for pkg in ({"dims": [17, 13, 5], "weight": 20.5}, {"dims": [18.5, 13, 5], "weight": 9}, {"dims": [17, 13, 8.2], "weight": 9},
                {"dims": None, "weight": 9}, {"dims": [17, 13, 5], "weight": None}):
        assert not discovery.small_item(row("L", pkg=pkg), cfg), pkg
    assert discovery.keep_product(row("N"), cfg)  # 没有包装数据：判断不了，不剔除
    coat = row("C", name="Coat Racks", title="Solid Wood Coat Rack Freestanding with 8 Hooks")
    assert not discovery.keep_product(coat, cfg)

    disc = {"nodes": [{"path": NODE_A, "name": "Nightstands"}, {"path": NODE_B, "name": "Coat Racks"}],
            "products": {"N": row("N"), "C": {**coat, "node": NODE_B}}}
    discovery.apply_product_filter(disc, cfg)
    assert [n["path"] for n in disc["nodes"]] == [NODE_A] and list(disc["products"]) == ["N"]
    raw = [{"path": p, "label": f"Home:Furniture:{n}", "name": n, "root": "Home", "products": 5000, "units": 9000}
           for p, n in ((NODE_A, "Nightstands"), (NODE_B, "Coat Racks"))]
    cfg["scope"]["roots"] = [{"path": "Home"}]
    assert [n["name"] for n in discovery.select_nodes(raw, cfg)] == ["Nightstands"]

    other = copy.deepcopy(cfg)
    other["scope"]["small_item"] = {"max_dims": [20, 15, 9], "max_weight": 25}
    other["scope"]["exclude_node_regex"] = ""
    assert discovery.scope_key(other) == discovery.scope_key(cfg)  # 改了不用重新发现


def _market():
    """子类目 A：20 个老头部（评论 1000 条）+ 各种新链接；子类目 B 里有 OldCo 的老品。"""
    heads = [row(f"H{k:02d}", revenue=200_000 - k * 1000) for k in range(20)]
    new = {
        "OK": row("OK", revenue=90_000, ratings=150, available="2026-09-01", brand="NewCo"),
        "MANY": row("MANY", revenue=89_000, ratings=250, available="2026-09-01"),        # 评论 > 1000 × 20%
        "OLDBRAND": row("OLDBRAND", revenue=88_000, ratings=50, available="2026-09-01", brand="OldCo"),
        "OLD": row("OLD", revenue=87_000, ratings=50, available="2026-03-01"),           # 上架 245 天
        "NOBRAND": row("NOBRAND", revenue=86_000, ratings=50, available="2026-09-01", brand=""),
        "LOWSTAR": row("LOWSTAR", revenue=85_000, ratings=50, available="2026-09-01", rating=4.1),  # 评论 <100 要 ≥4.2
        "SMALL": row("SMALL", revenue=84_000, ratings=50, available="2026-09-01", pkg={"dims": [17, 13, 5], "weight": 9}),
    }
    filler = [row(f"F{k:02d}", revenue=60_000 - k * 100, ratings=500) for k in range(24)]
    late = row("LATE", revenue=40_000, ratings=50, available="2026-09-01")  # 排在第 51 名以后
    oldco = row("OC", node=NODE_B, brand="OldCo", available="2023-05-01", name="Dressers")
    return heads + list(new.values()) + filler + [late, oldco]


def test_blue_ocean_candidates_follow_each_condition(cfg):
    """①~④ 只用本期全市场名单：上架 ≤180 天、销售额排进子类目前 50（小件不参与排名）、评论 ≤ 头部 20 名中位数的 20%、
    品牌已知且在全市场名单里（别的子类目也算）没有上架超过 180 天的商品、评分达标。名单不是本期翻的不判。"""
    st = state_with(_market())
    cands = blueocean.candidates(st, TODAY, cfg)
    assert list(cands) == ["OK"]
    ev = cands["OK"]
    assert ev["rank"] == 21 and ev["head_reviews"] == 1000 and ev["limit"] == 200 and ev["age"] == 61
    assert ev["n"] == 20 + 6 + 24 + 1  # 小件不算进子类目
    assert blueocean.candidates(state_with(_market(), date(2026, 10, 20)), TODAY, cfg) == {}
    cfg["thresholds"]["blue_ocean"]["enabled"] = False
    assert blueocean.candidates(st, TODAY, cfg) == {}


def _item(asin, **kw):
    base = {"asin": asin, "parent": None, "label": "watch", "label_cn": "观察", "daily": True, "spark": [1.0] * 30,
            "stale": False, "relist": None, "fading": False, "tags": [], "age_days": 61, "rising": None,
            "title": "NewCo Fluted Nightstand", "brand": "NewCo", "node_cn": "床头柜", "node_name": "Nightstands",
            "price": 150.0, "rating": 4.6, "ratings": 150, "image": "", "fake": {"score": 0, "level": None, "signals": []},
            "monthly": [], "spark_start": "2026-10-03", "recent_avg28": 20.0, "material": "实木", "features": []}
    return {**base, **kw}


def test_blue_ocean_is_listed_only_after_the_daily_data_check(cfg):
    """⑤ 日数据核对：没回落、不是老品换链接、名单和日数据对得上、不陈旧、不是异常信号；没拉到日数据的不列。
    商品照样留在自己的板块里，另成一份名单，按近 30 天销售额排。"""
    ev = {"asin": "A", "node": NODE_A, "rank": 3, "n": 80, "revenue": 90_000.0, "ratings": 20.0, "head_reviews": 300.0,
          "limit": 60.0, "age": 61}
    cands = {a: {**ev, "asin": a, "revenue": ev["revenue"] - i} for i, a in enumerate("ABCDEFG")}
    items = [_item("A", label="potential"), _item("B", fading=True), _item("C", relist={"why": "x"}),
             _item("D", tags=["名单和日数据对不上（名单近 30 天 900 件，日数据合计 30 件）：数据可能有误"]),
             _item("E", stale=True), _item("F", daily=False, spark=[])]
    out = blueocean.mark(items, cands, cfg)
    assert [i["asin"] for i in items if i.get("ocean")] == ["A"] and items[0]["label"] == "potential"
    assert out == {"candidates": 7, "tracked": 5, "listed": 1,
                   "missed": {"最近回落": 1, "老品换链接": 1, "名单和日数据对不上": 1, "数据陈旧": 1, "没有日数据": 2}}
    sec = analyze.sections(items, 0)
    assert [i["asin"] for i in sec["ocean"]] == ["A"] and sec["counts"]["ocean"] == 1 and sec["potential"][0]["asin"] == "A"

    html = render.card(items[0], "ocean", render.ocean_group(items[0]))
    assert "第 <b>3</b> 名 / 80 个" in html and "头部中位 300" in html and 'class="grp">潜力<' in html
    note = render._ocean_note({"ocean": out})
    assert note.startswith("名单上符合 7 个 → 有日数据 5 个 → 核对通过 1 个") and "没有日数据 2" in note
    hint = render.section_hints(cfg)["ocean"]
    assert "≤180 天" in hint and "前 20 名中位数的 20%" in hint and "前 50 名" in hint
    assert render.section_hints(cfg, universe=False)["ocean"] == "本期没翻成全市场名单，不判蓝海产品。"


def test_pool_pulls_daily_data_for_blue_ocean_candidates(cfg):
    """名单上满足 ①~④ 的候选进追踪池（pool.ocean_max 个，按近 30 天销售额），每期都拉日数据核对。"""
    st = state_with(_market())
    pool, reasons = tracking.select_pool(None, st, cfg, TODAY, limit=200)
    assert reasons.get("OK") == "ocean" and list(reasons.values()).count("ocean") == 1
    cfg["pool"]["ocean_max"] = 0
    _pool, reasons = tracking.select_pool(None, st, cfg, TODAY, limit=200)
    assert reasons.get("OK") != "ocean"


def test_dingtalk_lists_blue_ocean_products(cfg):
    item = _item("A", label="potential", ocean={"asin": "A", "rank": 3, "n": 80, "revenue": 90_000.0, "ratings": 20.0,
                                                "head_reviews": 300.0, "limit": 60.0, "age": 61})
    ctx = {"title": "家具爆品雷达", "report_id": "2026-11-02", "as_of": "2026-11-01", "cfg": cfg,
           "sections": {"surge": [], "potential": [], "hot": [], "fake": [], "ocean": [item],
                        "counts": {"surge": 0, "potential": 0, "hot": 0, "fake": 0, "ocean": 1, "total": 1}},
           "diff": {"first": True}, "traits": {}}
    text = dingtalk.build_text(ctx)
    assert "🌊 蓝海 1" in text and "**🌊 蓝海产品**" in text
    assert "第3名/80个，评论20条（头部中位300），上架61天，也在潜力" in text

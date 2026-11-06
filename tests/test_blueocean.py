"""小件、不追踪的子类目（radar/discovery.py）和蓝海产品（radar/detect/blueocean.py）。"""
import copy
from datetime import date

from radar import analyze, discovery, tracking
from radar.detect import blueocean
from radar.notify import dingtalk
from radar.report import render

TODAY = date(2026, 11, 1)
NODE_A, NODE_B = "1:2:3", "1:2:4"


def row(asin, *, node=NODE_A, revenue=50_000, ratings=1000, available="2026-09-01", brand=None, rating=4.5,
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
    """几类细分人群 / 用途的商品，加上写法相近、但不是专门设计的（不算）。"""
    return [
        row("SEW", title="GIANTEX Folding Sewing Table with Charging Station, Storage Trays", revenue=44_000),
        row("REC1", title="Record Player Stand, Turntable Stand Holds up to 160 Albums", revenue=66_000),
        row("REC2", title="Vinyl Record Storage Cabinet with Power Outlets", revenue=30_000, ratings=50, rating=4.1),  # 评分不够
        row("BED", title="Overbed Bedside Table with Wheels, Foldable Rolling Desk Adjustable Height", revenue=27_000),
        row("CTAB", title="C Shaped End Table, Skinny Overbed Table for Coffee Drink Snack"),  # 没有轮子 / 可升降：不是卧床护理
        row("DRUM", title="Mid Century Modern Fluted Drum Table"),                              # 鼓形是造型
        row("KIDS", title="Kids Bookshelf for Classroom and Playroom"),                         # 适用场景的罗列
        row("SMALL", title="Record Player Stand, Small End Table", pkg={"dims": [17, 13, 5], "weight": 9}),  # 小件
    ]


def test_niche_products_are_found_by_their_targeted_design(cfg):
    """蓝海产品 = 标题写明是给特定人群 / 用途专门设计的（词表），在本期全市场名单里（量够门槛、在范围内、不是小件），
    这一类全市场不超过 max_products 个（圈子小），评分达标。写法相近、但不是专门设计的不算；名单不是本期翻的不判。"""
    st = state_with(_market())
    cands, niches = blueocean.scan(st, TODAY, cfg)
    assert {e["niche"] for e in cands.values()} == {"缝纫手工", "黑胶唱片", "卧床护理"}
    assert sorted(cands) == ["BED", "REC1", "SEW"]  # REC2 评分不够：不列，但算进这一类的个数（也是竞品）
    assert niches["黑胶唱片"]["n"] == 2 and niches["黑胶唱片"]["revenue"] == 96_000 and niches["黑胶唱片"]["small"]
    assert cands["REC1"]["n"] == 2 and cands["REC1"]["niche_revenue"] == 96_000
    cfg["thresholds"]["blue_ocean"]["max_products"] = 1  # 黑胶唱片 2 个：圈子不算小
    cands, niches = blueocean.scan(st, TODAY, cfg)
    assert "REC1" not in cands and not niches["黑胶唱片"]["small"] and "SEW" in cands
    assert blueocean.scan(state_with(_market(), date(2026, 10, 20)), TODAY, cfg) == ({}, {})


def test_niche_vocabulary_covers_hobby_and_commercial_scenes(cfg):
    """不只是小孩、老人用的：桌游、街机、台球这类具体小众爱好，影碟收藏、讲台、员工更衣柜、商用备餐台这类专门场景也算。
    只认写明专门用途的说法：普通折叠牌桌、电竞桌、写着 Podium 的电脑推车、更衣柜里放的置物架、车库工具柜、coffee bar 都不算。"""
    niches = blueocean.settings(cfg)["niches"]
    cases = {
        "Board Game Table with Vault and Removable Topper, Solid Wood Dining Table": "桌游牌桌",
        "10 Player Oval Poker Table with Cup Holders and Padded Rail": "桌游牌桌",
        "Wooden Puzzle Table with Drawers and Cover, 1500 Piece Jigsaw": "桌游牌桌",
        "Bartop Arcade Cabinet Kit for 19\" Monitor, MDF": "街机电玩",
        "HOOBRO Game Console Shelf, 4-Tier Video Gaming Consoles Stand with Outlet": "街机电玩",
        "7 FT Pool Table with Table Tennis Top, Billiard Accessories Included": "台球游戏室",
        "Wall Mounted Billiard Cue Rack, Holds 6 Cues": "台球游戏室",
        "Dartboard Cabinet Set with LED Light, Solid Wood": "台球游戏室",
        "VEVOR Media Storage Cabinet, CD Rack Tower, 34 Compartments Rotating": "影碟收藏",
        "Church Podium Stand, Wooden Lectern with Shelf": "讲台展会",
        "VEVOR Folding Portable Bar Tradeshow Podium Table for Exhibition": "讲台展会",
        "Metal Lockers for Employees with 12 Doors and Hooks": "商用更衣柜",
        "IKIMI 3 Doors Metal Storage Locker for Employee, 72\" Sports Staff Lockers": "商用更衣柜",
        "Stainless Steel Table for Prep & Work, 24x48 Inches, Commercial Heavy Duty": "餐饮后厨",
        "VEVOR Stainless Steel Work Table, Food Prep Table, Commercial Kitchen Workstation": "餐饮后厨",
        "Folding Card Table, Black, Padded Vinyl Top": None,
        "GTPLAYER Gaming Desk 55 Inch with LED Lights": None,
        "Mount-It! Mobile Computer Cart, Rolling Laptop Stand for Teachers, Office, Classroom, Podium, Keyboard Tray": None,
        "Locker Organizer Shelf Stackable Extra Locker Shelf for School Locker": None,
        "75\" Tall Wheeled Garage Storage Cabinet, Rolling Metal Tool Cabinets": None,
        "Coffee Bar Cabinet with Storage, Farmhouse Buffet Sideboard": None,
        "Outdoor Poolside Side Table, Weather Resistant": None,
        "TV Media Console Table for 65 Inch TV": None,
    }
    for title, want in cases.items():
        assert blueocean.niche_of(title, niches) == want, title


def test_too_small_or_too_simple_niche_products_are_not_blue_ocean(cfg):
    """圈子再小，太小、结构太简单的也不算：包装不到 30 磅又没有升降 / 翻转 / 折叠 / 翻板 / 暗格这类机构的；
    书架 / 置物架类（标题或主图），不到 50 磅、不是超大件、没有柜门也没有机构的（带个抽屉也还是书架）。
    只按重量或只按结构都不行：42 磅的玩具架要去掉，45 磅带枪支暗格的床头柜、结构简单但不小的蒙氏地床要保留。
    太小、结构太简单的不进候选，但算进这一类的个数。"""
    def pkg(weight, dims=(30.0, 14.0, 6.0)):
        return {"dims": list(dims), "weight": weight}
    rows = [
        row("SPACEAID", title="SpaceAid 4-Tier Scalloped Bookshelf, Montessori Bookcase for Toddlers", pkg=pkg(19.6)),
        row("DRAWER", title="3-Tier Montessori Scalloped Kids Bookshelf with Drawer", pkg=pkg(18.2)),
        row("OOOK", title="OOOK Montessori Bookshelf and Toy Storage Organizer with Book Rack", pkg=pkg(42.3)),
        row("FLOORBED", title="Twin Montessori Floor Bed with Rails", pkg=pkg(44.0, (76.0, 16.0, 6.0))),
        row("OVERBED", title="Overbed Bedside Table with Wheels, Pneumatic Adjustable Height", pkg=pkg(21.4)),
        row("GUN", title="Night Stand with Gun Drawer, Charging Bedside Table", pkg=pkg(38.5)),
        row("RECSTAND", title="Record Player Stand, Turntable Stand Holds up to 160 Albums", pkg=pkg(27.0)),
        row("FLIP", title="Record Player Stand with Flip-Down Record Shelf, Walnut", pkg=pkg(27.7)),
        row("CONSOLE", title="Large Record Player Stand with 2 Fluted Cabinets, Open Storage", pkg=pkg(41.8, (50.0, 18.0, 5.0))),
        row("NOPKG", title="Montessori Toy Shelf, 2 Tier"),
    ]
    st = state_with(rows)
    cands, niches = blueocean.scan(st, TODAY, cfg)
    assert sorted(cands) == ["CONSOLE", "FLIP", "FLOORBED", "GUN", "NOPKG", "OVERBED"]
    assert niches["蒙氏幼儿"]["n"] == 5 and niches["蒙氏幼儿"]["simple"] == 3 and niches["黑胶唱片"]["simple"] == 1
    c = blueocean.settings(cfg)
    assert blueocean.too_simple(st["universe"]["rows"]["SPACEAID"], "", c) == "太小"
    assert blueocean.too_simple(st["universe"]["rows"]["OOOK"], "", c) == "结构太简单"
    # 主图描述也算：标题看不出来是书架、主图是开放格的，同样是书架类；主图看到柜门的不算结构太简单
    shelf = row("S2", title="Wooden Storage Cabinet for Kids Room", pkg=pkg(40.0))
    assert blueocean.too_simple(shelf, "原木色低矮开放格柜，上层分格收纳", c) == "结构太简单"
    assert blueocean.too_simple(shelf, "原木色柜体，下层两扇柜门封闭储物，上层开放格", c) is None
    note = render._ocean_note({"ocean": {"candidates": 6, "daily": 0, "monthly": 0, "list_only": 6, "missed": {},
                                         "niches": niches}})
    assert "太小、结构太简单的 4 个不算" in note


def test_blue_ocean_products_must_be_new_listings_selling_enough(cfg):
    """⑥ 和潜力产品同一个“新品”口径：上架 ≤90 天（thresholds.potential.max_age_days），近 30 天 ≥50 件。老款、月售不够的不进候选，但算进这一类的个数（也是竞品）；
    上架日期不知道的判断不了，不算。11-02：评分达标的 74 个里上架不到 90 天的只有 4 个（GTPLAYER 小升降桌 298~401 天、
    Bellemave 老款地床 880~1056 天都去掉）。"""
    rows = [
        row("NEW", title="Twin Montessori Floor Bed with Rails", available="2026-09-01"),             # 61 天
        row("EDGE", title="Full Montessori Floor Bed with Fence", available="2026-08-03"),            # 90 天
        row("OLD", title="Queen Montessori Floor Bed with Slats", available="2026-08-02"),            # 91 天
        row("NODATE", title="Montessori Floor Bed Frame, Twin", available=None),
        row("FEW", title="L-Shaped Reception Desk with Counter", available="2026-09-15", revenue=44_000),
    ]
    rows[-1]["units"] = 36  # 卖得贵，销售额够全市场门槛，件数不够 50
    st = state_with(rows)
    cands, niches = blueocean.scan(st, TODAY, cfg)
    assert sorted(cands) == ["EDGE", "NEW"]
    assert niches["蒙氏幼儿"]["n"] == 4 and niches["蒙氏幼儿"]["old"] == 2 and niches["店铺前台"]["few"] == 1
    note = render._ocean_note({"ocean": {"candidates": 2, "daily": 0, "monthly": 0, "list_only": 2, "missed": {},
                                         "niches": niches}})
    assert "不是近期新上架的 2 个不算" in note and "月售不够的 1 个不算" in note
    assert "<b>蒙氏幼儿</b> 4 个 · 符合 2 个" in render._niche_row({"ocean": {"niches": niches}})
    hint = render.section_hints(cfg)["ocean"]
    assert "上架 ≤90 天、近 30 天 ≥50 件，而且在全市场名单里（近 30 天 ≥300 件或 ≥4 万美元）" in hint


def _item(asin, **kw):
    base = {"asin": asin, "parent": None, "label": "watch", "label_cn": "观察", "daily": True, "spark": [1.0] * 30,
            "stale": False, "relist": None, "fading": False, "tags": [], "age_days": 200, "rising": None,
            "title": "Record Player Stand", "brand": "NewCo", "node_cn": "音响架", "node_name": "Audio-Visual Shelving",
            "price": 150.0, "rating": 4.6, "ratings": 150, "image": "", "fake": {"score": 0, "level": None, "signals": []},
            "monthly": [], "spark_start": "2026-10-03", "recent_avg28": 20.0, "material": "实木", "features": []}
    return {**base, **kw}


def test_blue_ocean_list_checks_daily_data_when_it_has_it(cfg):
    """拉到了日数据的再核对：异常信号、名单和日数据对不上的不列；月度名单补判的持续热销照样算；没拉到日数据的按名单那一行
    做卡片，写明只有名单数据。商品照样留在自己的板块里，同一类排在一起（合计销售额大的类在前）。"""
    st = state_with([row(a, title=f"Record Player Stand {a}", revenue=60_000 - i) for i, a in enumerate("ABCDE")]
                    + [row("S", title="Folding Sewing Table", revenue=90_000)])
    cands, niches = blueocean.scan(st, TODAY, cfg)
    items = [_item("A", label="potential"), _item("B", label="fake", fake={"score": 4, "level": "suspect", "signals": []}),
             _item("C", tags=["名单和日数据对不上（名单近 30 天 900 件，日数据合计 30 件）：数据可能有误"]),
             _item("D", label="hot", daily=False, spark=[], source="monthly", hot={"avg_monthly": 500})]
    out, judged = blueocean.mark(items, cands)
    assert [i["asin"] for i in items if i.get("ocean")] == ["A", "D"] and items[0]["label"] == "potential"
    assert out == {"candidates": 6, "daily": 3, "monthly": 1, "missed": {"异常信号": 1, "名单和日数据对不上": 1}}
    extra = blueocean.list_items(st, cands, judged, TODAY)
    assert sorted(i["asin"] for i in extra) == ["E", "S"] and all(i["tags"] == [blueocean.LIST_ONLY] for i in extra)
    sec = analyze.sections(items + extra, 0)
    assert [i["asin"] for i in sec["ocean"]] == ["A", "D", "E", "S"]  # 黑胶唱片合计 $30 万在前，缝纫 $9 万在后
    assert sec["counts"]["ocean"] == 4 and sec["counts"]["total"] == 3  # 只有名单数据的不算追踪商品

    html = render.card(items[0], "ocean", render.ocean_group(items[0]))
    assert "<b>黑胶唱片</b> · 全市场 5 个" in html and 'class="grp">潜力<' in html and 'data-node="黑胶唱片"' in html
    cov = {"ocean": {**out, "niches": niches, "list_only": len(extra)}}
    note = render._ocean_note(cov)
    assert note.startswith("名单上符合 6 个：有日数据 3 个、月度补判 1 个、只有名单数据 2 个") and "异常信号 1" in note
    pop = render._ocean_pop({"shown": sec["ocean"]}, "x", 4, cov)
    assert '<b>黑胶唱片</b> 5 个 · 符合 5 个 · $30 万/月 · 5 个品牌' in pop and 'data-group="细分"' in pop
    hint = render.section_hints(cfg)["ocean"]
    assert "特定人群 / 用途" in hint and f"不超过 {cfg['thresholds']['blue_ocean']['max_products']} 个" in hint
    assert render.section_hints(cfg, universe=False)["ocean"] == "本期没翻成全市场名单，不判蓝海产品。"


def test_pool_pulls_daily_data_for_blue_ocean_products(cfg):
    """符合的商品进追踪池拉日数据（pool.ocean_max 个，按近 30 天销售额），卡片上才有走势图、才能核对异常信号。"""
    st = state_with(_market())
    _pool, reasons = tracking.select_pool(None, st, cfg, TODAY, limit=200)
    assert {a for a, why in reasons.items() if why == "ocean"} == {"BED", "REC1", "SEW"}
    cfg["pool"]["ocean_max"] = 1
    _pool, reasons = tracking.select_pool(None, st, cfg, TODAY, limit=200)
    assert [a for a, why in reasons.items() if why == "ocean"] == ["REC1"]


def test_dingtalk_lists_blue_ocean_products(cfg):
    item = _item("A", label="potential", age_days=95, ocean={"asin": "A", "niche": "黑胶唱片", "n": 9, "revenue": 79_000.0,
                                                             "niche_revenue": 390_000.0})
    ctx = {"title": "家具爆品雷达", "report_id": "2026-11-02", "as_of": "2026-11-01", "cfg": cfg,
           "sections": {"surge": [], "potential": [], "hot": [], "fake": [], "ocean": [item],
                        "counts": {"surge": 0, "potential": 0, "hot": 0, "fake": 0, "ocean": 1, "total": 1}},
           "diff": {"first": True}, "traits": {}}
    text = dingtalk.build_text(ctx)
    assert "🌊 蓝海 1" in text and "**🌊 蓝海产品**" in text
    assert "黑胶唱片（全市场9个），上架95天，近30天$8 万，也在潜力" in text

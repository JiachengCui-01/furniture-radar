"""列出哪些商品（detect/showcase.py）：简单款不列、相近款只列一款；判定和统计不变。"""
from radar.detect import showcase
from radar.report import render


def _item(asin, title, *, node="n1", brand="", material="板材", label="surge"):
    return {"asin": asin, "title": title, "brand": brand, "node": node, "node_cn": node, "material": material,
            "label": label, "image": f"https://m.media-amazon.com/images/I/{asin}._AC_US600_.jpg"}


def _state(tags_by_asin, protocol=2):
    """protocol 2 = 新提示词的识别结果（照用）；1 = 旧提示词的（造型 / 工艺 / 功能要标题也有才算，见 vision.normalized）。"""
    return {"asins": {a: {"vision": {"tags": tags, "summary": "", "protocol": protocol}} for a, tags in tags_by_asin.items()}}


def test_plain_needs_an_image_read_and_no_feature_beyond_the_basic_structure():
    """11-02 那期的例子：开放格收纳柜、金属平台床架主图只认出开放格 / 低矮落地 / 带储物，是简单款；
    只有双色拼接、金色金属件这类表面处理的也算；有凹槽竖纹、标题写了 LED 灯的不算；没识别主图的不判。"""
    state = _state({
        "CUBE": {"造型": ["开放格", "高柜/立式"], "功能卖点": ["带储物/抽屉"]},
        "BED": {"造型": ["低矮落地", "模块化组合"]},
        "TWOTONE": {"工艺": ["双色拼接", "金色金属件"]},
        "FLUTED": {"造型": ["开放格"], "工艺": ["凹槽竖纹"]},
        "LEDBED": {"工艺": ["双色拼接"]},
    })
    cube = _item("CUBE", "DUMOS 6-Cube Storage Organizer, Open Bookshelf")
    bed = _item("BED", "AIRBIRD 14\" Queen Bed Frame, Heavy Duty Metal Platform, Tool-Free Assembly")
    assert showcase.is_plain(cube, state) and showcase.features(cube, state) == []
    assert showcase.is_plain(bed, state)  # 免安装只是说明，不算特色
    assert showcase.is_plain(_item("TWOTONE", "8-Drawer Dresser for Bedroom"), state)
    assert not showcase.is_plain(_item("FLUTED", "Fluted Bookcase"), state)
    led = _item("LEDBED", "SHA CERLIN Queen Led Bed Frame, 2 Storage Drawers, Charging Station")
    assert not showcase.is_plain(led, state)
    assert showcase.features(led, state) == ["双色拼接", "LED 灯", "充电/插座"]  # 主图在前，标题补充
    assert not showcase.is_plain(_item("NOVISION", "Wood Entryway Bench with Rush Seat"), state)
    # 旧提示词的识别结果只留下标题也写了的特征，说不清“没有特色”：照“没识别主图”处理，不判简单款
    old = _state({"CUBE": {"造型": ["开放格"], "工艺": ["凹槽竖纹"]}}, protocol=1)
    assert not showcase.is_plain(cube, old) and showcase.features(cube, old) == []


def test_lookalikes_fold_into_the_first_listed_one():
    """同子类目、主图特征和标题用词平均一半以上相同的只列排在前面的那款；都带电子壁炉的电视柜算同一类款；
    不同子类目的不比；关掉 showcase 时全部列出。"""
    state = _state({
        "LT1": {"工艺": ["锥形/外八腿"], "功能卖点": ["升降台面"]},
        "LT2": {"工艺": ["锥形/外八腿"]},
        "FP1": {"工艺": ["玻璃门", "凹槽竖纹"], "功能卖点": ["LED 灯"]},
        "FP2": {"工艺": ["高光烤漆"], "功能卖点": ["LED 灯"]},
        "RT": {"造型": ["圆形/椭圆", "单柱/郁金香底座"]},
        "CUBE": {"造型": ["开放格"]},
        "LT3": {"工艺": ["锥形/外八腿"], "功能卖点": ["升降台面"]},
    })
    rows = [_item("LT1", "DUMOS Coffee Table, Wood Lift Top Coffee Table for Living Room", node="咖啡桌"),
            _item("FP1", "97\" Fireplace TV Stand with 36\" Electric Fireplace, Farmhouse", node="电视架"),
            _item("CUBE", "IULULU 8-Cube Bookcase", node="书架"),
            _item("LT2", "NEWBULIG Wood Lift Top Coffee Table with Storage for Living Room", node="咖啡桌"),
            _item("FP2", "OKD 70\" Glossy TV Console with Electric Fireplace Insert", node="电视架"),
            _item("RT", "Round Pedestal Coffee Table", node="咖啡桌"),
            _item("LT3", "Lift Top Coffee Table", node="茶几")]
    cfg = {"report": {"showcase": {"enabled": True, "hide_plain": True, "similar_min": 0.5}}}
    out = showcase.curate(rows, state, cfg)
    assert [i["asin"] for i in out["shown"]] == ["LT1", "FP1", "RT", "LT3"]  # 不同子类目的升降茶几照样列
    assert [i["asin"] for i in out["plain"]] == ["CUBE"]
    assert {k: [i["asin"] for i in v] for k, v in out["similar"].items()} == {"LT1": ["LT2"], "FP1": ["FP2"]}
    assert showcase.similarity(rows[1], rows[4], state) == 1.0  # 都带电子壁炉
    assert showcase.similarity(rows[0], rows[5], state) < 0.5
    off = showcase.curate(rows, state, {"report": {"showcase": {"enabled": False}}})
    assert off["shown"] == rows and not off["plain"] and not off["similar"]


def test_items_without_any_shape_evidence_compare_by_title_only():
    """没有主图识别、标题也没有特色的两个商品，只比标题用词：以前两边只剩主材质，同材质就算一半像，
    11-02 月度补判的儿童书架和玩具收纳柜被并成了一款；评论抽查的“点名”也因此少查了商品。"""
    state = {"asins": {}}
    a = _item("K1", "3 Sprouts Recycled Kids Bookshelf, Holds 35 Books", node="书柜")
    b = _item("K2", "FOTOSOK Toy Storage Organizer with Movable Drawers", node="书柜")
    c = _item("K3", "FOTOSOK Toy Storage Organizer with Movable Drawer for Kids", node="书柜")
    assert showcase.similarity(a, b, state) < 0.2
    assert showcase.similarity(b, c, state) >= 0.5  # 标题几乎一样的仍算相近
    bare = [{"asin": f"X{k}", "label": "surge", "node_cn": "书桌"} for k in range(3)]  # 连标题都没有
    assert len(showcase.curate(bare, state, {"report": {}})["shown"]) == 3


def test_gallery_takes_one_per_sub_category_by_default():
    rows = [_item(f"A{k}", "x", node=node) for k, node in enumerate(["a", "a", "b", "c", "b", "d"])]
    assert [i["asin"] for i in showcase.gallery(rows, 3, 1)] == ["A0", "A2", "A3"]
    assert [i["asin"] for i in showcase.gallery(rows, 10, 2)] == ["A0", "A1", "A2", "A3", "A4", "A5"]


def test_popup_says_how_many_were_left_out_and_does_not_show_plain_ones():
    """小窗顶部写判定了几个、列出几个；简单款只写个数，不放图和品名（以前折叠在底部，点开还是看得到 IULULU 八格书架这种）；
    卡片上带相近款的缩略图和特色。"""
    def item(asin, **kw):
        base = _item(asin, f"{asin} Coffee Table")
        base.update({"label_cn": "突然爆火", "price": 60.0, "rating": 4.5, "ratings": 100, "age_days": 80, "tags": [],
                     "fake": {"score": 0, "signals": [], "level": None}, "spark": [], "spark_start": None,
                     "surge": {"sales_ratio": 2.5, "base_avg": 31.0, "recent_avg": 79.0, "window": 14, "base_days": 28},
                     "prev_avg28": 30.0, "recent_avg28": 70.0, "features": ["升降台面"], "look": "灰木纹箱体"})
        base.update(kw)
        return base

    shown, folded, plain = item("S1"), item("S2"), item("P1", features=[])
    cur = {"shown": [shown], "plain": [plain], "similar": {"S1": [folded]}}
    html = render._section_pop("surge", "突然爆火", "说明。", cur, 3)
    assert "判定 3 个，列出 1 个（简单款 1 个没列，相近款 1 个记在相似的卡片上）" in html
    assert html.count('<article class="card"') == 1 and "相近款 1 个：" in html and "<i>升降台面</i>" in html
    assert "P1" not in html and "<details" not in html
    assert "._AC_SL300_.jpg" in html  # 卡片主图按需缩小


def test_plain_items_are_not_shown_in_changes_table_or_direction_rows():
    """与上期对比、全部追踪商品表、开发方向一行的缩略图和价格点也不放简单款：个数照算，写明几个没列。"""
    def item(asin, plain, **kw):
        base = _item(asin, f"{asin} 8-Cube Bookcase")
        base.update({"plain": plain, "brand": asin, "label": "surge", "label_cn": "突然爆火", "price": 49.99, "bsr": 100,
                     "rating": 4.3, "ratings": 28, "tags": [], "fake": {"score": 0}, "recent_avg28": 5.0, "source": "",
                     "node_cn": "书架", "node_name": "Bookcases", "image": f"https://m.media-amazon.com/images/I/{asin}.jpg"})
        base.update(kw)
        return base

    keep, simple, moved = item("KEEP1", False), item("PLAIN1", True), item("MOVED1", True)
    by_asin = {i["asin"]: i for i in (keep, simple, moved)}
    diff = {"still": [], "new": {"surge": ["KEEP1", "PLAIN1", "MOVED1"]}, "moved_from": {"MOVED1": "potential"},
            "recheck": {"total": 2, "held": [], "lost": {"KEEP1": "surge", "PLAIN1": "surge"}, "gone": []}}
    text = render._changes(diff, by_asin)
    assert "新进入「突然爆火」<b>3</b>" in text and "（简单款 2 个没列）" in text and "蓝框" not in text
    assert "KEEP1" in text and "PLAIN1" not in text and "MOVED1" not in text
    assert "不再是 2 个：" in text and "（简单款 1 个没列）" in text
    table = render._all_table([keep, simple])
    assert "KEEP1" in table and "PLAIN1" not in table
    row = render._board_row(0, {"node": "书架", "n": 2, "sales": {"increment": 100.0, "monthly": 500.0}, "materials": [],
                                "features": {dim: {"more": [], "base": []} for dim in render.directions.DIMENSIONS}},
                            None, [simple, keep], [], render.charts.price_domain([49.99]), 100.0)
    assert "KEEP1" in row and "PLAIN1" not in row
    dots = render._price_dots([simple, keep])
    assert "PLAIN1" not in dots[0][2] and "简单款" in dots[0][2] and "KEEP1" in dots[1][2]


def test_structure_most_of_a_sub_category_has_is_not_a_feature():
    """子类目里主图识别过的商品多数都有的造型 / 功能是这个品类的基本结构：普通铁艺衣帽架只有单柱底座和挂钩，是简单款
    （11-02 图墙里的 Tujurich 衣帽架）；有大理石底座（主图认出）的不是；标题里的 stone 不算材质（以主图为准）；
    工艺再常见也照样算；主图识别过的不到 common_min_items 个的子类目不扣。"""
    cfg = {"report": {"showcase": {"common_share": 0.5, "common_min_items": 10}}}
    racks = {f"R{k}": {"造型": ["单柱/郁金香底座"], "功能卖点": ["带挂钩"]} for k in range(9)}
    racks["MARBLE"] = {"造型": ["单柱/郁金香底座"], "功能卖点": ["带挂钩"], "材质": ["金属", "大理石纹"]}
    racks["FLUTE"] = {"造型": ["单柱/郁金香底座"], "工艺": ["凹槽竖纹"]}
    state = _state(racks)
    rows = [_item(a, "Coat Rack with 8 Hooks", node="衣帽架") for a in racks]
    common = showcase.common_features(rows, state, cfg)
    assert common == {"衣帽架": frozenset({"单柱/郁金香底座", "带挂钩"})}
    plain = _item("R0", "Coat Rack with 8 Hooks, 3 Adjustable Heights, Sturdy Metal Clothes Stand, Stone Base", node="衣帽架")
    assert showcase.features(plain, state) == ["单柱/郁金香底座", "带挂钩"]  # 不扣时照常有；adjustable heights、stone 都不算
    assert showcase.is_plain(plain, state, common) and not showcase.is_plain(plain, state)
    assert showcase.features(rows[9], state, common) == ["大理石纹"]
    assert showcase.features(rows[10], state, common) == ["凹槽竖纹"]
    assert showcase.common_features(rows[:9], state, cfg) == {}  # 只有 9 个识别过的
    assert showcase.curate(rows[:1], state, cfg, common)["plain"] == rows[:1]


def test_adjustable_heights_on_a_coat_rack_is_not_a_lifting_desk():
    from radar.detect import design

    assert "电动/可升降" not in design.extract("Coat Rack with 8 Hooks, 3 Adjustable Heights")["功能卖点"]
    assert "电动/可升降" in design.extract("Narrow Console Table with Adjustable Height (25\"-33\")")["功能卖点"]
    assert "电动/可升降" in design.extract("ErGear Height Adjustable Electric Standing Desk")["功能卖点"]


def test_cached_image_tags_are_reused_when_only_the_size_differs(cfg):
    """同一个 ASIN，月度名单给 ._AC_US200_、日数据给 ._AC_US600_：同一张图，不重看；换了图片编号才重看。
    以前按整个地址比，开发方向（月度名单的图）和上榜商品（当期的图）两步每期轮流把对方当旧图，每次约 50 张。"""
    import httpx

    from radar import pipeline, vision

    assert vision.same_image("https://m.media-amazon.com/images/I/61AYTycliAL._AC_US200_.jpg",
                             "https://images-na.ssl-images-amazon.com/images/I/61AYTycliAL._AC_US600_.jpg")
    assert not vision.same_image("https://x/images/I/81c2iv8Ly9L._AC_US200_.jpg", "https://x/images/I/81SSU2zvgCL._AC_US200_.jpg")
    assert vision.same_image("https://x/a.jpg", "https://x/a.jpg") and not vision.same_image("https://x/a.jpg", "https://x/b.jpg")

    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.method == "GET":
            return httpx.Response(200, content=b"\xff\xd8fake", headers={"content-type": "image/jpeg"})
        content = '{"风格":[],"材质":[],"造型":["开放格"],"工艺":[],"颜色":[],"外观要点":"x"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    old = {"image": "https://m.media-amazon.com/images/I/61AYTycliAL._AC_US200_.jpg", "vocab": vision.VOCAB_VERSION,
           "tags": {"造型": ["开放格"]}, "summary": "x"}
    state = {"asins": {"A": {"vision": dict(old)}, "B": {"vision": dict(old)}}}
    items = [{"asin": "A", "image": "https://images-na.ssl-images-amazon.com/images/I/61AYTycliAL._AC_US600_.jpg"},
             {"asin": "B", "image": "https://m.media-amazon.com/images/I/71NEWPHOTOL._AC_US200_.jpg"}]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert vision.tag_items(items, state, cfg, "key", client=client) == 1  # 只有换了图的 B
    assert all("61AYTycliAL" not in u for u in seen)
    # 开发方向的头部商品（月度名单行）在本期判定过的，用本期商品自己的图
    head = {"asin": "A", "image": "https://m.media-amazon.com/images/I/OLDHEADIMG._AC_US200_.jpg"}
    assert pipeline.current_rows([head, {"asin": "C"}], items)[0] is items[0]

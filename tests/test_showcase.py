"""列出哪些商品（detect/showcase.py）：简单款不列、相近款只列一款；判定和统计不变。"""
from radar.detect import showcase
from radar.report import render


def _item(asin, title, *, node="n1", brand="", material="板材", label="surge"):
    return {"asin": asin, "title": title, "brand": brand, "node": node, "node_cn": node, "material": material,
            "label": label, "image": f"https://m.media-amazon.com/images/I/{asin}._AC_US600_.jpg"}


def _state(tags_by_asin):
    return {"asins": {a: {"vision": {"tags": tags, "summary": ""}} for a, tags in tags_by_asin.items()}}


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


def test_popup_says_how_many_were_left_out_and_lists_plain_ones_folded():
    """小窗顶部写判定了几个、列出几个；简单款折叠在底部；卡片上带相近款的缩略图和特色。"""
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
    assert '<details class="skip"><summary>另有 1 个简单款没列出' in html and "P1 Coffee Table" in html
    assert "._AC_SL300_.jpg" in html  # 卡片主图按需缩小

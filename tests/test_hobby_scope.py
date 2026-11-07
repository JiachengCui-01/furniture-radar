"""兴趣爱好类目（scope.extra_nodes）：只要家具、不要小件；里面的商品只以蓝海候选进追踪池；
不判持续热销、不补往月名单、算往月名单“已齐”时不看它们（2026-11-07 用户要求，见 config.yaml 的 hobby_noun_regex）。"""
from __future__ import annotations

import copy
from datetime import date

import pytest

from radar import discovery, monthly_lists, tracking
from radar.detect import design, showcase

HOBBY = "2619533011:2975504011"
MAIN = "1055398:1063306:1063308:3733251"


@pytest.fixture
def hcfg(cfg):
    out = copy.deepcopy(cfg)
    out["scope"]["extra_nodes"] = [{"path": HOBBY, "label": "爬宠用品（含饲养箱）"}]
    return out


def _p(asin, title, node=HOBBY, weight=None, **kw):
    out = {"asin": asin, "parent": None, "title": title, "node": node, "node_name": "x", "brand": f"B{asin}",
           "units": 900, "revenue": 90_000, "rating": 4.6, "ratings": 500, "sources": ["top"], "price": 120.0}
    if weight is not None:
        out["pkg"] = {"dims": [50.0, 22.0, 10.0], "weight": weight}
    return {**out, **kw}


def test_hobby_nodes_keep_only_furniture_that_is_not_small(hcfg):
    keep = discovery.keep_product
    assert discovery.hobby_node(HOBBY, hcfg) and not discovery.hobby_node(MAIN, hcfg)
    # 11-07 那期真的进来过的：活体饲料虫（没有包装数据）、垫材、海报筒、墙上枪架、球衣相框
    for title, w in (("100 Small Cupped Live Dubia Roaches Live Arrival is Guaranteed", None),
                     ("Zoo Med Forest Floor Bedding (Pack of 2)", 25.75),
                     ("Mr. Pen- Poster Storage Tube with Strap, 25\"-40\" Extendable", 1.28),
                     ("Wall-Mounted Gun Rack for Long Guns, Rifles and Shotguns", 7.25),
                     ("Jersey Frame Display Case, Large Lockable Shadow Box", 11.0),
                     ("25-Shelf Heavy Duty Cabinet Door Paint Drying Rack with Wheels", 80.0)):
        assert not keep(_p("X", title, weight=w), hcfg), title
    # 家具：名词 + ≥30 磅（没有重量数据的只看名词）
    for title, w in (("55-75 Gallon Fish Tank Stand, Reptile Tank Stand with Power Outlets", 72.0),
                     ("120 Gallon Reptile Tank, Stackable 4x2x2 Reptile Enclosure", 49.0),
                     ("Tornado Classic II Foosball Table Soccer Game (Black)", None),
                     ("American Legend LED Shuffleboard with Buffet Top", 253.5)):
        assert keep(_p("Y", title, weight=w), hcfg), title
    assert not keep(_p("Z", "10 Gallon Fish Tank Stand, Metal", weight=17.5), hcfg)  # 太小的简单铁架
    # 家具类目不受这两条影响（还是原来的小件、配件规则）
    assert keep(_p("M", "Small Wood Nightstand", node=MAIN, weight=12.0), hcfg)
    # 都是读的时候过滤：不改监控范围的指纹（改了会重新发现，多花约 130 次调用）
    plain = copy.deepcopy(hcfg)
    for k in ("hobby_noun_regex", "hobby_min_weight"):
        plain["scope"].pop(k, None)
    assert discovery.scope_key(plain) == discovery.scope_key(hcfg)


def test_hobby_products_enter_the_pool_only_as_blue_ocean_candidates(hcfg, monkeypatch):
    disc = {"nodes": [{"path": MAIN}, {"path": HOBBY}],
            "products": {"H": _p("H", "Reptile Tank Stand with Storage Cabinet", weight=60.0),
                         "M": _p("M", "Wood Nightstand with Drawers", node=MAIN, weight=40.0)}}
    state = {"asins": {}, "runs": [], "universe": {}}
    monkeypatch.setattr(tracking.blueocean, "candidates", lambda *a, **k: {})
    order, reason = tracking.select_pool(disc, state, hcfg, date(2026, 11, 6))
    assert order == ["M"]  # 子类目头部里兴趣爱好类目的不占名额
    monkeypatch.setattr(tracking.blueocean, "candidates",
                        lambda *a, **k: {"H": {"asin": "H", "revenue": 90_000, "node": HOBBY}})
    order, reason = tracking.select_pool(disc, state, hcfg, date(2026, 11, 6))
    assert reason == {"M": "top", "H": "ocean"}


def test_hobby_nodes_do_not_block_monthly_lists_or_need_rolling_lists(hcfg):
    disc = {"nodes": [{"path": MAIN}, {"path": HOBBY}],
            "products": {"H": _p("H", "Reptile Tank Stand", units=50, revenue=5_000),
                         "M": _p("M", "Nightstand", node=MAIN, units=50, revenue=5_000)}}
    assert monthly_lists.thin_nodes(disc, hcfg) == {MAIN}


def test_title_only_structure_features_count_as_design():
    """壁床、上下铺、翻板这些结构只看标题；以前词表里没有，这样的商品说不出特色、被当成简单款。"""
    got = design.extract("Queen Horizontal Murphy Bed with Storage Shelf and Drop-Leaf Desk, Twin over Twin Bunk Bed")
    assert {"壁床/隐形床", "高架床/上下铺", "翻板/折叠台面"} <= set(got["功能卖点"])
    assert "错落/阶梯层板" in design.extract("6-Cube Bookshelf with Irregular Compartments")["造型"]
    assert {"壁床/隐形床", "高架床/上下铺", "翻板/折叠台面"} <= showcase.TITLE_FUNCTIONS
    # 不在主图词表里：识别提示词不变，旧的识别结果不用重看
    assert not ({"壁床/隐形床", "高架床/上下铺", "翻板/折叠台面"} & set(design.VISION_FUNCTIONS))


def test_stopped_hobby_categories_are_not_paged_or_listed(cfg):
    """几乎没有家具的 5 个兴趣爱好类目停查（exclude_node_regex 按 label 匹配）：商品读的时候剔除、子类目列表里去掉、
    选子类目时不再加进来；读的时候过滤，监控范围的指纹不变（不重新发现）。"""
    live = copy.deepcopy(cfg)
    live["scope"]["extra_nodes"] = [{"path": "165793011:19431275011:19431291011", "label": "收藏品展示收纳"},
                                    {"path": "3375251:10971181011:706808011:3397051:7427857011:3397171", "label": "台球桌"}]
    live["scope"]["exclude_node_regex"] = (
        "(?i)" + chr(92) + "bcoat racks?" + chr(92) + "b|^(?:纪念品展示柜|收藏品展示收纳|手工收纳|枪支弹药收纳柜|台球杆架)$")
    card = {"asin": "C", "title": "Card Display Cabinet with Glass Door", "node": "165793011:19431275011:19431291011",
            "node_name": "收藏品展示收纳", "pkg": {"dims": [40.0, 20.0, 15.0], "weight": 45.0}}
    assert not discovery.keep_product(card, live)
    disc = {"nodes": [{"path": card["node"], "name": "收藏品展示收纳"}, {"path": MAIN, "name": "Nightstands"}], "products": {}}
    assert [n["path"] for n in discovery.apply_product_filter(disc, live)["nodes"]] == [MAIN]
    chosen = discovery.select_nodes([], live)
    assert [n["name"] for n in chosen] == ["台球桌"]

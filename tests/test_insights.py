"""商品卡片上的“可能原因”和“发现”（detect/insights.py）：每一条都是和同子类目的够量商品比出来的、带数字。"""
from __future__ import annotations

import copy
from datetime import date

from radar import vision
from radar.detect import insights
from radar.report import render

TODAY = date(2026, 11, 16)
NODE = "1055398:1063306:1063318:1063310:14109851"


def _row(asin, price, units, *, title="Wood TV Stand", ratings=500, parent=None):
    return {"asin": asin, "parent": parent, "node": NODE, "node_name": "Television Stands", "node_cn": "电视柜",
            "title": title, "price": price, "units": units, "revenue": price * units, "ratings": ratings,
            "pkg": {"dims": [60.0, 20.0, 10.0], "weight": 80.0}}


def _seen(tags):
    return {"vision": {"image": "x", "vocab": vision.VOCAB_VERSION, "protocol": vision.PROTOCOL, "tags": tags,
                       "summary": "", "where": {}}}


def _world():
    # 10 个同类：价格 100~280，2 个标题写了 fireplace，看过主图的 10 个里 1 个有凹槽竖纹
    rows = [_row(f"P{i}", 100 + 20 * i, 1000 - 50 * i, title="Fireplace TV Stand" if i < 2 else "Wood TV Stand",
                 ratings=2000 if i < 3 else 300) for i in range(10)]
    me = _row("ME", 120, 900, title="Fluted TV Stand with Fireplace", ratings=40)
    state = {"asins": {}, "universe": {"date": TODAY.isoformat(), "rows": {r["asin"]: r for r in rows + [me]}},
             "vision_extra": {r["asin"]: _seen({"工艺": ["凹槽竖纹"]} if r["asin"] == "P5" else {"造型": ["开放格"]})
                              for r in rows}}
    return state, me


def test_reasons_and_findings_compare_with_the_same_sub_category(cfg):
    state, me = _world()
    growth = [{"asin": "G1", "parent": None, "node": NODE, "features": ["电子壁炉"]},
              {"asin": "G2", "parent": None, "node": NODE, "features": ["电子壁炉", "LED 灯"]}]
    ctx = insights.context(state, TODAY, cfg, growth)
    item = {**me, "features": ["凹槽竖纹", "电子壁炉"], "age_days": 60, "pkg": {"fba": 45.0}, "recent_avg28": 30.0}
    ins = insights.for_item(item, ctx, cfg)
    texts = [t for t, _tip in ins["reasons"]]
    # 凹槽竖纹：看过主图的 10 个同类里 1 个（10%）；电子壁炉：10 个同类的标题里 2 个（20%）
    assert texts[0] == "少见的元素：凹槽竖纹（同类 10%）、电子壁炉（同类 20%）"
    assert "看过主图的 10 个同类里 1 个有" in ins["reasons"][0][1] and "标题里 10 个同类里 2 个有" in ins["reasons"][0][1]
    assert "同类爆品也有：同子类目另 2 个增长商品里 2 个也有「电子壁炉」" in texts
    # 同类中位价 (180+200)/2 = 190 → $120 低 37%
    assert "价格更低：$120，比同类中位价 $190 低 37%" in texts
    # 名单销量 900：比它多的 P0(1000)、P1(950) → 第 3 / 11；上架 60 天、前 10%（11 × 0.1 → 前 1 名）不够
    finds = [t for t, _tip in ins["findings"]]
    assert "近 30 天销量排同类第 3 / 11" in finds and not any(t.startswith("新品追得快") for t in texts)
    assert "同价位（$102~$138）够量的同类 1 个，占同类销售额 8%" in finds  # 只有 P1：$114,000 ÷ 同类 $1,390,000
    assert "FBA 约 $45，占售价 38%（利润空间要算清楚）" in finds
    assert ins["short"] == "少见：凹槽竖纹·电子壁炉 · 比同类便宜 37%"
    # 图墙卡片：少见的元素标亮（rare），一句话里只写其余的（more），不和特征标签重复
    assert ins["rare"] == {"凹槽竖纹": 0.1, "电子壁炉": 0.2} and ins["more"] == "比同类便宜 37%"


def test_moat_new_listing_and_missing_data(cfg):
    state, me = _world()
    ctx = insights.context(state, TODAY, cfg, [])
    # 排第 1 的新品：新品追得快；评论是头部中位数（前 20 名 = 全部 10 个：中位 300）的 2 倍以上 → 评论护城河
    star = {**me, "units": None, "features": [], "age_days": 45, "ratings": 900, "recent_avg28": 40.0, "pkg": {}}
    state["universe"]["rows"]["ME"]["units"] = 1200
    ins = insights.for_item(star, insights.context(state, TODAY, cfg, []), cfg)
    texts = [t for t, _tip in ins["reasons"]]
    assert "新品追得快：上架 45 天、评论 900 条，近 30 天销量已排同类第 1" in texts
    assert any(t.startswith("评论护城河：评论 900 条，是同类销量前 20 名中位数（300）的 3.0 倍") for t in texts)
    # 主图看过的同类不够 min_seen：主图特征不比；名单不是本期的、同类太少：什么都不写
    few = copy.deepcopy(cfg)
    few["report"].setdefault("insights", {})["min_seen"] = 50
    ins = insights.for_item({**me, "features": ["凹槽竖纹"], "age_days": 400}, insights.context(state, TODAY, few, []), few)
    assert not any(t.startswith("少见的元素") for t, _ in ins["reasons"])
    assert insights.context(state, date(2026, 11, 17), cfg, []) == {}
    assert insights.for_item({**me, "node": "other", "features": []}, ctx, cfg) is None


def test_card_and_tile_show_the_insight(cfg):
    item = {"asin": "B0X", "title": "Fluted TV Stand", "brand": "X", "node_cn": "电视柜", "price": 120.0, "image": "",
            "label": "surge", "label_cn": "突然爆火", "tags": [], "fake": {"score": 0, "level": None, "signals": []},
            "surge": None, "monthly": [], "spark": [], "features": ["凹槽竖纹"],
            "insight": {"reasons": [("少见的元素：凹槽竖纹（同类 10%）", "口径")], "findings": [("近 30 天销量排同类第 3 / 11", "口径")],
                        "short": "少见：凹槽竖纹"}}
    html = render._insight(item, "surge")
    assert "可能为什么在涨" in html and "和同类比" in html and 'data-tip="口径"' in html
    assert "可能为什么卖得稳" in render._insight(item, "hot")
    assert render._insight({**item, "insight": None}, "surge") == ""

"""商品卡片上和同类比的发现（detect/insights.py）和大模型写的两句话（cardnotes.py）：每一条都是和同子类目的够量商品比出来的、带数字；
大模型写的逐条核对，对不上的用规则句子。"""
from __future__ import annotations

import copy
import json
from datetime import date

import httpx

from radar import cardnotes, vision
from radar.detect import insights
from radar.report import render

TODAY = date(2026, 11, 16)
NODE = "1055398:1063306:1063318:1063310:14109851"


def _row(asin, price, units, *, title="Wood TV Stand", ratings=500, parent=None):
    return {"asin": asin, "parent": parent, "node": NODE, "node_name": "Television Stands", "node_cn": "电视柜",
            "title": title, "price": price, "units": units, "revenue": price * units, "ratings": ratings,
            "pkg": {"dims": [60.0, 20.0, 10.0], "weight": 80.0, "fba": round(price * 0.2, 2)}}


def _seen(tags):
    return {"vision": {"image": "x", "vocab": vision.VOCAB_VERSION, "protocol": vision.PROTOCOL, "tags": tags,
                       "summary": "", "where": {}}}


def _world():
    # 10 个同类：价格 100~280，2 个标题写了 fireplace，看过主图的 10 个里 9 个开放格、1 个凹槽竖纹；FBA 都是售价的 20%
    rows = [_row(f"P{i}", 100 + 20 * i, 1000 - 50 * i, title="Fireplace TV Stand" if i < 2 else "Wood TV Stand",
                 ratings=2000 if i < 3 else 300) for i in range(10)]
    me = _row("ME", 120, 900, title="Fluted TV Stand with Fireplace", ratings=40)
    state = {"asins": {}, "universe": {"date": TODAY.isoformat(), "rows": {r["asin"]: r for r in rows + [me]}},
             "vision_extra": {r["asin"]: _seen({"工艺": ["凹槽竖纹"]} if r["asin"] == "P5" else {"造型": ["开放格"]})
                              for r in rows}}
    return state, me


def _item(me, **kw):
    return {**me, "features": ["凹槽竖纹", "电子壁炉"], "design": {"工艺": ["凹槽竖纹"], "功能卖点": ["电子壁炉"]},
            "age_days": 400, "pkg": {"fba": 45.0}, "recent_avg28": 30.0, **kw}


def test_written_comparison_reasons_and_market_position(cfg):
    state, me = _world()
    growth = [{"asin": "G1", "parent": None, "node": NODE, "features": ["电子壁炉"]},
              {"asin": "G2", "parent": None, "node": NODE, "features": ["电子壁炉", "LED 灯"]}]
    ins = insights.for_item(_item(me, age_days=60), insights.context(state, TODAY, cfg, growth), cfg)
    # 外观：看过主图的 10 个同类里 9 个开放格（多是）、1 个凹槽竖纹（不同）；功能：电子壁炉只看标题，10 个同类里 2 个
    lines = [t for t, _tip in ins["compare"]]
    assert lines == ["外观：同类电视柜多是开放格（90%）；它不同在凹槽竖纹（同类 10%）。",
                     "功能：它还有电子壁炉（同类 20%），同类里少见。"]
    assert "看过主图的 10 个同类里 1 个有" in ins["compare"][0][1] and "标题里 10 个同类里 2 个有" in ins["compare"][1][1]
    texts = [t for t, _tip in ins["reasons"]]
    assert "同类爆品也有：同子类目另 2 个增长商品里 2 个也有「电子壁炉」" in texts
    # 价格更低按同类分布：最便宜的 25% = ≤ $145（100~280 的四分位），而且比中位价 $190 至少低 10%
    assert "价格更低：$120，在同类最便宜的 25% 里（同类中位 $190，便宜 37%）" in texts
    finds = [t for t, _tip in ins["findings"]]
    # 名单销量 900：比它多的 P0(1000)、P1(950) → 第 3 / 11；上架 60 天，前 10%（11 × 0.1 → 前 1 名）不够
    assert "近 30 天销量排同类第 3 / 11" in finds and not any(t.startswith("新品追得快") for t in texts)
    assert "同价位（$102~$138）够量的同类 1 个，占同类销售额 8%" in finds  # 只有 P1：$114,000 ÷ 同类 $1,390,000
    # FBA：同类都是售价的 20%（75 分位 20%），它 38% → 写
    assert "FBA 约 $45，占售价 38%，比同类 75% 的商品都高（利润空间要算清楚）" in finds
    assert ins["short"] == "比同类多：凹槽竖纹、电子壁炉 · 比同类便宜 37%"
    assert ins["rare"] == {"凹槽竖纹": 0.1, "电子壁炉": 0.2}


def test_moat_new_listing_premium_and_missing_data(cfg):
    state, me = _world()
    state["universe"]["rows"]["ME"]["units"] = 1200
    ctx = insights.context(state, TODAY, cfg, [])
    # 排第 1 的新品：新品追得快；评论 2,400 在同类前 10%（≥ 2,000）且 ≥ 头部中位数 300 的 1.5 倍 → 评论护城河
    star = _item(me, age_days=45, ratings=2400, design={}, price=270.0)
    ins = insights.for_item(star, ctx, cfg)
    texts = [t for t, _tip in ins["reasons"]]
    assert "新品追得快：上架 45 天、评论 2,400 条，近 30 天销量已排同类第 1" in texts
    assert "评论护城河：评论 2,400 条，在同类前 10%，是同类销量前 20 名中位数（300）的 8.0 倍" in texts
    assert "价格在同类最贵的 25% 里（$270，同类中位 $190，贵 42%）" in [t for t, _ in ins["findings"]]
    # 评论 900：没进同类前 10%，不算护城河
    ins = insights.for_item(_item(me, ratings=900), ctx, cfg)
    assert not any(t.startswith("评论护城河") for t, _ in ins["reasons"])
    # 看过主图的同类不够 min_seen：主图特征不比；名单不是本期的、子类目不在名单里：什么都不写
    few = copy.deepcopy(cfg)
    few["report"].setdefault("insights", {})["min_seen"] = 50
    ins = insights.for_item(_item(me), insights.context(state, TODAY, few, []), few)
    assert [t for t, _ in ins["compare"]] == ["功能：它还有电子壁炉（同类 20%），同类里少见。"]
    assert insights.context(state, date(2026, 11, 17), cfg, []) == {}
    assert insights.for_item({**me, "node": "other", "features": []}, ctx, cfg) is None


def test_ai_notes_are_checked_against_the_facts(cfg):
    state, me = _world()
    item = _item(me)
    item["insight"] = insights.for_item(item, insights.context(state, TODAY, cfg, []), cfg)
    facts = cardnotes.facts_of(item, "突然爆火")
    ok = {"对比": "同类电视柜多是开放格，它多了凹槽竖纹和电子壁炉。", "原因": "价格便宜 37%，可能靠低价和少见的凹槽竖纹。"}
    # 大模型写了“可能”也去掉（描述里不写“可能”）
    assert cardnotes.check(ok, facts, cfg) == ({"compare": ok["对比"], "why": "价格便宜 37%，靠低价和少见的凹槽竖纹。"}, None)
    # 百分比要对上百分比：事实里有 FBA“$45”，但没有“45%”
    assert cardnotes.check({**ok, "原因": "价格便宜 45%，可能靠低价。"}, facts, cfg)[1] == "数字 45% 不在事实里"
    assert cardnotes.check({**ok, "原因": "价格才 $120，比同类中位 $190 便宜。"}, facts, cfg)[0] is not None
    assert cardnotes.check({**ok, "原因": "价格才 $99。"}, facts, cfg)[1] == "数字 $99 不在事实里"
    assert cardnotes.check({**ok, "对比": "它多了 LED 灯。"}, facts, cfg)[1] == "特征 LED 灯 不在事实里"
    assert cardnotes.check({**ok, "原因": "一定会爆。"}, facts, cfg)[1] == "绝对化"
    assert cardnotes.check({**ok, "对比": "很长" * 50}, facts, cfg)[1] == "太长"
    # 没有外观、功能对比的：不给大模型写（说不出哪里不一样）
    assert cardnotes.facts_of({**item, "insight": {**item["insight"], "compare": []}}, "潜力") is None


def test_ai_notes_are_cached_and_fall_back_to_rules(cfg):
    state, me = _world()
    item = _item(me)
    item["insight"] = insights.for_item(item, insights.context(state, TODAY, cfg, []), cfg)
    other = {**_item(me, asin="ME2"), "insight": copy.deepcopy(item["insight"])}
    replies = {"ME": {"对比": "同类电视柜多是开放格，它多了凹槽竖纹。", "原因": "可能靠少见的凹槽竖纹。"},
               "ME2": {"对比": "它多了 LED 灯。", "原因": "可能靠灯。"}}  # ME2 编了事实里没有的 LED 灯
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(replies, ensure_ascii=False)}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = {}
    stats = cardnotes.write([(item, "突然爆火"), (other, "潜力")], store, cfg, "k", client=client)
    assert stats == {"cached": 0, "written": 1, "rejected": 1, "skipped": 0} and len(calls) == 1
    assert item["insight"]["note"]["compare"].startswith("同类电视柜") and "note" not in other["insight"]
    # 事实没变：下次直接用缓存，不再调用；没通过核对的下次再试
    item2 = {**item, "insight": {k: v for k, v in item["insight"].items() if k != "note"}}
    stats = cardnotes.write([(item2, "突然爆火")], store, cfg, "k", client=client)
    assert stats["cached"] == 1 and len(calls) == 1 and item2["insight"]["note"]
    # 没有大模型密钥：全部用规则句子
    assert cardnotes.write([(other, "潜力")], {}, cfg, "")["skipped"] == 1


def test_card_shows_ai_note_with_rule_basis_folded(cfg):
    ins = {"compare": [("外观：同类电视柜多是开放格（90%）；它不同在凹槽竖纹（同类 10%）。", "口径")],
           "reasons": [("价格更低：$120，在同类最便宜的 25% 里（同类中位 $190，便宜 37%）", "口径")],
           "findings": [("近 30 天销量排同类第 3 / 11", "口径")], "short": "比同类多：凹槽竖纹", "rare": {"凹槽竖纹": 0.1}}
    item = {"asin": "B0X", "node_cn": "电视柜", "insight": ins}
    html = render._insight(item, "surge")
    assert "和同类电视柜比" in html and "为什么在涨" in html and "市场位置" in html and "ins-more" not in html
    assert "可能" not in html  # 描述里不写“可能”（2026-11-16 用户要求）
    item["insight"] = {**ins, "note": {"compare": "同类多是开放格，它多了凹槽竖纹。", "why": "可能靠少见的凹槽竖纹。"}}
    html = render._insight(item, "hot")
    assert "同类多是开放格，它多了凹槽竖纹。" in html and "为什么卖得稳：" in html and "可能" not in html
    assert '<details class="ins-more"><summary>依据</summary>' in html and "外观、功能和同类比" in html
    assert render._insight({"insight": None}, "surge") == ""

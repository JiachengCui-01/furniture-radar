"""潜力SKU（radar/detect/potential_sku.py，2026-11-17 领导要求“抓一抓潜力SKU”）：用户说是“要很大概率以后能卖爆的”，
回测里最准的也只有三成，所以分两层都列：爆发苗头（近 7 天翻倍）+ 稳步在涨（还在加速的增长商品、刚起势），各写回测命中率。
另成一份清单（报告第 6 张 KPI、正文前几个、小窗表格可下载、钉钉一段），原板块都不动；原“潜力”板块改名“新品潜力”。"""
import json
import re

from radar import analyze, tracking
from radar.detect import diff as diffing
from radar.detect import potential_sku
from radar.notify import dingtalk
from radar.report import render


def _item(asin, **kw):
    base = {"asin": asin, "parent": None, "label": "watch", "label_cn": "观察", "daily": True, "spark": [1.0] * 30,
            "stale": False, "relist": None, "fading": False, "tags": [], "age_days": 200, "rising": None,
            "surge": None, "potential": None, "baseline_unknown": False, "recovered": None, "dormant": False,
            "title": f"Wood Nightstand {asin}", "brand": f"Brand{asin}", "node_cn": "床头柜", "node_name": "Nightstands",
            "price": 150.0, "rating": 4.6, "ratings": 150, "image": "", "fake": {"score": 0, "level": None, "signals": []},
            "monthly": [], "spark_start": "2026-10-03", "material": "实木", "features": [],
            "recent_avg7": 13.0, "last7_avg": 13.0, "recent_avg14": 12.0, "recent_avg28": 10.0, "prev_avg28": 10.0,
            "prev_in28": 28}
    return {**base, **kw}


def _rising(asin, a14, **kw):
    return _item(asin, rising={"ratio": 1.5}, recent_avg14=a14, recent_avg28=10.0, prev_avg28=6.0, **kw)


def _boom(asin, last7=20.0, **kw):
    return _item(asin, last7_avg=last7, **kw)


def test_boom_seedlings_doubled_in_the_last_week(cfg):
    """爆发苗头 = 近 7 天日均 ≥ 近 28 天日均 × 2、没回落、不是老品换链接、按近 7 天的节奏一个月 ≥300 件或 ≥4 万美元；
    哪个板块的都算。用 last7_avg（所有商品同一个口径），不用 recent_avg7（爆火商品那里是爆发窗口的日均）。"""
    s = potential_sku.settings(cfg)
    assert potential_sku.classify(_boom("A"), s) == {"type": "boom", "tier": "爆发苗头", "kind": "", "a7": 2.0, "a14": 1.2}
    assert potential_sku.classify(_boom("B", 19.9), s)["kind"] == "刚起势"  # 差一点翻倍：落到稳步在涨的刚起势
    assert potential_sku.classify(_boom("C", fading=True), s) is None
    assert potential_sku.classify(_boom("D", relist={"first_day": "2026-10-01"}), s) is None
    small = dict(recent_avg7=8.0, last7_avg=8.0, recent_avg14=5.0, recent_avg28=4.0, prev_avg28=4.0)  # 240 件 × $150 = $3.6 万
    assert potential_sku.classify(_item("E", **small), s) is None  # 刚起势也不够量（近 28 天 120 件）
    assert potential_sku.classify(_item("F", **small, price=200.0), s)["type"] == "boom"  # 240 件 × $200 = $4.8 万
    surge = _boom("G", 25.0, label="surge", surge={"kind": "稳在高位"}, recent_avg14=15.0)
    assert potential_sku.classify(surge, s)["kind"] == "突然爆火" and potential_sku.classify(surge, s)["tier"] == "爆发苗头"
    # 爆火商品的 recent_avg7 是爆发窗口的日均（这里 30），近 7 天其实只有 15：不是爆发苗头，最近两周还在加速就是稳步在涨
    window = _item("H", label="surge", surge={"kind": "稳在高位"}, recent_avg7=30.0, last7_avg=15.0, recent_avg14=15.0)
    assert potential_sku.classify(window, s)["tier"] == "稳步在涨"
    assert potential_sku.why({**_boom("A"), "sku": potential_sku.classify(_boom("A"), s)}) == \
        "近 7 天日均是近 28 天的 ×2.00，最近一周的节奏翻倍了；按这个节奏一个月约 600 件"


def test_steady_rising_items_count_only_when_still_accelerating(cfg):
    """稳步在涨（正在涨）= 突然爆火 / 新品潜力 / 上升中 / 新品起量里近 14 天日均 ≥ 近 28 天日均 × 1.1（最近两周还在加速）。
    没再加速的（原先的“待观察”）不进清单：回测不比同门槛的普通商品强，也不会被当成刚起势。"""
    s = potential_sku.settings(cfg)
    assert potential_sku.classify(_rising("A", 11.0), s) == \
        {"type": "rising", "tier": "稳步在涨", "kind": "上升中", "a7": 1.3, "a14": 1.1}
    assert potential_sku.classify(_rising("B", 10.9), s) is None
    assert potential_sku.classify(_rising("B2", None), s) is None  # 近 14 天没有日数据，看不出还在不在加速
    surge = potential_sku.classify(_item("C", label="surge", surge={"kind": "稳在高位"}, recent_avg14=15.0), s)
    assert surge["kind"] == "突然爆火" and surge["tier"] == "稳步在涨"
    assert potential_sku.classify(_item("D", label="potential", recent_avg14=11.0), s)["kind"] == "新品潜力"
    assert potential_sku.classify(_item("D2", label="potential", recent_avg14=9.0), s) is None
    assert potential_sku.classify(_rising("E", 12.0, label="hot"), s)["kind"] == "上升中"  # 持续热销里带上升中的也算
    ramp = _item("F", tags=["新品起量（对比数据不足，暂不确认新品潜力）"], recent_avg14=11.5, prev_avg28=None)
    assert potential_sku.classify(ramp, s) == {"type": "rising", "tier": "稳步在涨", "kind": "新品起量", "a7": 1.3, "a14": 1.15}
    assert potential_sku.classify({**ramp, "recent_avg14": 10.0}, s) is None
    loose = {**s, "early_accel": 1.0}  # 刚起势的加速门槛调低、数字够了，上升中没再加速的也不算刚起势（它已经在增长商品里）
    assert potential_sku.classify(_rising("G", 10.5, recent_avg7=12.0, last7_avg=12.0), loose) is None
    assert potential_sku.classify(_item("G2", recent_avg14=10.5, recent_avg7=12.0, last7_avg=12.0), loose)["kind"] == "刚起势"


def test_early_risers_must_accelerate_without_falling_and_have_volume(cfg):
    """稳步在涨（刚起势）= 还没到增长门槛、没在跌（近 28 天 ≥ 之前 28 天 × 0.9）、近 14 天和近 7 天日均都 ≥ 近 28 天 × 1.1、
    近 30 天 ≥300 件或 ≥4 万美元、不是老品换链接、没有回落。"""
    s = potential_sku.settings(cfg)
    early = potential_sku.classify(_item("A"), s)  # 28 天 10 件/天 = 300 件，14 天 ×1.2、7 天 ×1.3
    assert early == {"type": "early", "tier": "稳步在涨", "kind": "刚起势", "a7": 1.3, "a14": 1.2, "base": 1.0}
    assert potential_sku.classify(_item("B", recent_avg28=10.0, prev_avg28=11.2), s) is None  # 近 28 天跌到之前的 89%
    assert potential_sku.classify(_item("C", last7_avg=10.9), s) is None  # 近 7 天没跟上
    assert potential_sku.classify(_item("D", recent_avg14=10.9), s) is None
    small = dict(recent_avg7=6.5, last7_avg=6.5, recent_avg14=6.0, recent_avg28=5.0, prev_avg28=5.0)  # 150 件 × $150
    assert potential_sku.classify(_item("E", **small), s) is None
    assert potential_sku.classify(_item("F", **small, price=300.0), s)["kind"] == "刚起势"  # 150 件 × $300 = $4.5 万
    assert potential_sku.classify(_item("G", relist={"first_day": "2026-10-01"}), s) is None
    assert potential_sku.classify(_item("H", fading=True), s) is None
    assert potential_sku.classify(_item("I", prev_avg28=None), s) is None  # 对比期不知道，判断不了没在跌


def test_excluded_items_are_not_potential_skus(cfg):
    """评分偏低、异常信号、蓝海产品、数据陈旧、只有月度名单数据的都不进清单（两层都是）。"""
    s = potential_sku.settings(cfg)
    for kw in ({"label": "low"}, {"label": "fake"}, {"ocean": {"niche": "黑胶唱片"}}, {"stale": True},
               {"daily": False, "label": "hot"}):
        assert potential_sku.classify(_rising("A", 12.0, **kw), s) is None, kw
        assert potential_sku.classify(_boom("B", **kw), s) is None, kw


def test_sections_order_boom_then_steady(cfg):
    """清单顺序：爆发苗头（按近 7 天节奏一个月的销售额）→ 稳步在涨里正在涨的（按月增量）→ 刚起势（按近 30 天销售额）。"""
    items = [_item("E1"), _rising("W1", 10.0, price=500.0), _rising("S1", 12.0, price=100.0), _rising("S2", 12.0, price=300.0),
             _item("E2", price=400.0), _item("X", last7_avg=9.0), _boom("B2", 25.0, price=100.0), _boom("B1", 20.0, price=150.0)]
    assert potential_sku.mark(items, cfg) == 6
    sec = analyze.sections(items, 0)
    assert [i["asin"] for i in sec["sku"]] == ["B1", "B2", "S2", "S1", "E2", "E1"]  # $9 万 > $7.5 万
    c = sec["counts"]
    assert (c["sku"], c["sku_boom"], c["rising"]) == (6, 2, 3)
    assert potential_sku.record(items) == {"E1": ["稳步在涨", "刚起势"], "S1": ["稳步在涨", "上升中"], "S2": ["稳步在涨", "上升中"],
                                           "E2": ["稳步在涨", "刚起势"], "B2": ["爆发苗头", ""], "B1": ["爆发苗头", ""]}


def test_next_run_rechecks_last_runs_list_and_fetches_it_first(cfg):
    """上期的潜力SKU本期优先拉日数据（tracking.watchlist），“与上期对比”写出仍在清单 / 不再符合 / 没追踪各几个。"""
    prev = {"id": "2026-11-16", "labels": {"A": ["watch"], "B": ["surge"], "C": ["watch"]},
            "sku": {"A": ["稳步在涨", "上升中"], "B": ["爆发苗头", "突然爆火"], "C": ["稳步在涨", "刚起势"], "D": ["爆发苗头", ""]}}
    state = {"runs": [prev], "asins": {}}
    assert tracking.watchlist(state, 1) == ["B", "A", "C", "D"]  # 爆火 / 潜力 / 异常在前，潜力SKU 在后
    current = {"A": ["watch"], "B": ["watch"], "C": ["watch"]}
    out = diffing.compare(prev, current, sku={"A": ["稳步在涨", "上升中"], "C": ["稳步在涨", "刚起势"]})
    sc = out["sku_check"]
    assert sc == {"total": 4, "held": ["A", "C"], "lost": {"B": "爆发苗头"}, "gone": ["D"]}
    assert diffing.compare(None, current)["sku_check"] is None
    assert diffing.compare({"labels": {}}, current, sku={})["sku_check"] is None  # 旧的运行记录没有 sku
    html = render._changes(out, {})
    assert "上期潜力SKU 4 个：本期仍在清单 2 个，还在追踪但不再符合 1 个" in html
    assert "（上期爆发苗头）" in html and "本期没追踪 1 个" in html


def test_report_block_table_and_download(cfg):
    """第 6 张 KPI、正文前几个（每个子类目最多 per_node 个）、小窗表格：每行带筛选 / 排序用的 data-*，CSV 一行一个商品，
    点一行展开完整卡片；提示里的数字取自 config.yaml。"""
    items = [_rising("S1", 12.0), _rising("S2", 12.0, node_cn="书架"), _boom("B1", node_cn="电视柜"),
             _rising("W1", 10.0, node_cn="电视柜"), _item("E1", node_cn="床架", tags=["降价驱动"])]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    cur = {"shown": sec["sku"], "plain": [], "similar": {}}
    cfg["report"]["potential_sku"] = {"body_rows": 10, "per_node": 1}
    body = render._sku_section(cur, cfg)
    assert 'id="sku"' in body and 'data-pop="sku"' in body and "全部 4 个 · 表格可下载" in body
    assert body.count('class="sr"') == 4 and 'class="sc"' not in body  # 正文不展开卡片
    assert "列出 4 个：爆发苗头 1 个、稳步在涨 3 个。" in body and "W1" not in body
    pop = render._sku_pop(cur, render.sku_hint(cfg), 4, "2026-11-16")
    assert 'id="pop-sku"' in pop and "data-sku" in pop and 'data-file="潜力SKU-2026-11-16"' in pop
    assert pop.count('class="sc" hidden') == 4
    assert 'class="grp">稳步在涨·上升中<' in pop and 'class="grp">爆发苗头<' in pop
    assert "按近7天节奏一个月" in pop  # 不在增长板块里的爆发苗头，卡片写近 7 天的节奏
    head = json.loads(re.search(r'data-head="([^"]+)"', pop).group(1).replace("&quot;", '"'))
    assert head[:7] == ["ASIN", "亚马逊链接", "品牌", "标题", "子类目", "层", "类型"] and len(head) == len(render.SKU_CSV)
    assert head[11:13] == ["近7天÷近28天", "近14天÷近28天"]
    rows = [json.loads(m.replace("&quot;", '"')) for m in re.findall(r'data-csv="([^"]+)"', pop)]
    assert len(rows) == 4 and all(len(r) == len(head) for r in rows)
    assert rows[0][0] == "B1"
    early = next(r for r in rows if r[0] == "E1")
    assert early[5:7] == ["稳步在涨", "刚起势"] and early[8] == 300 and early[9] == 45000 and early[10] == ""
    assert early[11:13] == [1.3, 1.2] and early[17] == "降价驱动"
    assert 'data-seg="爆发苗头"' in pop and 'data-seg="稳步在涨"' in pop and 'data-a7="2.0"' in pop
    hint = render.sku_hint(cfg)
    assert "爆发苗头：近 7 天日均 ≥ 近 28 天的 2 倍、没回落、不是老品换链接，按这个节奏一个月 ≥300 件或 ≥4 万美元" in hint
    assert "卖爆 = 第 2 个月的 30 天销量 ≥ 现在 2 倍且 ≥300 件" in hint
    assert "爆发苗头卖爆 30%、新进子类目销量前 10% 的 17%" in hint and "稳步在涨卖爆 6.5%" in hint
    assert "全部商品卖爆 3.0%" in hint and "把握大" not in hint and "待观察" not in pop
    js = render.SKU_JS
    assert "window.skuTable" in js and "\\ufeff" in js and "/\\bsc\\b/" in js and "'-a7'" in js and "'爆发苗头','稳步在涨'" in js


def test_dingtalk_lists_potential_skus(cfg):
    items = [_rising("S1", 12.0), _item("E1", tags=["降价驱动"]), _boom("B1")]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    ctx = {"title": "家具爆品雷达", "report_id": "2026-11-16", "as_of": "2026-11-15", "cfg": cfg,
           "sections": {**sec, "surge": [], "potential": [], "hot": [], "fake": [], "ocean": []},
           "diff": {"first": True}, "traits": {}}
    text = dingtalk.build_text(ctx)
    assert "🎯 潜力SKU 3**" in text
    assert "**🎯 潜力SKU**（爆发苗头 1，回测 30% 之后卖爆；稳步在涨 2，回测 6.5% 之后卖爆；表格在报告里可下载）" in text
    assert "爆发苗头：" in text and "稳步在涨·上升中：" in text and "稳步在涨·刚起势：" in text and "（风险：降价驱动）" in text
    assert text.index("爆发苗头：") < text.index("稳步在涨·上升中：")
    assert dingtalk.build_text(ctx, sku_items=2).count("刚起势：") == 0


def test_old_potential_section_is_now_called_new_product_potential(cfg):
    """原“潜力”板块改名“新品潜力”：规则、商品、内部 key 都不变，只是名字和潜力SKU 分开（一个词不能有两种意思）。"""
    assert analyze.LABEL_CN["potential"] == "新品潜力" and render.GROUP_CN["potential"] == "新品潜力"
    hints = render.section_hints(cfg)
    assert "更新的达到这些算新品潜力" in hints["surge"] and "增长商品 = 突然爆火 + 新品潜力 + 上升中" in hints["growth"]

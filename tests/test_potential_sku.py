"""潜力SKU（radar/detect/potential_sku.py，2026-11-17 领导要求“抓一抓潜力SKU”）：用户定的口径是“本期增长的产品里最容易后续爆火的”，
= 增长商品里近 7 天日均 ≥ 近 28 天 1.5 倍的，分一周翻倍（≥2 倍）/ 一周涨五成，各写回测里后续卖爆的比例。
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
            "stale": False, "relist": None, "fading": False, "tags": [], "age_days": 200, "rising": {"ratio": 1.5},
            "surge": None, "potential": None, "baseline_unknown": False, "recovered": None, "dormant": False,
            "title": f"Wood Nightstand {asin}", "brand": f"Brand{asin}", "node_cn": "床头柜", "node_name": "Nightstands",
            "price": 150.0, "rating": 4.6, "ratings": 150, "image": "", "fake": {"score": 0, "level": None, "signals": []},
            "monthly": [], "spark_start": "2026-10-03", "material": "实木", "features": [],
            "recent_avg7": 16.0, "last7_avg": 16.0, "recent_avg14": 13.0, "recent_avg28": 10.0, "prev_avg28": 6.0,
            "prev_in28": 28}
    return {**base, **kw}


def test_growth_items_accelerating_in_the_last_week(cfg):
    """潜力SKU = 增长商品（突然爆火、新品潜力、上升中）里近 7 天日均 ≥ 近 28 天日均 × 1.5；≥2 倍是一周翻倍，其余一周涨五成。
    用 last7_avg（所有商品同一个口径），不用 recent_avg7（爆火商品那里是爆发窗口的日均）。"""
    s = potential_sku.settings(cfg)
    assert potential_sku.classify(_item("A"), s) == {"tier": "一周涨五成", "kind": "上升中", "a7": 1.6, "a14": 1.3}
    assert potential_sku.classify(_item("B", last7_avg=20.0), s)["tier"] == "一周翻倍"
    assert potential_sku.classify(_item("C", last7_avg=14.9), s) is None  # 1.49 倍：和增长商品平均一样容易爆，不列
    assert potential_sku.classify(_item("D", label="surge", surge={"kind": "稳在高位"}, rising=None), s)["kind"] == "突然爆火"
    assert potential_sku.classify(_item("E", label="potential", rising=None), s)["kind"] == "新品潜力"
    assert potential_sku.classify(_item("F", label="hot"), s)["kind"] == "上升中"  # 持续热销里带上升中的也是增长商品
    window = _item("G", label="surge", surge={"kind": "稳在高位"}, rising=None, recent_avg7=30.0, last7_avg=12.0)
    assert potential_sku.classify(window, s) is None  # 爆发窗口日均 30，近 7 天其实只有 12（×1.2）
    assert potential_sku.why({**_item("B", last7_avg=20.0), "sku": potential_sku.classify(_item("B", last7_avg=20.0), s)}) == \
        "上升中；近 7 天日均是近 28 天的 ×2.00，最近一周翻倍了"


def test_only_growth_items_count(cfg):
    """不是增长商品的不收（观察里没有上升中的、持续热销里没有上升中的、评分偏低、异常）：最近一周再加速也不算；
    蓝海、数据陈旧、只有月度名单数据的也不进。"""
    s = potential_sku.settings(cfg)
    for kw in ({"rising": None}, {"label": "hot", "rising": None}, {"label": "low"}, {"label": "fake"},
               {"ocean": {"niche": "黑胶唱片"}}, {"stale": True}, {"daily": False}):
        assert potential_sku.classify(_item("A", last7_avg=25.0, **kw), s) is None, kw
    assert potential_sku.classify(_item("B", last7_avg=None), s) is None


def test_sections_order_by_tier_then_acceleration(cfg):
    """清单顺序：一周翻倍 → 一周涨五成，同档里按近 7 天 ÷ 近 28 天从高到低。"""
    items = [_item("M1", last7_avg=15.5), _item("T1", last7_avg=21.0), _item("M2", last7_avg=19.0), _item("T2", last7_avg=30.0),
             _item("X", last7_avg=12.0), _item("Y", last7_avg=25.0, rising=None)]
    assert potential_sku.mark(items, cfg) == 4
    sec = analyze.sections(items, 0)
    assert [i["asin"] for i in sec["sku"]] == ["T2", "T1", "M2", "M1"]
    c = sec["counts"]
    assert (c["sku"], c["sku_top"]) == (4, 2)
    assert potential_sku.record(items) == {"M1": ["一周涨五成", "上升中"], "T1": ["一周翻倍", "上升中"],
                                           "M2": ["一周涨五成", "上升中"], "T2": ["一周翻倍", "上升中"]}


def test_next_run_rechecks_last_runs_list_and_fetches_it_first(cfg):
    """上期的潜力SKU本期优先拉日数据（tracking.watchlist），“与上期对比”写出仍在清单 / 不再符合 / 没追踪各几个。"""
    prev = {"id": "2026-11-16", "labels": {"A": ["watch"], "B": ["surge"], "C": ["watch"]},
            "sku": {"A": ["一周涨五成", "上升中"], "B": ["一周翻倍", "突然爆火"], "C": ["一周涨五成", "新品潜力"],
                    "D": ["一周翻倍", "上升中"]}}
    state = {"runs": [prev], "asins": {}}
    assert tracking.watchlist(state, 1) == ["B", "A", "C", "D"]  # 爆火 / 潜力 / 异常在前，潜力SKU 在后
    current = {"A": ["watch"], "B": ["watch"], "C": ["watch"]}
    out = diffing.compare(prev, current, sku={"A": ["一周涨五成", "上升中"], "C": ["一周翻倍", "新品潜力"]})
    sc = out["sku_check"]
    assert sc == {"total": 4, "held": ["A", "C"], "lost": {"B": "一周翻倍"}, "gone": ["D"]}
    assert diffing.compare(None, current)["sku_check"] is None
    assert diffing.compare({"labels": {}}, current, sku={})["sku_check"] is None  # 旧的运行记录没有 sku
    html = render._changes(out, {})
    assert "上期潜力SKU 4 个：本期仍在清单 2 个，还在追踪但不再符合 1 个" in html
    assert "（上期一周翻倍）" in html and "本期没追踪 1 个" in html


def test_report_block_table_and_download(cfg):
    """第 6 张 KPI、正文前几个（每个子类目最多 per_node 个）、小窗表格：每行带筛选 / 排序用的 data-*，CSV 一行一个商品，
    点一行展开完整卡片；提示里的数字取自 config.yaml。"""
    items = [_item("M1"), _item("M2", node_cn="书架"), _item("T1", last7_avg=20.0, node_cn="电视柜"),
             _item("M3", node_cn="电视柜"), _item("M4", node_cn="床架", tags=["降价驱动"], label="potential", rising=None)]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    cur = {"shown": sec["sku"], "plain": [], "similar": {}}
    cfg["report"]["potential_sku"] = {"body_rows": 10, "per_node": 1}
    body = render._sku_section(cur, cfg)
    assert 'id="sku"' in body and 'data-pop="sku"' in body and "全部 5 个 · 表格可下载" in body
    assert body.count('class="sr"') == 4 and 'class="sc"' not in body  # 正文不展开卡片；电视柜只放 1 个
    assert "列出 5 个：一周翻倍 1 个、一周涨五成 4 个。" in body and "按最近一周加速排" in body
    pop = render._sku_pop(cur, render.sku_hint(cfg), 5, "2026-11-16")
    assert 'id="pop-sku"' in pop and "data-sku" in pop and 'data-file="潜力SKU-2026-11-16"' in pop
    assert pop.count('class="sc" hidden') == 5
    assert 'class="grp">一周翻倍·上升中<' in pop and 'class="grp">一周涨五成·新品潜力<' in pop
    head = json.loads(re.search(r'data-head="([^"]+)"', pop).group(1).replace("&quot;", '"'))
    assert head[:7] == ["ASIN", "亚马逊链接", "品牌", "标题", "子类目", "档", "类型"] and len(head) == len(render.SKU_CSV)
    assert head[11:13] == ["近7天÷近28天", "近14天÷近28天"]
    rows = [json.loads(m.replace("&quot;", '"')) for m in re.findall(r'data-csv="([^"]+)"', pop)]
    assert len(rows) == 5 and all(len(r) == len(head) for r in rows) and rows[0][0] == "T1"
    m4 = next(r for r in rows if r[0] == "M4")
    assert m4[5:7] == ["一周涨五成", "新品潜力"] and m4[8] == 300 and m4[9] == 45000
    assert m4[11:13] == [1.6, 1.3] and m4[17] == "降价驱动"
    assert 'data-seg="一周翻倍"' in pop and 'data-seg="一周涨五成"' in pop and 'data-a7="2.0"' in pop
    hint = render.sku_hint(cfg)
    assert "潜力SKU = 本期增长商品（突然爆火、新品潜力、上升中）里最近一周在加速的：近 7 天日均 ≥ 近 28 天的 1.5 倍" in hint
    assert "卖爆 = 第 2 个月的 30 天销量 ≥ 现在 2 倍且 ≥300 件" in hint
    assert "一周翻倍的 27% 后续卖爆、一周涨五成的 12%" in hint and "（增长商品平均 5.6%，全部商品 3.0%）" in hint
    for old in ("把握大", "待观察", "爆发苗头", "稳步在涨", "刚起势"):
        assert old not in hint and old not in pop and old not in body, old
    js = render.SKU_JS
    assert "window.skuTable" in js and "\\ufeff" in js and "/\\bsc\\b/" in js and "'-a7'" in js and "'一周翻倍','一周涨五成'" in js


def test_dingtalk_lists_potential_skus(cfg):
    items = [_item("M1"), _item("T1", last7_avg=20.0, tags=["降价驱动"])]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    ctx = {"title": "家具爆品雷达", "report_id": "2026-11-16", "as_of": "2026-11-15", "cfg": cfg,
           "sections": {**sec, "surge": [], "potential": [], "hot": [], "fake": [], "ocean": []},
           "diff": {"first": True}, "traits": {}}
    text = dingtalk.build_text(ctx)
    assert "🎯 潜力SKU 2**" in text
    assert ("**🎯 潜力SKU**（增长商品里最近一周加速的：一周翻倍 1，回测 27% 后续卖爆；一周涨五成 1，回测 12% 后续卖爆；"
            "表格在报告里可下载）") in text
    assert "一周翻倍·上升中：" in text and "一周涨五成·上升中：" in text and "（风险：降价驱动）" in text
    assert text.index("一周翻倍·上升中：") < text.index("一周涨五成·上升中：")
    assert dingtalk.build_text(ctx, sku_items=1).count("一周涨五成·上升中：") == 0


def test_old_potential_section_is_now_called_new_product_potential(cfg):
    """原“潜力”板块改名“新品潜力”：规则、商品、内部 key 都不变，只是名字和潜力SKU 分开（一个词不能有两种意思）。"""
    assert analyze.LABEL_CN["potential"] == "新品潜力" and render.GROUP_CN["potential"] == "新品潜力"
    hints = render.section_hints(cfg)
    assert "更新的达到这些算新品潜力" in hints["surge"] and "增长商品 = 突然爆火 + 新品潜力 + 上升中" in hints["growth"]

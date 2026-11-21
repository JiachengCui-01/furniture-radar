"""潜力SKU（radar/detect/potential_sku.py，2026-11-17 领导要求“抓一抓潜力SKU”）：用户定的口径是“本期增长的产品里最容易后续爆火的”，
= 增长商品里近 7 天日均 ≥ 近 28 天日均 1.5 倍的（2026-11-17 的判定，11-20 恢复），分一周翻倍（≥2 倍）/ 一周涨五成，各写回测里后续卖爆的比例。
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
    assert potential_sku.classify(_item("A"), s) == {"tier": "一周涨五成", "kind": "上升中", "a7": 1.6, "net": 1.6,
                                                     "a14": 1.3, "promo": None, "flags": []}
    assert potential_sku.classify(_item("B", last7_avg=20.0), s)["tier"] == "一周翻倍"
    assert potential_sku.classify(_item("C", last7_avg=14.9), s) is None  # 1.49 倍：和增长商品平均差不多，不列
    assert potential_sku.classify(_item("D", label="surge", surge={"kind": "稳在高位"}, rising=None), s)["kind"] == "突然爆火"
    assert potential_sku.classify(_item("E", label="potential", rising=None), s)["kind"] == "新品潜力"
    assert potential_sku.classify(_item("F", label="hot"), s)["kind"] == "上升中"  # 持续热销里带上升中的也是增长商品
    window = _item("G", label="surge", surge={"kind": "稳在高位"}, rising=None, recent_avg7=30.0, last7_avg=12.0)
    assert potential_sku.classify(window, s) is None  # 爆发窗口日均 30，近 7 天其实只有 12（×1.2）
    assert potential_sku.why({**_item("B", last7_avg=20.0), "sku": potential_sku.classify(_item("B", last7_avg=20.0), s)}) == \
        "上升中；近 7 天日均是近 28 天日均的 ×2.00，最近一周翻倍了"


def test_subcategory_wide_acceleration_is_not_taken_out(cfg):
    """2026-11-20 恢复成 11-17 的判定（用户要那版的数量）：不再扣子类目整体的加速，整类一起涨的那几周也按商品自己的倍数分档。"""
    s = potential_sku.settings(cfg)
    peers = [_item(f"P{i}", node="tv", last7_avg=12.0, rising=None) for i in range(10)]  # 整体 ×1.2
    star = _item("S", node="tv", last7_avg=21.0)
    assert potential_sku.mark(peers + [star], cfg) == 1
    assert (star["sku"]["tier"], star["sku"]["a7"], star["sku"]["net"]) == ("一周翻倍", 2.1, 2.1) and "pace" not in star["sku"]
    assert potential_sku.classify(_item("M", node="tv", last7_avg=16.0), s)["tier"] == "一周涨五成"
    assert not hasattr(potential_sku, "node_pace")
    assert "扣子类目后" not in render._sku_row(star, 0, cfg)


def test_only_growth_items_count(cfg):
    """不是增长商品的不收（观察里没有上升中的、持续热销里没有上升中的、评分偏低、异常）：最近一周再加速也不算；
    数据陈旧、只有月度名单数据的也不进。蓝海产品 2026-11-20 起照样判（用户要），条件一样，清单上写明“蓝海产品”。"""
    s = potential_sku.settings(cfg)
    for kw in ({"rising": None}, {"label": "hot", "rising": None}, {"label": "low"}, {"label": "fake"},
               {"stale": True}, {"daily": False}):
        assert potential_sku.classify(_item("A", last7_avg=25.0, **kw), s) is None, kw
    assert potential_sku.classify(_item("B", last7_avg=None), s) is None
    ocean = potential_sku.classify(_item("O", last7_avg=25.0, ocean={"niche": "黑胶唱片"}), s)
    assert ocean["tier"] == "一周翻倍" and ocean["ocean"] is True
    assert potential_sku.label_of(ocean) == "一周翻倍·上升中·蓝海产品"
    assert "ocean" not in potential_sku.classify(_item("P", last7_avg=25.0), s)
    # 条件一样：不是增长商品的蓝海产品不收；带“月度增长尚未验证”的照收（风险里写明）
    assert potential_sku.classify(_item("Q", last7_avg=25.0, rising=None, ocean={"niche": "台球"}), s) is None
    sea = potential_sku.classify(_item("R", last7_avg=16.0, ocean={"niche": "台球"}, tags=["月度增长尚未验证（完整月份不足）"]), s)
    assert sea["tier"] == "一周涨五成" and sea["flags"] == ["月度增长尚未验证"]


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
    """第 6 张 KPI、正文前几个（每个子类目最多 per_node 个）、小窗表格：每行带筛选 / 排序用的 data-*，下载的 Excel 表一行一个商品（列和 SKU_COLS 一一对应），
    点一行展开完整卡片；提示里的数字取自 config.yaml。"""
    items = [_item("M1"), _item("M2", node_cn="书架"), _item("T1", last7_avg=20.0, node_cn="电视柜"),
             _item("M3", node_cn="电视柜"), _item("M4", node_cn="床架", tags=["断货恢复"], label="potential", rising=None)]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    cur = {"shown": sec["sku"], "plain": [], "similar": {}}
    cfg["report"]["potential_sku"] = {"body_rows": 10, "per_node": 1}
    body = render._sku_section(cur, cfg)
    assert 'id="sku"' in body and 'data-pop="sku"' in body and "全部 5 个 · 表格可下载" in body
    assert body.count('class="sr"') == 4 and 'class="sc"' not in body  # 正文不展开卡片；电视柜只放 1 个
    assert "列出 5 个：一周翻倍 1 个、一周涨五成 4 个。" in body and "按最近一周加速排" in body
    pop = render._sku_pop(cur, render.sku_hint(cfg), 5, "2026-11-16", None, cfg)
    assert 'id="pop-sku"' in pop and "data-sku" in pop and 'data-file="潜力SKU-2026-11-16"' in pop
    assert pop.count('class="sc" hidden') == 5
    assert 'class="grp">一周翻倍·上升中<' in pop and 'class="grp">一周涨五成·新品潜力<' in pop
    cols = json.loads(re.search(r'data-cols="([^"]+)"', pop).group(1).replace("&quot;", '"'))
    assert [c[0] for c in cols[:7]] == ["主图", "商品", "品牌", "ASIN", "子类目", "档", "类型"] and len(cols) == len(render.SKU_COLS)
    assert [c[0] for c in cols[11:14]] == ["近7天÷近28天", "同样加速回测卖爆", "近14天÷近28天"]
    assert [c[2] for c in cols[11:14]] == ["bar", "text", "ratio"] and "扣子类目整体后" not in [c[0] for c in cols]
    assert all(len(c) == 4 and isinstance(c[1], int) for c in cols)
    assert 'data-title="潜力SKU · 2026-11-16"' in pop
    rows = [json.loads(m.replace("&quot;", '"')) for m in re.findall(r'data-xl="([^"]+)"', pop)]
    assert len(rows) == 5 and all(len(r) == len(cols) for r in rows) and rows[0][3][0] == "T1"
    m4 = next(r for r in rows if r[3][0] == "M4")
    assert m4[1][1] == m4[3][1] == "https://www.amazon.com/dp/M4"  # 商品名、ASIN 都链到商品页
    assert m4[5:7] == ["一周涨五成", "新品潜力"] and m4[8] == 300 and m4[9] == 45000
    assert m4[11:14] == [1.6, "7.4%~9.3%", 1.3] and m4[18] == "断货恢复"
    assert 'data-seg="一周翻倍"' in pop and 'data-seg="一周涨五成"' in pop and 'data-a7="2.0"' in pop
    # 2026-11-20 用户要简要明确：一行一项（怎么收集 / 怎么判断 / 回测 / 不判的），细的回测数在“回测卖爆”一列和 README
    hint = render.sku_hint(cfg)
    assert hint.split("\n") == [
        "怎么收集：每期按卖家精灵全市场名单挑可能在涨的商品拉日数据，在其中的增长商品（突然爆火、新品潜力、上升中，蓝海产品也算）里判断。",
        "怎么判断：加速倍数 = 近 7 天日均 ÷ 近 28 天日均（都去掉大促日，断货的日子也算）。≥2 倍是一周翻倍；1.5~2 倍是一周涨五成。",
        "回测：卖爆 = 之后第 2 个月的 30 天销量 ≥ 现在的 2 倍且 ≥300 件（一周翻倍的保持这周的速度就够）。2025-11~2026-08 每周倒推："
        "一周翻倍 18%~31% 卖爆，一周涨五成 7.5%~12%，增长商品平均 5.3%；加速越猛越容易卖爆，每行见“回测卖爆”一列。"
        "老品换链接单算（一周翻倍 43%、一周涨五成 29%）。样本偏乐观（只含 2026-10-23 还在名单上的商品），实际会低一些。",
        "不判的：数据陈旧、只有月度数据的；商品照样留在原来的板块里（蓝海产品留在蓝海产品里）。"]
    assert len(hint) < 500
    # 本期留了排名改善名额的：怎么收集里写上
    assert "拉日数据（另留 79 个名额给近 7 天排名改善 ≥20% 的），在其中" in render.sku_hint(cfg, [79, 64])
    assert render.section_hints(cfg, rank=[79, 64])["sku"] == render.sku_hint(cfg, [79, 64])
    # 小窗：开头加粗、一行一项，最后一行是本期的判定 / 列出个数
    pop2 = render._sku_pop(cur, hint, 5, "2026-11-16", None, cfg)
    assert '<p class="hint lines"><b>怎么收集：</b>每期按卖家精灵' in pop2 and "\n<b>本期：</b>共 5 个。</p>" in pop2
    assert render.hint_lines("说明一句话，没有短开头：这里") == "说明一句话，没有短开头：这里"
    for old in ("把握大", "待观察", "爆发苗头", "稳步在涨", "刚起势"):
        assert old not in hint and old not in pop and old not in body, old
    js = render.SKU_JS
    assert "window.skuTable" in js and "/\\bsc\\b/" in js and "'-a7'" in js and "'一周翻倍','一周涨五成'" in js
    assert ".xlsx'" in js and "twoCellAnchor" in js and "dataBar" in js and "'data-xl'" in js and "text/csv" not in js


def test_dingtalk_lists_potential_skus(cfg):
    items = [_item("M1"), _item("T1", last7_avg=20.0, tags=["降价驱动"])]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    ctx = {"title": "家具爆品雷达", "report_id": "2026-11-16", "as_of": "2026-11-15", "cfg": cfg,
           "sections": {**sec, "surge": [], "potential": [], "hot": [], "fake": [], "ocean": []},
           "diff": {"first": True}, "traits": {}}
    text = dingtalk.build_text(ctx)
    assert "🎯 潜力SKU 2**" in text
    assert ("**🎯 潜力SKU**（增长商品里最近一周加速的：一周翻倍 1，回测 18%~31% 后续卖爆；"
            "一周涨五成 1，回测 7.5%~12% 后续卖爆；表格在报告里可下载）") in text
    assert "一周翻倍·上升中：" in text and "一周涨五成·上升中：" in text and "（风险：降价驱动）" in text
    assert text.index("一周翻倍·上升中：") < text.index("一周涨五成·上升中：")
    assert dingtalk.build_text(ctx, sku_items=1).count("一周涨五成·上升中：") == 0


def test_old_potential_section_is_now_called_new_product_potential(cfg):
    """原“潜力”板块改名“新品潜力”：规则、商品、内部 key 都不变，只是名字和潜力SKU 分开（一个词不能有两种意思）。"""
    assert analyze.LABEL_CN["potential"] == "新品潜力" and render.GROUP_CN["potential"] == "新品潜力"
    hints = render.section_hints(cfg)
    assert "更新的达到这些算新品潜力" in hints["surge"] and "增长商品 = 突然爆火 + 新品潜力 + 上升中" in hints["growth"]


def test_promo_days_in_last_week_are_a_risk_not_one_day_spikes(cfg):
    """2026-11-18 复核：近 7 天里有大促日的，加速倍数是去掉大促日算的（剩下的是大促前后几天），回测里一周翻倍的后续卖爆 18% 对 36%：
    打风险标签“近7天含大促日”（放最前），不剔除。近 7 天靠一两天冲出来的回测里不比其他差，不打标签。"""
    from datetime import date, timedelta
    s = potential_sku.settings(cfg)
    bf = {date(2026, 11, 28) + timedelta(days=i) for i in range(4)}  # 11-28 ~ 12-01
    assert potential_sku.promo_days("2026-11-30", bf) == ["2026-11-28", "2026-11-29", "2026-11-30"]
    assert potential_sku.promo_days("2026-12-07", bf) == ["2026-12-01"]  # 往前第 7 天（含当天）
    assert potential_sku.promo_days("2026-12-08", bf) == [] and potential_sku.promo_days(None, bf) == []
    star = _item("T1", last7_avg=20.0, as_of="2026-11-30", tags=["降价驱动"])
    calm = _item("T2", last7_avg=20.0, as_of="2026-12-08")
    assert potential_sku.mark([star, calm], cfg, bf) == 2
    assert star["sku"]["promo"] == ["2026-11-28", "2026-11-29", "2026-11-30"] and calm["sku"]["promo"] is None
    assert potential_sku.classify(star, s)["promo"] is None  # 没传大促日
    assert render.sku_risks(star) == ["近7天含大促日", "降价驱动"] and render.sku_risks(calm) == []
    note = potential_sku.promo_note(star["sku"], cfg)
    assert note == ("近 7 天里有大促日（11-28~11-30）：加速倍数是去掉这几天算的，剩下的是大促前后几天；"
                    "回测里一周翻倍的这类后续卖爆 10%，近 7 天没有大促日的 27%")
    mid = _item("M1", as_of="2026-12-01")
    potential_sku.mark([mid], cfg, bf)
    assert potential_sku.promo_note(mid["sku"], cfg).endswith("回测里一周涨五成的这类后续卖爆 7%，近 7 天没有大促日的 10%")
    row = render._sku_row(star, 0, cfg)
    assert f'<small class="rk" data-tip="{note}">风险：近7天含大促日、降价驱动</small>' in row
    assert json.loads(re.search(r'data-xl="([^"]+)"', row).group(1).replace("&quot;", '"'))[18] == "近7天含大促日、降价驱动"
    assert 'class="rk"' not in render._sku_row(calm, 1, cfg)  # 没有风险的不写这一行
    sec = analyze.sections([star, calm], 0)
    ctx = {"title": "家具爆品雷达", "report_id": "2026-11-30", "as_of": "2026-11-30", "cfg": cfg,
           "sections": {**sec, "surge": [], "potential": [], "hot": [], "fake": [], "ocean": []},
           "diff": {"first": True}, "traits": {}}
    assert "（风险：近7天含大促日、降价驱动）" in dingtalk.build_text(ctx)
    gloss = render._method(cfg)
    assert "近7天含大促日" in gloss and "实盘" in gloss


def _series(start, values):
    return {"start": start, "bsr": [1000 if v is not None else None for v in values], "sales": list(values),
            "price": [150.0] * len(values)}


def test_live_hit_rate_is_tracked_two_months_after_each_pick(cfg):
    """实盘命中率：每期记下判定的潜力SKU（判定时近 30 天销量 = 近 28 天日均 × 30），第 2 个月（判定后第 27~56 天这 30 天）
    结束再过 14 天（最近几天的数改完）用那以后拉到的日数据看卖爆没有，口径同回测；同一个商品还在等结果时不另记，
    重新生成本期时按现在的规则重记；到期了还没出结果的下期必看（tracking.watchlist）。报告小窗、钉钉写“卖爆个数/出结果个数”。"""
    from datetime import date
    items = [_item("T1", last7_avg=20.0, as_of="2026-11-15"), _item("M1", as_of="2026-11-15"),
             _item("M2", as_of="2026-11-15"), _item("X", last7_avg=12.0, as_of="2026-11-15")]
    potential_sku.mark(items, cfg)
    state = {"asins": {}, "runs": []}
    live = potential_sku.track(state, items, "2026-11-16", cfg, date(2026, 11, 15))
    picks = state["sku_live"]["picks"]
    assert [p["asin"] for p in picks] == ["T1", "M1", "M2"] and state["sku_live"]["since"] == "2026-11-16"
    assert picks[0] == {"asin": "T1", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍", "kind": "上升中", "net": 2.0,
                        "u": 300.0, "title": "Wood Nightstand T1", "node": "床头柜", "flags": []}
    assert (live["total"], live["waiting"], live["next_due"], live["done"]) == \
        (3, 3, "2027-01-24", {"一周翻倍": [0, 0], "一周涨五成": [0, 0]})
    text = render.sku_live_text(live, cfg)
    assert text == "实盘跟踪：2026-11-16 起每期记下判定的潜力SKU，第 2 个月结束再过 14 天按回测的标准看卖爆没有。已记 3 个，还没有到期的，最早 2027-01-24 出结果。"
    assert text.endswith("已记 3 个，还没有到期的，最早 2027-01-24 出结果。")
    # 重新生成本期：本期记的先去掉再按现在的规则重记（M2 不再算）
    del items[2]["sku"]
    potential_sku.track(state, items, "2026-11-16", cfg, date(2026, 11, 15))
    assert [p["asin"] for p in state["sku_live"]["picks"]] == ["T1", "M1"]
    # 下一期 M1 还在清单：还在等结果，不另记（按第一次入选那天算）
    potential_sku.track(state, [items[1]], "2026-11-19", cfg, date(2026, 11, 18))
    assert len(state["sku_live"]["picks"]) == 2
    # 到期（第 2 个月结束 2027-01-10 再过 14 天）还没出结果的：下期必看
    assert potential_sku.due(state, date(2027, 1, 23)) == [] and potential_sku.due(state, date(2027, 1, 24)) == ["T1", "M1"]
    assert tracking.watchlist(state, 1, date(2027, 1, 26)) == ["T1", "M1"]
    assert potential_sku.waiting(state) == {"T1", "M1"}  # 很久没进池也不从状态里整条删（pipeline 的清理）
    # 2027-01-26 那期拉到了日数据：第 2 个月 = 2026-12-12 ~ 2027-01-10
    days = (date(2027, 1, 26) - date(2026, 11, 1)).days + 1
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    fetched = "2027-01-26T04:41:00+00:00"
    state["asins"]["T1"] = {"series": _series("2026-11-01", [10] * lead + [25] * 30 + [5] * 16),  # 750 件 ≥ 300 × 2：卖爆
                            "fetched_at": fetched}
    state["asins"]["M1"] = {"series": _series("2026-11-01", [10] * lead + [12] * 30 + [5] * 16),  # 360 件：没有
                            "fetched_at": fetched}
    assert len(state["asins"]["T1"]["series"]["sales"]) == days
    live = potential_sku.track(state, [{**items[0], "as_of": "2027-01-26"}], "2027-01-26", cfg, date(2027, 1, 26))
    done = {p["asin"]: p for p in state["sku_live"]["picks"] if "f" in p}
    assert (done["T1"]["f"], done["T1"]["boom"], done["M1"]["f"], done["M1"]["boom"]) == (750.0, True, 360.0, False)
    assert done["T1"]["done"] == "2027-01-26"
    assert live["done"] == {"一周翻倍": [1, 1], "一周涨五成": [1, 0]} and live["overdue"] == 0
    assert [p["asin"] for p in state["sku_live"]["picks"] if "f" not in p] == ["T1"]  # 出了结果以后再入选：另记一次
    assert potential_sku.due(state, date(2027, 1, 26)) == []
    text = render.sku_live_text(live, cfg)
    assert ("已出结果 2 个，卖爆的一周翻倍 1/1（100%，样本少，回测 18%~31%）、一周涨五成 0/1（0%，样本少，回测 7.5%~12%）。"
            "还在等 1 个（下一批 2027-04-06 起）。样本少 = 出结果不到 30 个：比例上下差十几个百分点都正常，只供参考。") in text
    table = render._sku_live_rows(live)
    assert "出了结果的 2 个" in table and table.count("<tr>") == 3 and ">卖爆</td>" in table and ">没有</td>" in table
    pop = render._sku_pop({"shown": [], "plain": [], "similar": {}}, "提示", 0, "2027-01-26", live, cfg)
    assert render.esc(text) in pop and "出了结果的 2 个" in pop
    sec = analyze.sections([items[0]], 0)
    ctx = {"title": "家具爆品雷达", "report_id": "2027-01-26", "as_of": "2027-01-26", "cfg": cfg, "sku_live": live,
           "sections": {**sec, "surge": [], "potential": [], "hot": [], "fake": [], "ocean": []},
           "diff": {"first": True}, "traits": {}}
    assert "；实盘已出结果 2 个，卖爆的一周翻倍 1/1（样本少）、一周涨五成 0/1（样本少）；表格在报告里可下载）" in dingtalk.build_text(ctx)


def test_live_outcome_needs_the_whole_second_month(cfg):
    """第 2 个月的数要到判定后第 56 天才有：没到的接着等；到了但有数的不到 20 天的算“数据不全”，不算进比例；
    到期了这期没拉到它第 56 天日数据的写“到期了还没拉到”。"""
    from datetime import date
    s = potential_sku.settings(cfg)
    day = date(2026, 11, 15)
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    assert potential_sku.outcome(_series("2026-11-01", [10] * lead + [25] * 29), day, 300, s) is None  # 只到 01-09
    assert potential_sku.outcome(None, day, 300, s) is None
    gaps = [10] * lead + [25] * 19 + [None] * 11 + [5]
    assert potential_sku.outcome(_series("2026-11-01", gaps), day, 300, s) == {"f": None, "boom": False}
    assert potential_sku.outcome(_series("2026-11-01", [10] * lead + [9] * 30), day, 100, s) == {"f": 270.0, "boom": False}  # 不到 300 件
    fetched = "2027-01-26T04:41:00+00:00"
    state = {"asins": {"A": {"series": _series("2026-11-01", gaps), "fetched_at": fetched}},
             "sku_live": {"since": "2026-11-16", "picks": [
                 {"asin": "A", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍", "kind": "上升中", "net": 2.1, "u": 300.0},
                 {"asin": "B", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周涨五成", "kind": "上升中", "net": 1.6, "u": 200.0}]}}
    live = potential_sku.track(state, [], "2027-01-26", cfg, date(2027, 1, 26))
    assert (live["short"], live["overdue"], live["waiting"], live["stop"], live["done"]) == \
        (1, 1, 1, 0, {"一周翻倍": [0, 0], "一周涨五成": [0, 0]})
    # 有到期的（数据不全、还没拉到）就不能再写“还没有到期的”
    text = render.sku_live_text(live, cfg)
    assert "还没有到期" not in text and "已出结果 0 个" in text
    assert "到期了还没拉到第 56 天日数据的 1 个（最多再等 28 天）" in text and "第 2 个月有数的不到 20 天没算的 1 个" in text
    assert render.sku_live_text({"total": 0}, cfg) == "" and render.sku_live_text(None, cfg) == ""


def test_live_outcome_waits_for_revisions_and_gives_up_on_stopped_data(cfg):
    """边界（2026-11-19 复核）：
    ① 第 2 个月最后几天的数卖家精灵还会改（各期测到改最近 5~9 天），回测用的是改完的：到期 = 第 2 个月结束再过 14 天，
       看的是这个商品的日数据什么时候拉的（fetched_at），满 56 天但拉得早的接着等；
    ② 断货、下架后日数据停在最后在售那天：第 56 天以后才停的照算（回测里算），没到第 56 天就停的、一直没拉到的，
       到期 28 天后不再等，记成停更、不算进比例，也不再进必看、状态里可以删；以前会一直等、每期多花一次调用。"""
    from datetime import date
    s = potential_sku.settings(cfg)
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    pick = lambda asin: {"asin": asin, "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍",  # noqa: E731
                         "kind": "上升中", "net": 2.1, "u": 300.0}
    full = _series("2026-11-01", [10] * lead + [25] * 30 + [5] * 2)  # 第 2 个月 750 件，日数据到 01-12
    assert potential_sku.outcome(full, date(2026, 11, 15), 300, s, settled=False) is None
    state = {"asins": {"EARLY": {"series": full, "fetched_at": "2027-01-12T04:00:00+00:00"},  # 满 56 天但拉得早：数还会改
                       "LATE": {"series": _series("2026-11-01", [10] * lead + [25] * 30 + [5] * 4),  # 01-14 起断货，01-26 拉的
                                "fetched_at": "2027-01-26T04:00:00+00:00"},
                       "STOP": {"series": _series("2026-11-01", [10] * lead + [25] * 10),  # 12-21 起断货，第 2 个月不全
                                "fetched_at": "2027-01-26T04:00:00+00:00"},
                       "OLD": {"series": full}},  # 旧状态没记拉取时间：看日数据最后一天（01-12，不到 01-24）
             "sku_live": {"since": "2026-11-16", "picks": [pick(a) for a in ("EARLY", "LATE", "STOP", "OLD", "MISS")]}}
    live = potential_sku.track(state, [], "2027-01-26", cfg, date(2027, 1, 26))
    res = {p["asin"]: (p.get("f"), p.get("boom")) for p in state["sku_live"]["picks"] if "f" in p}
    assert res == {"LATE": (750.0, True)}
    assert (live["waiting"], live["overdue"], live["stop"]) == (4, 4, 0)
    assert potential_sku.due(state, date(2027, 1, 26)) == ["EARLY", "STOP", "OLD", "MISS"]
    # 到期后 27 天还在等；28 天不再等
    live = potential_sku.track(state, [], "2027-02-20", cfg, date(2027, 2, 20))
    assert (live["waiting"], live["stop"]) == (4, 0)
    live = potential_sku.track(state, [], "2027-02-21", cfg, date(2027, 2, 21))
    assert (live["waiting"], live["stop"], live["short"], live["overdue"]) == (0, 4, 0, 0)
    assert potential_sku.due(state, date(2027, 2, 21)) == [] and potential_sku.waiting(state) == set()
    assert all(p["done"] == "2027-02-21" for p in state["sku_live"]["picks"] if p.get("stop"))
    assert live["done"]["一周翻倍"] == [1, 1] and len(live["rows"]) == 1  # 不再等的不算进比例、不进明细
    text = render.sku_live_text(live, cfg)
    assert ("到期 28 天还没有、不再等的 4 个（断货、下架后卖家精灵的日数据停在最后在售那天，或一直没拉到；不算进比例）") in text
    # 重新生成同一期：已经记成停更的不再改
    potential_sku.track(state, [], "2027-02-21", cfg, date(2027, 2, 21))
    assert sum(1 for p in state["sku_live"]["picks"] if p.get("stop")) == 4


def test_tagged_items_are_kept_and_caveats_are_written(cfg):
    """2026-11-20 恢复成 11-17 的判定：带“月度增长尚未验证”“降价驱动”的两档都照收（11-19~11-20 一周涨五成里不收过）。
    “月度增长尚未验证”写进风险、按档写回测数（一周翻倍 8% 对 25%、一周涨五成 6% 对 10%），降价驱动是红色标签照写；
    老品换链接（新链接还在起量，基数低）照收，回测数单算、写进风险。"""
    s = potential_sku.settings(cfg)
    unver = ["对比期只有 5 天在卖", "月度增长尚未验证（完整月份不足）"]
    m1 = _item("M1", label="potential", rising=None, tags=unver)
    m1["sku"] = potential_sku.classify(m1, s)
    assert m1["sku"]["tier"] == "一周涨五成" and m1["sku"]["flags"] == ["月度增长尚未验证"]
    m2 = _item("M2", tags=["仍在上涨", "降价驱动"])
    m2["sku"] = potential_sku.classify(m2, s)
    assert m2["sku"]["tier"] == "一周涨五成" and m2["sku"]["flags"] == [] and render.sku_risks(m2) == ["降价驱动"]
    top = _item("T1", label="potential", rising=None, last7_avg=21.0, tags=unver)
    top["sku"] = potential_sku.classify(top, s)
    assert top["sku"]["tier"] == "一周翻倍" and top["sku"]["flags"] == ["月度增长尚未验证"]
    relist = _item("R1", relist={"old": "B0OLD"}, tags=["老品换链接（…）"])
    relist["sku"] = potential_sku.classify(relist, s)
    assert relist["sku"]["tier"] == "一周涨五成" and relist["sku"]["flags"] == ["老品换链接"]
    assert render.sku_risks(top) == ["月度增长尚未验证"] and render.sku_risks(relist) == ["老品换链接"]
    assert potential_sku.risk_notes(top["sku"], cfg) == [
        "月度增长尚未验证：新品完整月份还不够，看不出月度在涨；回测里一周翻倍的这类后续卖爆 8%，其他 25%"]
    assert potential_sku.risk_notes(m1["sku"], cfg) == [
        "月度增长尚未验证：新品完整月份还不够，看不出月度在涨；回测里一周涨五成的这类后续卖爆 6%，其他 10%"]
    assert potential_sku.risk_notes(relist["sku"], cfg) == [
        "老品换链接：新链接还在起量、基数低，后续卖爆多半是回到老链接原来的量；"
        "回测里这类一周翻倍的 43%、一周涨五成的 29% 后续卖爆，不算进两档的比例"]
    row = render._sku_row(top, 0, cfg)
    assert '<small class="rk" data-tip="月度增长尚未验证：新品完整月份还不够' in row and ">风险：月度增长尚未验证</small>" in row
    gloss = render._method(cfg)
    assert "接近不列的" not in gloss and "近 7 天日均 ÷ 近 28 天日均（都去掉大促日，断货的日子也算）" in gloss


def test_base_is_the_28_day_average_with_stockout_days(cfg):
    """2026-11-20 恢复成 11-17 的判定：加速倍数和卖爆的基数是近 28 天日均（analyze 的 recent_avg28，断货的日子也算进去，
    同表格“近30天”那一列），11-19~11-20 按在售日算过。"""
    s = potential_sku.settings(cfg)
    back = _item("B", last7_avg=20.0, recent_avg28=10.0, as_of="2026-11-15")  # 近 28 天里断货了一段：按日历算日均 10
    back["sku"] = potential_sku.classify(back, s)
    assert back["sku"]["tier"] == "一周翻倍" and back["sku"]["a7"] == 2.0 and potential_sku.base_avg(back) == 10.0
    assert potential_sku.base_avg(_item("Y", recent_avg28=None)) is None and potential_sku.classify(_item("Y", recent_avg28=None), s) is None
    from datetime import date
    state = {"asins": {}}
    potential_sku.track(state, [back], "2026-11-16", cfg, date(2026, 11, 15))
    assert state["sku_live"]["picks"][0]["u"] == 300.0  # 近 28 天日均 10 × 30


def test_live_second_month_leaves_out_promo_days(cfg):
    """实盘的第 2 个月去掉大促日（判定时的近 28 天也去掉了，前后同一个口径）：大促那几天卖得多，不能算成卖爆。"""
    from datetime import date, timedelta
    s = potential_sku.settings(cfg)
    day = date(2026, 11, 15)
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    sales = [10] * lead + [18] * 30 + [5]  # 第 2 个月 540 件：不到 300 × 2
    sales[lead + 10: lead + 14] = [200] * 4  # 其中 4 天大促（12-22 ~ 12-25）卖了 800 件
    promo = {date(2026, 12, 12) + timedelta(days=10 + i) for i in range(4)}
    with_promo = potential_sku.outcome(_series("2026-11-01", sales), day, 300, s)
    without = potential_sku.outcome(_series("2026-11-01", sales), day, 300, s, promo)
    assert with_promo["boom"] and without == {"f": 540.0, "boom": False}
    state = {"asins": {"A": {"series": _series("2026-11-01", sales), "fetched_at": "2027-01-26T04:41:00+00:00"}},
             "sku_live": {"since": "2026-11-16", "picks": [
                 {"asin": "A", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍", "kind": "上升中", "net": 2.1, "u": 300.0}]}}
    live = potential_sku.track(state, [], "2027-01-26", cfg, date(2027, 1, 26), promo)
    assert live["done"]["一周翻倍"] == [1, 0]


def test_live_relist_picks_are_counted_apart(cfg):
    """老品换链接的回测数单算、不进两档的比例，实盘也一样：两档的“卖爆个数/出结果个数”不含它，另写一句。"""
    from datetime import date
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    boom = _series("2026-11-01", [10] * lead + [25] * 30 + [5, 5])
    fetched = "2027-01-26T04:41:00+00:00"
    state = {"asins": {"R": {"series": boom, "fetched_at": fetched}, "T": {"series": boom, "fetched_at": fetched}},
             "sku_live": {"since": "2026-11-16", "picks": [
                 {"asin": "R", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周涨五成", "kind": "突然爆火", "net": 1.9, "u": 300.0,
                  "flags": ["老品换链接"]},
                 {"asin": "T", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍", "kind": "上升中", "net": 2.1, "u": 300.0}]}}
    live = potential_sku.track(state, [], "2027-01-26", cfg, date(2027, 1, 26))
    assert live["done"] == {"一周翻倍": [1, 1], "一周涨五成": [0, 0]} and live["relist"] == [1, 1]
    assert len(live["rows"]) == 2
    text = render.sku_live_text(live, cfg)
    assert ("已出结果 1 个，卖爆的一周翻倍 1/1（100%，样本少，回测 18%~31%）、一周涨五成 0/0（回测 7.5%~12%）；"
            "老品换链接的单算，1/1（100%，样本少）。") in text
    assert "老品换链接（单算）" in render._sku_live_rows(live)


def test_each_row_shows_the_backtest_rate_of_its_own_acceleration_band(cfg):
    """一档一个数盖住了档里的差别（×1.5~1.75 8.3%、×2.5 以上 37%）：表格“回测卖爆”一列写同一段加速倍数的后续卖爆，
    前段~后段；老品换链接的写单算的比例；还符合同一档里分得出的（上架一年以上、量大、评论多，前后两段都对得上）的括号里另写。"""
    s = potential_sku.settings(cfg)
    cases = {"A": 15.5, "B": 18.0, "C": 21.0, "D": 26.0}  # 近 7 天日均，近 28 天 10
    texts = {asin: render.sku_band_text(potential_sku.classify(_item(asin, last7_avg=l7), s), cfg) for asin, l7 in cases.items()}
    assert texts == {"A": "7.4%~9.3%", "B": "7.6%~18%", "C": "15%~24%", "D": "27%~50%"}
    assert potential_sku.band({"net": 1.49}, cfg) is None and potential_sku.band({"net": 1.5}, cfg) == (0.0744, 0.0928)
    assert potential_sku.band({"net": 1.75}, cfg) == (0.0758, 0.1818)
    relist = potential_sku.classify(_item("R", relist={"old": "B0OLD"}), s)
    assert render.sku_band_text(relist, cfg) == "单算 29%"
    item = _item("C", last7_avg=21.0)
    item["sku"] = potential_sku.classify(item, s)
    assert '<td class="n bt">15%~24%</td>' in render._sku_row(item, 0, cfg)
    big = _item("G", last7_avg=50.0, recent_avg28=25.0, ratings=800)  # 近 30 天 750 件、评论 800 条的一周翻倍（×2.0）
    big["sku"] = potential_sku.classify(big, s)
    assert render.sku_band_text(big["sku"], cfg, big) == "15%~24%（近30天≥600件的 15%；评论500条以上的 41%）"
    old = _item("O", last7_avg=18.0, age_days=500)
    old["sku"] = potential_sku.classify(old, s)
    assert render.sku_band_text(old["sku"], cfg, old) == "7.6%~18%（上架一年以上的 7.8%）"
    assert render.sku_band_text(old["sku"], cfg) == "7.6%~18%"
    assert render.sku_band_text({**relist, "tier": "一周涨五成"}, cfg, _item("R", age_days=400)) == "单算 29%（上架一年以上的 7.8%）"
    hint = render.sku_hint(cfg)  # 分段、同一档里分得出的比例只在“回测卖爆”一列写
    assert "加速越猛越容易卖爆，每行见“回测卖爆”一列。" in hint and "×1.5~1.75" not in hint
    assert render.SKU_HEAD[6] == "回测卖爆" and len(render.SKU_HEAD) == 9
    assert "回测卖爆" in render._method(cfg)


def test_tier_and_band_follow_the_two_decimal_value_shown(cfg):
    """边界（2026-11-19 复核，恢复 11-17 的判定后照留）：按显示的两位小数分档、对段：×1.996 显示 ×2.00 算一周翻倍、
    对 ×2~2.5 那段；×1.4952 显示 ×1.50 算一周涨五成；×1.494 显示 ×1.49 不收。"""
    s = potential_sku.settings(cfg)
    edge = potential_sku.classify(_item("E", last7_avg=19.96), s)  # 1.996 → ×2.00
    assert (edge["a7"], edge["net"], edge["tier"]) == (2.0, 2.0, "一周翻倍") and render.sku_band_text(edge, cfg) == "15%~24%"
    low = potential_sku.classify(_item("L", last7_avg=14.952), s)  # 1.4952 → ×1.50
    assert (low["net"], low["tier"]) == (1.5, "一周涨五成") and render.sku_band_text(low, cfg) == "7.4%~9.3%"
    assert potential_sku.classify(_item("N", last7_avg=14.94), s) is None  # 1.494 → ×1.49
    assert potential_sku.classify(_item("F", last7_avg=13.952), s) is None  # 1.3952 → ×1.40：11-20 有几个小时收过，现在不收
    mid = potential_sku.classify(_item("M", last7_avg=17.496), s)  # 1.7496 → ×1.75
    assert mid["net"] == 1.75 and render.sku_band_text(mid, cfg) == "7.6%~18%"


def test_old_mid_name_in_saved_state_reads_as_the_new_tier(cfg):
    """2026-11-20 有几个小时线在 ×1.4、叫一周涨四成，又恢复成 ×1.5 的一周涨五成。状态里存的是名字：
    实盘记录在 track 里换成现在的名字存回去；没经过 track 直接读的也一样；上期的潜力SKU 复查时写现在的名字；
    提示、名词说明里不出现“一周涨四成”。"""
    from datetime import date
    assert potential_sku.tier_name("一周涨四成") == "一周涨五成"
    assert potential_sku.tier_name("一周翻倍") == "一周翻倍" and potential_sku.tier_name(None) is None
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    old = {"asin": "M", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周涨四成", "kind": "上升中", "net": 1.6, "u": 300.0}
    state = {"asins": {"M": {"series": _series("2026-11-01", [10] * lead + [25] * 30 + [5] * 16),
                             "fetched_at": "2027-01-26T04:41:00+00:00"}},
             "sku_live": {"since": "2026-11-16", "picks": [dict(old)]}}
    live = potential_sku.track(state, [], "2027-01-26", cfg, date(2027, 1, 26))
    assert live["done"] == {"一周翻倍": [0, 0], "一周涨五成": [1, 1]}
    assert state["sku_live"]["picks"][0]["tier"] == "一周涨五成"  # 存回状态的是现在的名字
    raw = potential_sku.summary({"picks": [{**old, "f": 750.0, "boom": True, "done": "2027-01-26"}]}, date(2027, 1, 26))
    assert raw["done"]["一周涨五成"] == [1, 1]
    rows = render._sku_live_rows(raw)
    assert "<td>一周涨五成" in rows and "涨四成" not in rows
    rc = potential_sku.recheck({"sku": {"M": ["一周涨四成", "上升中"]}}, {}, {"M": "watch"})
    assert rc["lost"] == {"M": "一周涨五成"}
    assert "涨四成" not in render.sku_hint(cfg) and "涨四成" not in render._method(cfg)


def test_big_volume_note_uses_the_table_units(cfg):
    """边界：括号里的“近30天≥600件”按表格“近30天”那一列（近 28 天日均 × 30，取整后显示）算：599.7 件显示 600 件的要写。"""
    s = potential_sku.settings(cfg)
    oos = _item("O", last7_avg=50.0, recent_avg28=19.0)  # 表格 570 件，×2.63
    oos["sku"] = potential_sku.classify(oos, s)
    assert oos["sku"]["tier"] == "一周翻倍" and render.sku_band_text(oos["sku"], cfg, oos) == "27%~50%"  # 不写括号
    assert '<td class="n">570 件' in render._sku_row(oos, 0, cfg)
    near = _item("N", last7_avg=50.0, recent_avg28=19.99)  # 599.7 件，表格显示 600 件
    near["sku"] = potential_sku.classify(near, s)
    assert render.sku_band_text(near["sku"], cfg, near) == "27%~50%（近30天≥600件的 15%）"
    assert render.sku_band_text(near["sku"], cfg) == "27%~50%"



def test_blue_ocean_items_are_judged_but_stay_in_the_blue_ocean_board(cfg):
    """2026-11-20 用户：蓝海也要判进来。蓝海产品只在蓝海产品里列（不进突然爆火、新品潜力、增长商品），潜力SKU 清单上照样判，
    表格“类型”下面写“蓝海产品”，下载的表格“所在板块”写蓝海产品；上期是潜力SKU 的蓝海产品本期还在追踪就不算“没追踪”。"""
    from radar.detect import diff
    sea = _item("SEA", last7_avg=25.0, ocean={"niche": "台球", "niche_revenue": 1.0, "revenue": 1.0}, label="potential", rising=None)
    items = [_item("M1"), sea]
    potential_sku.mark(items, cfg)
    sec = analyze.sections(items, 0)
    assert [i["asin"] for i in sec["sku"]] == ["SEA", "M1"] and sec["counts"]["sku"] == 2
    assert [i["asin"] for i in sec["ocean"]] == ["SEA"] and not sec["potential"]  # 照样只在蓝海产品里
    row = render._sku_row(sea, 0, cfg)
    assert '<td class="nc opt" data-tip=' in row and "<small>新品潜力</small><small>蓝海产品</small></td>" in row
    xl = json.loads(re.search(r'data-xl="([^"]+)"', row).group(1).replace("&quot;", '"'))
    assert xl[6] == "新品潜力" and xl[-2] == "蓝海产品"
    # 复查：上期潜力SKU 里的蓝海产品，本期拉到了日数据（sku_tracked）但不再符合 → 还在追踪但不再符合，不是“没追踪”
    prev = {"id": "2026-11-02", "labels": {"M1": ["watch"]}, "sku": {"SEA": ["一周翻倍", "新品潜力"], "GONE": ["一周涨五成", "上升中"]}}
    got = diff.compare(prev, {"M1": "watch"}, sku={"M1": ["一周涨五成", "上升中"]}, sku_tracked={"SEA"})["sku_check"]
    assert got["lost"] == {"SEA": "一周翻倍"} and got["gone"] == ["GONE"]
    assert diff.compare(prev, {"M1": "watch"}, sku={})["sku_check"]["gone"] == ["SEA", "GONE"]  # 不传时和以前一样


def test_table_fits_the_popup_without_side_scrolling(cfg):
    """2026-11-20 用户：小窗表格 14 列要左右拖，尽量都展示全。相关的并成一格（下面一行小字），9 列：
    子类目下面写类型；近30天下面写销售额、月增量；近7天÷近28天下面写近14天的；回测卖爆括号里的另起小字；
    评分下面写评论数、上架天数；风险写在“为什么值得看”下面（红字，鼠标停留写回测数）。下载的 Excel 列不变。"""
    s = potential_sku.settings(cfg)
    big = _item("G", last7_avg=50.0, recent_avg28=25.0, ratings=800, tags=["降价驱动"])
    big["sku"] = potential_sku.classify(big, s)
    row = render._sku_row(big, 0, cfg)
    assert row.count("<td") == len(render.SKU_HEAD) == 9
    assert render.SKU_HEAD == ("", "商品", "子类目 / 类型", "售价", "近30天 / 月增量", "近7天÷近28天", "回测卖爆", "评分 / 上架",
                               "为什么值得看 / 风险")
    assert ">床头柜<small>上升中</small></td>" in row
    assert '<td class="n">750 件<small>$11 万</small><small>月增 $' in row
    assert '<td class="n">×2.00<small>近14天 ×0.52</small></td>' in row
    assert '<td class="n bt">15%~24%<small>近30天≥600件的 15%</small><small>评论500条以上的 41%</small></td>' in row
    assert "★4.6<small>800 条</small><small>上架 200 天</small></td>" in row
    assert row.endswith('<small class="rk">风险：降价驱动</small></td></tr>')
    table = render._sku_table([big], cfg=cfg)
    assert len(re.findall(r"<th[ >]", table)) == 9 and 'colspan="9"' in table
    assert len(json.loads(re.search(r'data-xl="([^"]+)"', row).group(1).replace("&quot;", '"'))) == len(render.SKU_COLS)

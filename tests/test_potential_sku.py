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
    assert potential_sku.classify(_item("A"), s) == {"tier": "一周涨五成", "kind": "上升中", "a7": 1.6, "net": 1.6, "pace": None,
                                                     "a14": 1.3, "promo": None}
    assert potential_sku.classify(_item("B", last7_avg=20.0), s)["tier"] == "一周翻倍"
    assert potential_sku.classify(_item("C", last7_avg=14.9), s) is None  # 1.49 倍：和增长商品平均一样容易爆，不列
    assert potential_sku.classify(_item("D", label="surge", surge={"kind": "稳在高位"}, rising=None), s)["kind"] == "突然爆火"
    assert potential_sku.classify(_item("E", label="potential", rising=None), s)["kind"] == "新品潜力"
    assert potential_sku.classify(_item("F", label="hot"), s)["kind"] == "上升中"  # 持续热销里带上升中的也是增长商品
    window = _item("G", label="surge", surge={"kind": "稳在高位"}, rising=None, recent_avg7=30.0, last7_avg=12.0)
    assert potential_sku.classify(window, s) is None  # 爆发窗口日均 30，近 7 天其实只有 12（×1.2）
    assert potential_sku.why({**_item("B", last7_avg=20.0), "sku": potential_sku.classify(_item("B", last7_avg=20.0), s)}) == \
        "上升中；近 7 天日均是近 28 天的 ×2.00，最近一周翻倍了"


def test_subcategory_wide_acceleration_is_taken_out(cfg):
    """2026-11-18 复核回测：整个子类目一起涨的那几周，单个商品的加速多半是跟着涨（子类目中位数 ≥1.25 时一周翻倍只有 6% 后续卖爆）。
    加速倍数先除以所在子类目这周整体的倍数（本期追踪的同子类目商品、近 30 天 ≥60 件的，近 7 天 ÷ 近 28 天的中位数；≥10 个才算，只除大于 1 的）。"""
    s = potential_sku.settings(cfg)
    # 电视柜 10 个追踪商品整体 ×1.2（近 28 天日均 10、近 7 天 12），一个近 7 天 21 的（×2.1）
    peers = [_item(f"P{i}", node="tv", last7_avg=12.0, rising=None) for i in range(10)]
    star = _item("S", node="tv", last7_avg=21.0)
    pace = potential_sku.node_pace(peers + [star], s)
    assert pace == {"tv": 1.2}
    sku = potential_sku.classify(star, s, pace)
    assert (sku["tier"], sku["a7"], sku["net"], sku["pace"]) == ("一周涨五成", 2.1, 1.75, 1.2)  # ×2.1 ÷ 1.2 = ×1.75
    assert potential_sku.why({**star, "sku": sku}) ==         "上升中；近 7 天日均是近 28 天的 ×2.10（所在子类目这周整体 ×1.20，扣掉后 ×1.75），最近一周涨了五成以上"
    assert potential_sku.classify(_item("M", node="tv", last7_avg=16.0), s, pace) is None  # ×1.6 ÷ 1.2 = ×1.33：不列
    # 不到 10 个、量不够 60 件/30 天的不算进子类目；整体在跌（<1）的不扣（不会因此多收）
    assert potential_sku.node_pace(peers[:8] + [star], s) == {}  # 9 个
    small = [_item(f"Q{i}", node="tv", last7_avg=1.2, recent_avg28=1.0, rising=None) for i in range(5)]
    assert potential_sku.node_pace(peers[:4] + small + [star], s) == {}  # 量够的只有 5 个
    slow = [_item(f"D{i}", node="tv", last7_avg=8.0, rising=None) for i in range(10)]
    assert potential_sku.classify(star, s, potential_sku.node_pace(slow, s))["net"] == 2.1
    # mark 用全部商品算子类目的倍数
    items = peers + [star]
    assert potential_sku.mark(items, cfg) == 1 and star["sku"]["pace"] == 1.2


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
    """第 6 张 KPI、正文前几个（每个子类目最多 per_node 个）、小窗表格：每行带筛选 / 排序用的 data-*，下载的 Excel 表一行一个商品（列和 SKU_COLS 一一对应），
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
    cols = json.loads(re.search(r'data-cols="([^"]+)"', pop).group(1).replace("&quot;", '"'))
    assert [c[0] for c in cols[:7]] == ["主图", "商品", "品牌", "ASIN", "子类目", "档", "类型"] and len(cols) == len(render.SKU_COLS)
    assert [c[0] for c in cols[11:14]] == ["近7天÷近28天", "扣子类目整体后", "近14天÷近28天"]
    assert [c[2] for c in cols[11:14]] == ["bar", "ratio", "ratio"]
    assert all(len(c) == 4 and isinstance(c[1], int) for c in cols)
    assert 'data-title="潜力SKU · 2026-11-16"' in pop
    rows = [json.loads(m.replace("&quot;", '"')) for m in re.findall(r'data-xl="([^"]+)"', pop)]
    assert len(rows) == 5 and all(len(r) == len(cols) for r in rows) and rows[0][3][0] == "T1"
    m4 = next(r for r in rows if r[3][0] == "M4")
    assert m4[1][1] == m4[3][1] == "https://www.amazon.com/dp/M4"  # 商品名、ASIN 都链到商品页
    assert m4[5:7] == ["一周涨五成", "新品潜力"] and m4[8] == 300 and m4[9] == 45000
    assert m4[11:14] == [1.6, 1.6, 1.3] and m4[18] == "降价驱动"  # 没扣子类目的：扣子类目整体后和近7天÷近28天一样
    assert 'data-seg="一周翻倍"' in pop and 'data-seg="一周涨五成"' in pop and 'data-a7="2.0"' in pop
    hint = render.sku_hint(cfg)
    assert "潜力SKU = 本期增长商品（突然爆火、新品潜力、上升中）里最近一周在加速的：近 7 天日均 ≥ 近 28 天的 1.5 倍" in hint
    assert "所在子类目这周整体也在涨的，先扣掉整体的涨幅" in hint
    assert "卖爆 = 第 2 个月的 30 天销量 ≥ 现在近 30 天的 2 倍且 ≥300 件，一周翻倍的只要把最近一周的速度保持两个月就够这个标准" in hint
    # 回测（2026-11-18 复核后）：区间是有第 2 个月结果的判定日；写前后两段的范围和不重合的“再翻一倍”，说明幸存者偏差
    assert "回测（2025-11~2026-08 每周" in hint
    assert "一周翻倍的 26%~38% 后续卖爆，其中在最近一周的速度上再翻一倍的 8.1%；一周涨五成的 12%~15%" in hint
    assert "加速 1.2~1.5 倍的 6.7%、不到 1.2 倍的 2.3%" in hint and "（增长商品平均 5.6%，全部商品 3.1%）" in hint
    assert "样本是 2026-10-23 还在全市场名单里的商品，越靠近这天判的比例越高，实际会低一些" in hint
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
    assert ("**🎯 潜力SKU**（增长商品里最近一周加速的，子类目整体在涨的已扣掉：一周翻倍 1，回测 26%~38% 后续卖爆；"
            "一周涨五成 1，回测 12%~15% 后续卖爆；表格在报告里可下载）") in text
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
                    "回测里一周翻倍的这类后续卖爆 18%，近 7 天没有大促日的 36%")
    mid = _item("M1", as_of="2026-12-01")
    potential_sku.mark([mid], cfg, bf)
    assert potential_sku.promo_note(mid["sku"], cfg).endswith("回测里一周涨五成的这类后续卖爆 10%，近 7 天没有大促日的 14%")
    row = render._sku_row(star, 0, cfg)
    assert f'data-tip="{note}"' in row and ">近7天含大促日、降价驱动</td>" in row
    assert json.loads(re.search(r'data-xl="([^"]+)"', row).group(1).replace("&quot;", '"'))[18] == "近7天含大促日、降价驱动"
    assert "data-tip" not in render._sku_row(calm, 1, cfg).split('class="rk')[1]
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
    """实盘命中率：每期记下判定的潜力SKU（判定时近 30 天销量 = 近 28 天日均 × 30），判定后满 56 天用那时拉到的日数据看第 2 个月
    （判定后第 27~56 天这 30 天）卖爆没有，口径同回测；同一个商品还在等结果时不另记，重新生成本期时按现在的规则重记；
    到期了还没出结果的下期必看（tracking.watchlist）。报告小窗、钉钉写“卖爆个数/出结果个数”。"""
    from datetime import date
    items = [_item("T1", last7_avg=20.0, as_of="2026-11-15"), _item("M1", as_of="2026-11-15"),
             _item("M2", as_of="2026-11-15"), _item("X", last7_avg=12.0, as_of="2026-11-15")]
    potential_sku.mark(items, cfg)
    state = {"asins": {}, "runs": []}
    live = potential_sku.track(state, items, "2026-11-16", cfg, date(2026, 11, 15))
    picks = state["sku_live"]["picks"]
    assert [p["asin"] for p in picks] == ["T1", "M1", "M2"] and state["sku_live"]["since"] == "2026-11-16"
    assert picks[0] == {"asin": "T1", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍", "kind": "上升中", "net": 2.0,
                        "u": 300.0, "title": "Wood Nightstand T1", "node": "床头柜"}
    assert (live["total"], live["waiting"], live["next_due"], live["done"]) == \
        (3, 3, "2027-01-10", {"一周翻倍": [0, 0], "一周涨五成": [0, 0]})
    text = render.sku_live_text(live, cfg)
    assert text.startswith("实盘跟踪：2026-11-16 这期起每期记下判定的潜力SKU（含简单款；同一个商品还在等结果时再入选不另记）")
    assert text.endswith("已记 3 个，还没有到期的，最早 2027-01-10 出结果。")
    # 重新生成本期：本期记的先去掉再按现在的规则重记（M2 不再算）
    del items[2]["sku"]
    potential_sku.track(state, items, "2026-11-16", cfg, date(2026, 11, 15))
    assert [p["asin"] for p in state["sku_live"]["picks"]] == ["T1", "M1"]
    # 下一期 M1 还在清单：还在等结果，不另记（按第一次入选那天算）
    potential_sku.track(state, [items[1]], "2026-11-19", cfg, date(2026, 11, 18))
    assert len(state["sku_live"]["picks"]) == 2
    # 到期（判定后满 56 天）还没出结果的：下期必看
    assert potential_sku.due(state, date(2027, 1, 9)) == [] and potential_sku.due(state, date(2027, 1, 10)) == ["T1", "M1"]
    assert tracking.watchlist(state, 1, date(2027, 1, 12)) == ["T1", "M1"]
    assert potential_sku.waiting(state) == {"T1", "M1"}  # 很久没进池也不从状态里整条删（pipeline 的清理）
    # 2027-01-12 那期拉到了日数据：第 2 个月 = 2026-12-12 ~ 2027-01-10
    days = (date(2027, 1, 12) - date(2026, 11, 1)).days + 1
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    state["asins"]["T1"] = {"series": _series("2026-11-01", [10] * lead + [25] * 30 + [5, 5])}  # 750 件 ≥ 300 × 2：卖爆
    state["asins"]["M1"] = {"series": _series("2026-11-01", [10] * lead + [12] * 30 + [5, 5])}  # 360 件：没有
    assert len(state["asins"]["T1"]["series"]["sales"]) == days
    live = potential_sku.track(state, [{**items[0], "as_of": "2027-01-12"}], "2027-01-12", cfg, date(2027, 1, 12))
    done = {p["asin"]: p for p in state["sku_live"]["picks"] if "f" in p}
    assert (done["T1"]["f"], done["T1"]["boom"], done["M1"]["f"], done["M1"]["boom"]) == (750.0, True, 360.0, False)
    assert done["T1"]["done"] == "2027-01-12"
    assert live["done"] == {"一周翻倍": [1, 1], "一周涨五成": [1, 0]} and live["overdue"] == 0
    assert [p["asin"] for p in state["sku_live"]["picks"] if "f" not in p] == ["T1"]  # 出了结果以后再入选：另记一次
    assert potential_sku.due(state, date(2027, 1, 12)) == []
    text = render.sku_live_text(live, cfg)
    assert ("已出结果 2 个，卖爆的一周翻倍 1/1（100%，回测 26%~38%）、一周涨五成 0/1（0%，回测 12%~15%）。"
            "还在等 1 个（下一批 2027-03-09 起）。") in text
    table = render._sku_live_rows(live)
    assert "出了结果的 2 个" in table and table.count("<tr>") == 3 and ">卖爆</td>" in table and ">没有</td>" in table
    pop = render._sku_pop({"shown": [], "plain": [], "similar": {}}, "提示", 0, "2027-01-12", live, cfg)
    assert render.esc(text) in pop and "出了结果的 2 个" in pop
    sec = analyze.sections([items[0]], 0)
    ctx = {"title": "家具爆品雷达", "report_id": "2027-01-12", "as_of": "2027-01-12", "cfg": cfg, "sku_live": live,
           "sections": {**sec, "surge": [], "potential": [], "hot": [], "fake": [], "ocean": []},
           "diff": {"first": True}, "traits": {}}
    assert "；实盘已出结果 2 个，卖爆的一周翻倍 1/1、一周涨五成 0/1；表格在报告里可下载）" in dingtalk.build_text(ctx)


def test_live_outcome_needs_the_whole_second_month(cfg):
    """第 2 个月的数要到判定后第 56 天才有：没到的接着等；到了但有数的不到 20 天的算“数据不全”，不算进比例；
    到期了这期没拉到它的日数据的写“到期了还没拉到日数据”。"""
    from datetime import date
    s = potential_sku.settings(cfg)
    day = date(2026, 11, 15)
    lead = (date(2026, 12, 12) - date(2026, 11, 1)).days
    assert potential_sku.outcome(_series("2026-11-01", [10] * lead + [25] * 29), day, 300, s) is None  # 只到 01-09
    assert potential_sku.outcome(None, day, 300, s) is None
    gaps = [10] * lead + [25] * 19 + [None] * 11 + [5]
    assert potential_sku.outcome(_series("2026-11-01", gaps), day, 300, s) == {"f": None, "boom": False}
    assert potential_sku.outcome(_series("2026-11-01", [10] * lead + [9] * 30), day, 100, s) == {"f": 270.0, "boom": False}  # 不到 300 件
    state = {"asins": {"A": {"series": _series("2026-11-01", gaps)}},
             "sku_live": {"since": "2026-11-16", "picks": [
                 {"asin": "A", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周翻倍", "kind": "上升中", "net": 2.1, "u": 300.0},
                 {"asin": "B", "rid": "2026-11-16", "day": "2026-11-15", "tier": "一周涨五成", "kind": "上升中", "net": 1.6, "u": 200.0}]}}
    live = potential_sku.track(state, [], "2027-01-12", cfg, date(2027, 1, 12))
    assert (live["short"], live["overdue"], live["waiting"], live["done"]) == (1, 1, 1, {"一周翻倍": [0, 0], "一周涨五成": [0, 0]})
    text = render.sku_live_text(live, cfg)
    assert text.startswith("实盘跟踪：") and "已记 2 个" in text
    assert render.sku_live_text({"total": 0}, cfg) == "" and render.sku_live_text(None, cfg) == ""

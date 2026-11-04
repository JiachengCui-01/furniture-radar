"""开发方向一览的市场面（radar/detect/market.py）和报告里的一览、小窗（radar/report/render.py）。"""
from datetime import date

from radar.detect import market
from radar.report import charts, render

TODAY = date(2026, 11, 1)


def row(asin, node, price, revenue, *, available="2024-01-01", ratings=100, brand=None, title="Wood Nightstand"):
    return {"asin": asin, "node_cn": node, "node_name": "", "title": title, "brand": brand if brand is not None else f"B{asin}",
            "price": price, "revenue": revenue, "available": available, "ratings": ratings}


def state_with(rows, day=TODAY):
    return {"universe": {"date": day.isoformat(), "rows": {r["asin"]: r for r in rows}}}


def test_market_numbers_are_revenue_weighted_and_from_this_runs_list(cfg):
    """主力价格带按销售额加权（便宜货多但卖得少不会把价格带拉低）；新品占比、头部评论、品牌集中度都按定义算；
    名单不是本期翻的不算（不拿旧名单说现在的市场）；标题不在范围内的（读的时候也过滤）不算。"""
    cfg["report"]["market"] = {"new_days": 180, "head_n": 3, "top_brands": 1}
    rows = [row("A", "床头柜", 30, 1000, ratings=50), row("B", "床头柜", 35, 1000, ratings=60),
            row("C", "床头柜", 120, 5000, available="2026-08-01", ratings=10, brand="Big"),
            row("D", "床头柜", 150, 3000, ratings=900, brand="Big"),
            row("X", "床头柜", 500, 50000, title="Upholstered Velvet Bed Frame with Headboard")]
    out = market.summarize(state_with(rows), TODAY, cfg)["床头柜"]
    assert out["n"] == 4  # 软包床架不在范围内
    assert out["price"] == {"p25": 120, "p50": 120, "p75": 150}  # 一半的销售额在 $120~150，不是件数上的 $30~35
    assert out["new_share"] == 0.5 and out["n_new"] == 1  # 上架 92 天的那个占 5000/10000
    assert out["head_reviews"] == 50  # 销售额前 3 名 C、D、A：10、900、50 的中位数
    assert out["brand_share"] == 0.8  # Big 品牌两个商品合计 8000/10000
    assert abs(sum(out["hist"]) - 1) < 1e-6 and len(out["hist"]) == len(market.EDGES) - 1
    assert market.summarize(state_with(rows, date(2026, 10, 20)), TODAY, cfg) == {}


def test_board_rows_carry_sortable_numbers_and_open_their_own_popup(cfg):
    """开发方向一览：一行一个子类目（月增量条、价格带、新品占比、头部评论、比头部多的特征），表头可排序，
    每行点开自己的小窗（数字、价格分布、画像、商品卡片）；本期没翻全市场名单时市场列写“—”，价格带只画增长商品。"""
    def growth(asin, price, label="surge"):
        return {"asin": asin, "node_cn": "床头柜", "node_name": "", "title": "Fluted Nightstand", "brand": f"B{asin}",
                "label": label, "label_cn": "", "price": price, "image": "", "tags": [], "features": [], "fake": {"score": 0, "signals": []},
                "surge": {"window": 7, "base_avg": 2.0, "recent_avg": 8.0, "sales_ratio": 4.0, "base_days": 28},
                "recent_avg28": 6.0, "prev_avg28": 2.0, "spark": [2.0] * 30 + [8.0] * 7, "spark_start": "2026-09-26",
                "monthly": [], "age_days": 40, "rating": 4.6, "ratings": 30}
    items = [growth("G1", 120), growth("G2", 150)]
    d = {"node": "床头柜", "n": 2, "n_brands": 2, "n_surge": 2, "sales": {"monthly": 30_000, "increment": 20_000},
         "materials": [{"label": "实木", "count": 2}], "price": {"min": 120, "max": 150, "median": None}, "pkg": None,
         "features": {dim: {"more": [], "base": []} for dim in ("风格", "造型", "工艺", "颜色", "功能卖点")},
         "compare": {"mode": "title", "n_head": 30}, "refs": []}
    d["features"]["工艺"]["more"] = [{"label": "凹槽竖纹", "count": 2, "share": 1.0, "head_share": 0.2, "trend": True}]
    m = {"n": 40, "n_new": 6, "new_days": 180, "new_share": 0.25, "head_n": 20, "head_reviews": 1530.0, "top_brands": 5,
         "brand_share": 0.4, "price": {"p25": 80, "p50": 110, "p75": 160}, "hist": [0, 0, 0, 0, 0.1, 0.4, 0.3, 0.2, 0, 0, 0, 0]}
    cur = {"growth": {"shown": items, "similar": {}}}
    section, pops = render._directions({"directions": [d], "market": {"床头柜": m}}, cfg, cur, items)
    assert section.count('class="brow"') == 1 and 'data-pop="dir0"' in section
    assert 'data-inc="20000"' in section and 'data-new="0.25"' in section and 'data-rev="1530"' in section
    assert ">25%<" in section and ">1.5k<" in section and "主力 $80–160" in section
    assert "凹槽竖纹" in section and 'data-sort="new"' in section and section.count('class="dot k-surge"') == 2
    assert 'id="pop-dir0"' in pops and 'class="phist"' in pops and "25%（6/40 个）" in pops
    assert pops.count('<article class="card"') == 2 and 'class="dbox dir"' in pops

    plain, _pops = render._directions({"directions": [d], "market": {}}, cfg, cur, items)
    assert '<span class="bnew">—</span>' in plain and "增长商品 $120–$150" in plain and 'class="pband"' not in plain


def test_charts_scale_and_skip_empty_input():
    lo, hi = charts.price_domain([33, 450])
    assert (lo, hi) == (30, 500)
    strip = charts.price_strip({"p25": 50, "p50": 80, "p75": 120}, [(60, "potential", "x"), (61, "rising", "y")], (lo, hi))
    assert strip.count("<circle") == 2 and 'class="pband"' in strip
    assert charts.mini_spark([None, 5.0], "2026-10-01", 7, (None, None)) == ""
    spark = charts.mini_spark([1.0] * 60 + [5.0] * 7, "2026-09-01", 7, (1.0, 5.0))
    assert 'data-d="2026-09-12"' in spark  # 只画最近 56 天，起始日期跟着往后挪

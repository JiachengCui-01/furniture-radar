"""外观与工艺：设计词库、主图识别、报告小窗、重新生成。"""
import json
from datetime import date, datetime, timedelta, timezone

import httpx

from radar import crypto, pipeline, store, vision
from radar.cli import _decrypt_page
from radar.cli import TRACK_ALL
from radar.config import Secrets
from radar.demo import SyntheticWorld
from radar.detect import design, traits


def test_extract_reads_appearance_and_craft_from_titles():
    f = design.extract("Mid-Century Modern Sideboard Buffet with Fluted Glass Doors, Open Shelves, Rattan, Walnut, Storage")
    assert f["风格"] == {"中古风", "现代"}
    assert f["材质"] == {"玻璃", "藤/编织"}
    assert f["造型"] == {"开放格"}
    assert f["工艺"] == {"凹槽竖纹", "玻璃门", "藤编/编织"}
    assert f["颜色"] == {"胡桃色"}
    assert f["功能卖点"] == {"带储物/抽屉"}
    # 板式家具的造型、功能、颜色（2026-10 重写词库时补上的）
    g = design.extract("L Shaped Computer Desk with Power Outlets, Monitor Stand, Cable Management, Rolling Wheels, Espresso")
    assert g["造型"] == {"L 形/U 形"} and g["颜色"] == {"深咖色"}
    assert g["功能卖点"] == {"充电/插座", "显示器架/键盘托", "线缆管理", "带轮可移动"}
    h = design.extract("Lift Top Coffee Table, Extendable, Barn Door, Greige; 5-Tier Staggered Bookshelf")
    assert h["功能卖点"] == {"升降台面", "可伸缩/加长"} and h["工艺"] == {"谷仓门/推拉门"}
    assert h["颜色"] == {"灰褐/做旧灰"} and h["造型"] == {"错落/阶梯层板"}  # “5-Tier”只是层数，不算错落


def test_appearance_also_tier_and_colors_from_images(cfg):
    """“也有”：10%~30% 有、不比对照组多；颜色有主图识别时只看主图，标题里的颜色不能否决（标题常列出全部可选颜色）。"""
    t = lambda label, n, share, base: {"label": label, "count": n, "share": share, "baseline_share": base, "brands": n}
    i = lambda label, n, ref: {"label": label, "focus": n, "focus_share": n / 50, "reference": ref,
                               "reference_share": ref / 40, "brands": n}
    tr = {"design": {"颜色": [{"label": "棕色"}]},
          "title_shares": {"颜色": {"胡桃色": t("胡桃色", 3, 0.05, 0.08), "棕色": t("棕色", 8, 0.15, 0.09)},
                                         "功能卖点": {"电动/可升降": t("电动/可升降", 12, 0.21, 0.17)}}}
    vision = {"n_focus": 50, "n_reference": 40, "dimensions": {
        "颜色": [i("胡桃色", 10, 0), i("白色", 12, 10), i("原木色", 7, 6), i("灰色", 4, 2)],
        "造型": [i("模块化组合", 7, 4)]}}
    out = traits.merge_appearance(tr, vision, cfg)
    assert [f["label"] for f in out["颜色"]["more"]] == ["胡桃色"]  # 主图 20% vs 0%，标题 5% vs 8% 不再否决；标题的棕色不看
    assert [f["label"] for f in out["颜色"]["also"]] == ["白色", "原木色"]  # 灰色 8% 不到 10%
    assert [f["label"] for f in out["造型"]["also"]] == ["模块化组合"]
    assert [f["label"] for f in out["功能卖点"]["also"]] == ["电动/可升降"]  # 只有标题依据，21%、1.24 倍
    no_images = traits.merge_appearance(tr, {}, cfg)  # 没有主图识别时颜色看标题
    assert [f["label"] for f in no_images["颜色"]["more"]] == ["棕色"]


def test_vision_cache_is_redone_when_the_vocabulary_changes(cfg):
    """词库一改（词表版本不同），缓存的识别结果按新词表重看；同一版本不重复调用。"""
    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(200, content=b"\xff\xd8fake", headers={"content-type": "image/jpeg"})
        content = '{"风格":[],"材质":[],"造型":["开放格"],"工艺":[],"颜色":[],"外观要点":"x"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    url = "https://x/a._AC_US600_.jpg"
    state = {"asins": {"A": {"vision": {"image": url, "tags": {"造型": ["云朵造型"]}, "summary": ""}}}}
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert vision.tag_items([{"asin": "A", "image": url}], state, cfg, "key", client=client) == 1
    assert state["asins"]["A"]["vision"]["tags"]["造型"] == ["开放格"]
    assert state["asins"]["A"]["vision"]["vocab"] == vision.VOCAB_VERSION
    assert vision.tag_items([{"asin": "A", "image": url}], state, cfg, "key", client=client) == 0


def test_compare_finds_features_overrepresented_in_hot_products():
    baseline = [{"asin": f"B{i}", "title": "Modern Engineered Wood Bookcase, Black"} for i in range(60)]
    baseline += [{"asin": f"C{i}", "title": "Fluted Sideboard, Walnut"} for i in range(3)]
    focus = [{"asin": f"F{i}", "title": "Fluted Curved Sideboard Cabinet with Rattan Doors, Walnut"} for i in range(5)]
    result = design.compare(baseline + focus, focus)
    craft = {r["label"]: r for r in result["工艺"]}
    assert craft["凹槽竖纹"]["count"] == 5 and craft["凹槽竖纹"]["lift"] > 5
    assert "人造板" not in {r["label"] for r in result.get("材质", [])}  # 基线里到处都是，不算爆火特征


def test_vision_parse_tolerates_wrapped_json():
    text = '好的：```json\n{"风格":["奶油风"],"材质":"泰迪绒/羊羔绒","造型":[],"工艺":["凹槽竖纹"],"颜色":["奶油/米色"],' \
           '"外观要点":"奶油色泰迪绒弧形沙发"}\n```'
    parsed = vision._parse(text)
    assert parsed["tags"]["材质"] == ["泰迪绒/羊羔绒"] and parsed["summary"] == "奶油色泰迪绒弧形沙发"
    assert vision.small_image("https://m.media-amazon.com/images/I/41Ac._AC_US600_.jpg").endswith("._AC_SL400_.jpg")


def test_vision_tags_are_cached(cfg):
    calls = {"img": 0, "llm": 0}

    def handler(request: httpx.Request):
        if request.method == "GET":
            calls["img"] += 1
            return httpx.Response(200, content=b"\xff\xd8fake", headers={"content-type": "image/jpeg"})
        calls["llm"] += 1
        body = json.loads(request.content)
        assert body["model"] == cfg["llm"]["vision_model"]
        assert body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        content = '{"风格":["奶油风"],"材质":["泰迪绒/羊羔绒"],"造型":["弧形曲线"],"工艺":[],"颜色":[],"外观要点":"弧形"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    state = {"asins": {"A1": {}, "A2": {}}}
    items = [{"asin": "A1", "image": "https://x/a._AC_US600_.jpg", "label": "surge"},
             {"asin": "A2", "image": "https://x/b._AC_US600_.jpg", "label": "potential"}]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert vision.tag_items(items, state, cfg, "key", client=client) == 2
    assert vision.tag_items(items, state, cfg, "key", client=client) == 0  # 图片没变：不再调用
    assert calls == {"img": 2, "llm": 2}
    summary = vision.summarize(items, [], state)
    assert summary["dimensions"]["材质"][0] == {"label": "泰迪绒/羊羔绒", "focus": 2, "focus_share": 1.0, "brands": 2,
                                              "reference": 0, "reference_share": None, "distinct": False}
    # 有持续热销对照时，“比热销多见”的特征排前面并标出
    state["asins"]["H1"] = {"vision": {"image": "x", "summary": "", "tags": {"材质": ["人造板"], "造型": ["弧形曲线"]}}}
    state["asins"]["A1"]["vision"]["tags"]["造型"] = ["弧形曲线", "低矮落地"]
    state["asins"]["A2"]["vision"]["tags"]["造型"] = ["弧形曲线", "低矮落地"]
    shapes = vision.summarize(items, [{"asin": "H1"}], state)["dimensions"]["造型"]
    assert [r["label"] for r in shapes] == ["低矮落地", "弧形曲线"] and shapes[0]["distinct"] and not shapes[1]["distinct"]
    assert vision.tag_items(items, {"asins": {}}, cfg, "") == 0  # 没有 key 就不识别


def _two_runs(cfg, master_key, tmp_path):
    cfg["budget"].update(stable_refresh_every=1, per_run=400, bootstrap_run=400)
    cfg["pool"].update(TRACK_ALL)
    start = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)
    world = SyntheticWorld(start.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=start)
    world.advance(3)
    second = pipeline.run(cfg, secrets, tmp_path, transport=world, now=start + timedelta(days=3))
    return secrets, second


def test_report_has_popups_and_design_section(cfg, master_key, tmp_path):
    _secrets, second = _two_runs(cfg, master_key, tmp_path)
    page = (tmp_path / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8")
    plain = _decrypt_page(page, crypto.parse_master_key(master_key))
    for kind in ("surge", "potential", "hot", "fake", "seasonal", "growth", "season", "all"):
        assert f'data-pop="{kind}"' in plain and f'id="pop-{kind}"' in plain
    assert plain.count('class="pop-src"') == 8 and 'id="pop"' in plain
    # 季节性商品：演示数据里餐边柜有一个每年 11~12 月卖得特别好的商品，10 月是淡季，不在任何板块（卡片写“观察”）；
    # 卡片上是它自己近 2 年每月日均的柱状图（旺季月深色，两年的旺季都在）
    seasonal = plain.split('id="pop-seasonal"')[1].split('<div class="pop-src"')[0]
    winter = next(p.asin for p in SyntheticWorld(date(2026, 10, 5)).products if p.kind == "seasonal")
    assert winter in seasonal and '<span class="grp">观察</span>' in seasonal and seasonal.split(winter, 1)[1].split('</article>')[0].count('class="bar hi"') == 4
    assert "连续 2 年 11~12 月旺，每年旺季日均都至少是前后淡季的" in seasonal and "离旺季还有 1 个月" in seasonal
    assert "<em>品类</em>季节性品类：旺季 11~12 月" in seasonal and "季节性（连续 2 年 11~12 月旺" in seasonal
    # 主体里不放商品卡片：所有卡片都在隐藏的小窗内容里
    body = plain.split('<div class="pop-src"')[0]
    assert '<article class="card"' not in body and '<article class="card"' in plain
    assert "外观与工艺依据" in body and "增长商品" in body and "本期要点" in body
    assert "凹槽竖纹" in body and "<h4>主材质</h4>" in body  # 演示数据里爆火商品标题都带 Fluted
    assert "Material 属性" in plain
    assert body.index("<h3>开发建议</h3>") < body.index("<h3>开发方向</h3>")  # 两节都要有，不是互相替换
    assert "<h3>开发方向依据</h3>" in body and 'class="dbox dir"' in body
    for gone in ("爆火/上升中", "爆火/潜力中", "全部家具", "爆火回落", "持续型", "爬升型", "脉冲型"):
        assert gone not in plain, gone  # 同一个概念只有一种叫法


def test_rerender_keeps_report_id_and_schedule(cfg, master_key, tmp_path):
    secrets, second = _two_runs(cfg, master_key, tmp_path)
    master = crypto.parse_master_key(master_key)
    before = store.load(tmp_path, master)
    path = tmp_path / "reports" / f"{second.report_id}.html"
    old_page = path.read_text(encoding="utf-8")

    result = pipeline.rerender(cfg, secrets, tmp_path)

    after = store.load(tmp_path, master)
    assert result.published and result.report_id == second.report_id
    assert path.read_text(encoding="utf-8") != old_page  # 重新加密写入
    assert len(after["runs"]) == len(before["runs"]) and after["reports"] == before["reports"]
    assert after["last_success"] == before["last_success"] and after["run_seq"] == before["run_seq"]
    assert after["calls"] == before["calls"]  # 没有调用卖家精灵
    plain = _decrypt_page(path.read_text(encoding="utf-8"), master)
    assert "与上期对比" in plain and "2026-10-05.html#k=" in plain


def test_amazon_material_attribute_beats_title():
    from radar import materials

    assert materials.from_overviews('{"Brand":"X","Material":"Engineered Wood","Frame Material":"Metal"}')         == "Engineered Wood, Metal"
    assert materials.classify("Engineered Wood, Metal") == "铁木（金属+木）"
    assert materials.classify("Particle Board") == "板材"
    assert materials.classify("Solid Pine") == "实木"
    assert materials.classify("Tempered Glass") == "其他"
    assert materials.classify("") is None
    rec = {"title": "Rustic Wood TV Stand", "amazon_material": {"raw": "MDF", "class": "板材"}}
    assert materials.best(rec) == ("板材", "amazon")
    assert materials.best({"title": "Solid Wood Nightstand"}) == ("实木", "title")


def test_potential_growth_shows_units_when_percentage_is_absurd():
    from radar.report.render import growth_text

    item = {"potential": {"growth": 93.0}, "monthly": [["2026-08", 13], ["2026-09", 1222]]}
    assert growth_text(item) == "月销 13 → 1,222 件"
    assert growth_text({"potential": {"growth": 0.42}, "monthly": []}) == "月环比 +42%"


def test_appearance_lists_more_and_common_features(cfg):
    """“更多”要有依据且不冲突；“常见”只说明多数有。卡在门槛上的不能因为浮点误差漏掉。"""
    t = lambda label, n, share, base: {"label": label, "count": n, "share": share, "baseline_share": base}
    i = lambda label, n, ref: {"label": label, "focus": n, "focus_share": n / 20, "reference": ref,
                               "reference_share": ref / 10}
    tr = {"design": {"工艺": [dict(t("凹槽竖纹", 3, 0.15, 0.07), lift=2.1)],
                     "风格": [dict(t("现代", 4, 0.2, 0.13), lift=1.5)]},
          "title_shares": {"工艺": {"凹槽竖纹": t("凹槽竖纹", 3, 0.15, 0.07)},
                           "风格": {"现代": t("现代", 4, 0.2, 0.13), "农舍风": t("农舍风", 2, 0.1, 0.09)},
                           "颜色": {"黑色": t("黑色", 3, 0.15, 0.2)}}}
    tr["title_shares"]["功能卖点"] = {"带储物/抽屉": t("带储物/抽屉", 9, 0.48, 0.52)}
    vision = {"n_focus": 20, "n_reference": 10, "dimensions": {
        "风格": [i("现代", 16, 9), i("农舍风", 3, 0)],
        "工艺": [i("锥形/外八腿", 6, 1)],
        "颜色": [i("黑色", 11, 8)],
        "功能卖点": [i("带储物/抽屉", 16, 4)]}}
    out = traits.merge_appearance(tr, vision, cfg)
    assert [f["label"] for f in out["工艺"]["more"]] == ["锥形/外八腿", "凹槽竖纹"]  # 30% − 10% 正好 20 个百分点
    # 依据冲突的哪一档都不列：现代（标题说更多、主图说更少）；带储物（主图 80% vs 40%、标题 48% vs 52%），
    # 放进“常见但不比对照组多”就和显示的主图数字矛盾（实测 2026-10-14 报告：40/50 · 对照 17/40）
    assert [f["label"] for f in out["风格"]["more"]] == ["农舍风"] and out["风格"]["common"] == []
    assert "功能卖点" not in out
    assert out["颜色"]["more"] == [] and [f["label"] for f in out["颜色"]["common"]] == ["黑色"]


def test_reference_group_excludes_growth_products(cfg):
    """持续热销里带“上升中”的也是增长商品：只算增长商品，不进对照组，空出的位置按月销量往下补。"""
    def item(asin, label, avg, rising=None):
        return {"asin": asin, "label": label, "hot": {"avg_monthly": avg}, "rising": rising}

    items = [item("H1", "hot", 900), item("H2", "hot", 800, rising={"ratio": 1.5}), item("H3", "hot", 700),
             item("H4", "hot", 600), item("W1", "watch", 0, rising={"ratio": 2.0})]
    cfg["llm"].update(vision_focus=50, vision_reference=3)
    focus, reference = pipeline.analysis_groups(items, cfg)
    assert [i["asin"] for i in focus] == ["W1", "H2"]
    assert [i["asin"] for i in reference] == ["H1", "H3", "H4"]



def test_directions_list_only_what_most_of_the_subcategory_has(cfg):
    """开发方向：只看该子类目自己的增长商品，特征要一半以上（且 ≥2 个）具备；标题和主图任一有就算。"""
    from radar.detect import directions

    def item(asin, node, title, label="surge", material="板材", price=100.0):
        return {"asin": asin, "node_cn": node, "node_name": "", "title": title, "brand": f"Brand{asin}",
                "label": label, "material": material, "price": price, "look": ""}

    focus = [
        item("V1", "梳妆台", "Vanity Desk with Mirror and LED Lights, Glass Doors, White", price=160),
        item("V2", "梳妆台", "Makeup Vanity with LED Lights and Charging Outlet, White", price=228),
        item("V3", "梳妆台", "Vanity Table with Drawers, Black", label="potential", material="实木", price=170),
        item("S1", "书架", "Narrow Bookshelf, Black"),
        item("S2", "书架", "Narrow Bookcase for Small Space, Walnut", label="potential"),
        item("T1", "茶几", "Round Coffee Table"),
    ]
    state = {"asins": {"V3": {"vision": {"tags": {"工艺": ["玻璃门"]}}}}}
    appearance = {"工艺": {"more": [{"label": "玻璃门"}], "common": []}}
    out = directions.compute(focus, state, appearance, cfg)
    assert [d["node"] for d in out] == ["梳妆台", "书架"]  # 茶几只有 1 个增长商品
    v = out[0]
    assert v["n"] == 3 and v["need"] == 2 and v["n_surge"] == 2
    assert v["materials"] == [{"label": "板材", "count": 2}]
    # 没有月度名单（同子类目头部商品）时不对比，只列共同特征
    assert v["compare"]["mode"] is None
    assert v["features"]["工艺"] == {"more": [], "base": [{"label": "玻璃门", "count": 2, "trend": True,
                                                           "head_share": None}]}  # 标题 1 个 + 主图 1 个
    assert [f["label"] for f in v["features"]["功能卖点"]["base"]] == ["LED 灯"]  # 充电/插座、抽屉各只有 1 个
    assert v["features"]["颜色"]["base"] == [{"label": "白色", "count": 2, "trend": False, "head_share": None}]
    assert v["price"] == {"min": 160, "max": 228, "median": 170}
    assert [r["asin"] for r in v["refs"]] == ["V1", "V2"]
    assert directions.headline(v) == "梳妆台：板材，玻璃门、白色、LED 灯，$160–$228"
    assert [f["label"] for f in out[1]["features"]["造型"]["base"]] == ["窄小省空间"] and out[1]["materials"] == [
        {"label": "板材", "count": 2}]


def test_directions_count_brands_and_skip_flagged_references(cfg):
    """同一品牌的多条链接只算一个品牌：一家的产品线撑不起“白色 / LED”这样的方向；
    只有一个品牌的子类目不出方向；有异常信号的商品不当参考，两个参考来自不同品牌。"""
    from radar.detect import directions

    def item(asin, node, title, brand, score=0):
        return {"asin": asin, "node_cn": node, "node_name": "", "title": title, "brand": brand, "label": "surge",
                "material": "板材", "price": 100.0, "look": "", "fake": {"score": score}}

    focus = [
        item("F1", "梳妆台", "Vanity Desk with LED Lights, White", "FIONESO"),
        item("F2", "梳妆台", "Makeup Vanity with LED Lights, White", "FIONESO"),
        item("F3", "梳妆台", "Vanity Table with LED Lights, White", "fioneso "),
        item("K1", "梳妆台", "Vanity Table, Black", "King Oasis"),
        item("N1", "床头柜", "Nightstand with Charging Station", "VASAGLE"),
        item("N2", "床头柜", "Nightstand with Charging Station, Drawers", "VASAGLE"),
        item("S1", "书架", "Narrow Bookshelf, Black", "TUTOTAK", score=2),
        item("S2", "书架", "Narrow Bookshelf, Walnut", "OOOK"),
        item("S3", "书架", "Narrow Bookcase, Walnut", "OOOK"),
    ]
    out = directions.compute(focus, {"asins": {}}, {}, cfg)
    assert [d["node"] for d in out] == ["梳妆台", "书架"]  # 床头柜只有 VASAGLE 一个品牌
    v = out[0]
    assert (v["n"], v["n_brands"], v["need"]) == (4, 2, 2)
    assert v["features"]["颜色"] == {"more": [], "base": []}  # 白色、LED 只有 FIONESO 一家有
    assert v["features"]["功能卖点"] == {"more": [], "base": []}
    assert v["materials"] == [{"label": "板材", "count": 2}]
    shelf = out[1]
    assert [f["label"] for f in shelf["features"]["造型"]["base"]] == ["窄小省空间"]
    assert [r["asin"] for r in shelf["refs"]] == ["S2"]  # S1 有异常信号；S3 和 S2 同一品牌


def _dir_item(asin, node, title, brand, *, price=100.0, recent=10.0, prev=5.0, label="surge"):
    return {"asin": asin, "node_cn": node, "node_name": "", "title": title, "brand": brand, "label": label,
            "material": "板材", "price": price, "look": "", "fake": {"score": 0},
            "recent_avg28": recent, "prev_avg28": prev}


def _head_row(asin, node, title, units):
    return {"asin": asin, "node_cn": node, "node_name": "", "title": title, "brand": f"H{asin}", "units": units,
            "parent": None, "image": f"https://img/{asin}.jpg"}


def test_directions_rank_by_increment_not_brand_count(cfg):
    """按增量销售额排：3 个品牌、每个只多卖几件的便宜货，排在 2 个品牌、每个多卖很多的贵货后面。"""
    from radar.detect import directions

    focus = [
        _dir_item("C1", "衣帽架", "Coat Rack", "A", price=30, recent=20, prev=15),
        _dir_item("C2", "衣帽架", "Coat Rack", "B", price=30, recent=20, prev=15),
        _dir_item("C3", "衣帽架", "Coat Rack", "C", price=30, recent=20, prev=15),
        _dir_item("V1", "梳妆台", "Vanity with Mirror", "D", price=150, recent=40, prev=10),
        _dir_item("V2", "梳妆台", "Vanity with Mirror", "E", price=150, recent=30, prev=None),  # 之前没在卖：按 0
    ]
    out = directions.compute(focus, {"asins": {}}, {}, cfg)
    assert [d["node"] for d in out] == ["梳妆台", "衣帽架"]
    assert out[0]["sales"] == {"monthly": round((40 + 30) * 150 * 30), "increment": round((30 + 30) * 150 * 30)}
    assert out[1]["sales"]["increment"] == 3 * 5 * 30 * 30
    assert directions.money_text(out[0]["sales"]["increment"]) == "$27 万"


def test_directions_compare_with_heads_of_the_same_subcategory(cfg):
    """比头部多 = ≥2 个品牌、比同子类目头部高 20 个百分点以上且是 1.5 倍以上；品类本来就都有的进“基本款”。
    头部商品不在增长商品里；有主图识别的头部不到 10 个时两边都只看标题。"""
    from radar.detect import directions

    focus = [_dir_item(f"G{k}", "书架", "Fluted Bookshelf with Open Shelves, Walnut", f"B{k}") for k in range(4)]
    heads = [_head_row(f"H{k:02d}", "书架", "Bookshelf with Open Shelves" + (", Fluted" if k == 0 else ""), 900 - k)
             for k in range(14)]
    heads.append(_head_row("G0", "书架", "Fluted Bookshelf", 5000))  # 自己是增长商品，不算头部
    disc = {"products": {r["asin"]: r for r in heads}}
    out = directions.compute(focus, {"asins": {}}, {}, cfg, disc=disc)
    d = out[0]
    assert d["compare"] == {"mode": "title", "n_head": 14, "n_head_all": 14}
    assert [f["label"] for f in d["features"]["工艺"]["more"]] == ["凹槽竖纹"]  # 4/4 品牌 vs 头部 1/14
    assert d["features"]["工艺"]["more"][0]["head_share"] == round(1 / 14, 3)
    assert [f["label"] for f in d["features"]["造型"]["more"]] == []
    assert "开放格" in [f["label"] for f in d["features"]["造型"]["base"]]  # 头部也都有
    assert directions.headline(d).startswith("书架：板材，凹槽竖纹")

    # 头部商品有主图识别（≥10 个）：两边都看标题 + 主图，头部只算识别过的
    state = {"asins": {f"G{k}": {"vision": {"tags": {"颜色": ["胡桃色"]}}} for k in range(4)},
             vision.EXTRA_KEY: {r["asin"]: {"vision": {"tags": {"颜色": ["黑色"]}}} for r in heads[:12]}}
    d = directions.compute(focus, state, {}, cfg, disc=disc)[0]
    assert d["compare"] == {"mode": "image", "n_head": 12, "n_head_all": 14}
    assert [f["label"] for f in d["features"]["颜色"]["more"]] == ["胡桃色"]

    # 头部商品不够 10 个：不对比
    few = {"products": {r["asin"]: r for r in heads[:5]}}
    d = directions.compute(focus, {"asins": {}}, {}, cfg, disc=few)[0]
    assert d["compare"]["mode"] is None and d["features"]["工艺"]["more"] == []
    assert [f["label"] for f in d["features"]["工艺"]["base"]] == ["凹槽竖纹"]


def test_universal_feature_is_base_not_more(cfg):
    """4 个品牌全有、头部 73% 有：差 27 个百分点但不到 1.5 倍，算基本款。"""
    from radar.detect import directions

    focus = [_dir_item(f"G{k}", "梳妆台", "Vanity with Drawers", f"B{k}") for k in range(4)]
    heads = [_head_row(f"H{k:02d}", "梳妆台", "Vanity Table" + (" with Drawers" if k < 11 else ""), 900 - k)
             for k in range(15)]
    d = directions.compute(focus, {"asins": {}}, {}, cfg, disc={"products": {r["asin"]: r for r in heads}})[0]
    assert d["features"]["功能卖点"]["more"] == []
    assert [f["label"] for f in d["features"]["功能卖点"]["base"]] == ["带储物/抽屉"]


SEASON_CFG = {"history_months": 36, "max_pages": 4, "min_months": 24, "min_consistency": 0.5, "min_amplitude": 1.3,
              "max_error": 0.25, "peak_month": 1.15, "low_month": 0.87, "tag_ratio": 1.15, "ratio_shrink": 0.6,
              "calibrate_min_pairs": 30, "lead_days": 120, "launch_days": 90, "peak_ratio": 1.3, "busier": 1.2,
              "quieter": 0.8}


def _history(daily, months=36, end=(2026, 9)):
    """{“YYYY-MM”: 月销量}，daily(年, 月, 第几个月) = 当月日均。"""
    import calendar
    out = {}
    y, m = end
    for k in range(months):
        out[f"{y}-{m:02d}"] = daily(y, m, months - 1 - k) * calendar.monthrange(y, m)[1]
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def test_seasonal_category_from_three_years_of_category_sales():
    """每年 11、12 月是平时的 2、3 倍：季节性品类，旺季 11~12 月；品类自己在涨（每月 +2%）也认得出；
    按往年规律：近 28 天没什么季节变化，到货前 12 月是现在的 3 倍（赶不上），上架后和现在差不多。"""
    from datetime import date
    from radar.detect import seasonality as S

    winter = lambda y, m, t: 100 * {11: 2.0, 12: 3.0}.get(m, 1.0) * 1.02 ** t
    prof = S.profile(_history(winter), SEASON_CFG)
    assert prof["seasonal"] and prof["consistency"] > 0.99 and prof["peak_months"] == [11, 12]
    # 预测不带趋势项（实测加阻尼趋势也没更准），品类每月 +2% 时提前 1~6 个月的误差中位数约 9%
    assert S.months_text(prof["low_months"]) == "1~10 月" and prof["error"] < 0.15
    assert round(prof["index"][12] / prof["index"][6], 1) == 3.0  # 去掉了每月 2% 的增长
    o = S.outlook(prof, date(2026, 10, 19), SEASON_CFG)
    assert not o["growth_seasonal"] and o["peak_before"] and o["peak"]["month"] == "2026-12"
    assert o["launch_state"] == "same" and [m for m, _ in o["curve"]][:3] == ["2026-11", "2026-12", "2027-01"]
    v = S.verdicts(o)
    assert v[0].startswith("季节性品类：旺季 11~12 月、淡季 1~10 月")
    assert v[2].startswith("到货前品类会先冲高：按往年规律 12 月是现在的 3.0 倍，按开发周期 4 个月赶不上")
    # 11 月中旬：近 28 天按往年规律本来就比之前 28 天高 → 本期增长有季节因素
    o = S.outlook(prof, date(2026, 11, 20), SEASON_CFG)
    assert o["growth_seasonal"] and S.verdicts(o)[1].startswith("本期增长有季节因素：按往年规律，近 28 天品类本来就是之前 28 天的")


def test_not_every_category_is_seasonal():
    """每年高峰在不同月份（形状不一致）、起伏不大、历史不够 2 年、预测不准：都不算季节性品类，不下季节结论。"""
    from datetime import date
    from radar.detect import seasonality as S

    wandering = lambda y, m, t: 100 * (2.5 if m == 3 + (y % 3) * 4 else 1.0)
    prof = S.profile(_history(wandering), SEASON_CFG)
    assert not prof["seasonal"] and prof["consistency"] < 0.5
    assert S.verdicts(S.outlook(prof, date(2026, 10, 19), SEASON_CFG))[0].startswith("没有固定的季节：每年的起伏不一致")
    flat = S.profile(_history(lambda y, m, t: 100 * (1.1 if m == 12 else 1.0)), SEASON_CFG)
    assert not flat["seasonal"] and "起伏不大" in S.outlook(flat, date(2026, 10, 19), SEASON_CFG)["why"]
    short = S.profile(_history(lambda y, m, t: 100.0, months=20), SEASON_CFG)
    assert not short["seasonal"] and S.verdicts(S.outlook(short, date(2026, 10, 19), SEASON_CFG)) == [
        "类目历史只有 20 个月，还看不出季节"]
    noisy = lambda y, m, t: 100 * {11: 2.0, 12: 3.0}.get(m, 1.0) * (1.3 if (t * 7) % 5 == 0 else 1.0)
    strict = {**SEASON_CFG, "max_error": 0.0}
    prof = S.profile(_history(noisy), strict)
    assert prof["consistency"] > 0.5 and not prof["seasonal"]
    assert "预测不准" in S.outlook(prof, date(2026, 10, 19), strict)["why"]


def test_day_index_is_smooth_between_months():
    from datetime import date
    from radar.detect import seasonality as S

    idx = {m: (2.0 if m == 12 else 1.0) for m in range(1, 13)}
    assert abs(S.day_index(idx, date(2026, 12, 16)) - 2.0) < 0.05
    assert 1.0 < S.day_index(idx, date(2026, 12, 3)) < S.day_index(idx, date(2026, 12, 10)) < 2.0
    assert 1.0 < S.day_index(idx, date(2027, 1, 5)) < 2.0  # 跨年也连续
    prof = {"seasonal": True, "index": idx}
    r = S.expected_ratio(prof, (date(2026, 12, 3), date(2026, 12, 30)), (date(2026, 11, 5), date(2026, 12, 2)))
    assert r > 1.3 and S.expected_ratio({"seasonal": False}, (date(2026, 12, 3), date(2026, 12, 30)),
                                        (date(2026, 11, 5), date(2026, 12, 2))) is None


def test_category_history_backfills_from_spare_budget(cfg):
    """类目历史：近的月份先补，每期最多 limit 次；找齐子类目或翻完所有页就算补完；
    月度发现查到的那个月直接记进来；太早的月份删掉。"""
    from datetime import date
    from radar import category_history as H
    from radar.demo import NODES, SyntheticWorld
    from radar.vendor import Budget, Vendor

    world = SyntheticWorld(date(2026, 10, 5))
    nodes = [{"path": path, "root": root, "name_cn": cn} for root, path, _label, cn, _n in NODES]
    state = {}
    disc = {"period": "202609", "nodes": [{**n, "units": 300.0} for n in nodes]}
    H.record_discovery(state, disc)
    assert H.series(state, nodes[0]["path"]) == {"2026-09": 300.0}
    vendor = Vendor(world, Budget(100), live=False)
    assert H.backfill(vendor, cfg, state, nodes, date(2026, 10, 5), limit=10) == 10
    months = sorted(state[H.STATE_KEY]["months"])
    assert months[-1] == "2026-09" and len(months) == 6  # 9 月已有；每月 2 个根类目各 1 次 → 又补了 5 个月
    H.backfill(vendor, cfg, state, nodes, date(2026, 10, 5), limit=100)
    assert len(state[H.STATE_KEY]["months"]) == 36 and vendor.billable_calls == 10 + 60
    assert H.backfill(vendor, cfg, state, nodes, date(2026, 10, 5), limit=100) == 0  # 都齐了，不再调用
    H.backfill(vendor, cfg, state, nodes, date(2026, 12, 5), limit=100)
    assert "2023-10" not in state[H.STATE_KEY]["months"] and "2026-11" in state[H.STATE_KEY]["months"]


def test_package_fields_are_parsed_from_product_rows():
    """卖家精灵的包装尺寸 / 包装重量 / FBA 配送费：解析成英寸、磅、美元，体积换算成立方英尺和立方米。"""
    from radar import discovery, shipping
    from tests.conftest import fixture

    row = fixture("product_research")["data"]["items"][0]
    item = discovery.normalize_product(row, {"path": "x", "name": "Buffets & Sideboards"}, "top", 1)
    assert item["pkg"] == {"dims": [38.4, 19.3, 9.8], "weight": 82.9, "fba": 62.43}
    assert shipping.cubic_feet(item["pkg"]["dims"]) == 4.2
    assert shipping.parse_weight("12 Ounces") == 0.75
    assert shipping.parse_dims("100 x 50 x 20 cm") == [39.37, 19.69, 7.87]
    assert shipping.parse_dims("15.7 x 32.7 inches") is None
    assert "pkg" not in discovery.normalize_product({"asin": "B0X"}, {"path": "x", "name": "n"}, "top", 1)
    item["price"] = 1019.99
    s = shipping.summarize([item, {"pkg": {"dims": [20, 20, 20], "weight": 30, "fba": 30.0}, "price": 100}])
    assert s["n"] == 2 and s["ft3"] == 4.4 and s["weight"] == 56.5 and s["fba"] == 46.22
    assert shipping.text(item["pkg"], item["price"]) == "包装 4.2 立方英尺（0.12 m³） · 82.9 磅 · FBA $62.43（售价的 6%）"


def test_vision_tags_untracked_heads_and_prunes_them(cfg, monkeypatch):
    """同子类目头部商品不在追踪里：识别结果存在 state[vision.EXTRA_KEY]，不在月度名单里的会被删掉。"""
    monkeypatch.setattr(vision, "_describe_with_retry",
                        lambda client, cfg, key, url: {"tags": {"颜色": ["黑色"]}, "summary": ""})
    state = {"asins": {}}
    rows = [{"asin": "H1", "image": "https://img/1.jpg"}, {"asin": "H2", "image": ""}]
    assert vision.tag_items(rows, state, cfg, "", client=httpx.Client()) == 0  # 没有 key 不识别
    assert vision.tag_items(rows, state, cfg, "k", client=httpx.Client()) == 0  # 不在追踪里，默认跳过
    assert vision.tag_items(rows, state, cfg, "k", client=httpx.Client(), untracked=True) == 1
    assert vision.of(state, "H1")["tags"] == {"颜色": ["黑色"]} and "H2" not in state[vision.EXTRA_KEY]
    vision.prune_extra(state, {"H9"})
    assert vision.of(state, "H1") == {}


def test_appearance_needs_two_brands_and_keywords_drop_brand_names(cfg):
    """“比对照组更多”的特征要来自至少 2 个品牌；标题高频词不出现品牌名。"""
    t = lambda label, n, brands: {"label": label, "count": n, "share": 0.3, "baseline_share": 0.05, "brands": brands}
    tr = {"design": {"工艺": [dict(t("凹槽竖纹", 3, 1), lift=4.0), dict(t("玻璃门", 3, 2), lift=4.0)]},
          "title_shares": {"工艺": {"凹槽竖纹": t("凹槽竖纹", 3, 1), "玻璃门": t("玻璃门", 3, 2)}}}
    out = traits.merge_appearance(tr, {}, cfg)
    assert [f["label"] for f in out["工艺"]["more"]] == ["玻璃门"]  # 凹槽竖纹只有一个品牌
    assert "vasagle" not in traits.title_tokens("VASAGLE Nightstand with Charging Station", "VASAGLE")
    assert "nightstand" in traits.title_tokens("VASAGLE Nightstand with Charging Station", "VASAGLE")


def test_vision_retries_and_skips_a_bad_image(cfg, monkeypatch):
    """连接中断先重试；某张一直失败就跳过它，后面的照常识别；密钥无效才整批停（实测一次中断停掉整批，19 个增长商品没识别）。"""
    monkeypatch.setattr(vision, "RETRY_WAITS", (0, 0))
    gets = {"a": 0, "b": 0, "c": 0}

    def handler(request: httpx.Request):
        if request.method == "GET":
            name = request.url.path.split("/")[-1][0]
            gets[name] += 1
            if name == "b" or (name == "a" and gets["a"] == 1):
                raise httpx.RemoteProtocolError("Server disconnected", request=request)
            return httpx.Response(200, content=b"\xff\xd8fake", headers={"content-type": "image/jpeg"})
        content = '{"风格":["现代"],"材质":[],"造型":[],"工艺":[],"颜色":[],"外观要点":"x"}'
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    state = {"asins": {k: {} for k in ("A", "B", "C")}}
    items = [{"asin": k, "image": f"https://x/{k.lower()}._AC_US600_.jpg"} for k in ("A", "B", "C")]
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert vision.tag_items(items, state, cfg, "key", client=client) == 2
    assert gets == {"a": 2, "b": 3, "c": 1}  # A 第二次成功；B 试了 3 次后跳过；C 照常
    assert "vision" in state["asins"]["A"] and "vision" not in state["asins"]["B"] and "vision" in state["asins"]["C"]

    calls = {"n": 0}

    def unauthorized(request: httpx.Request):
        calls["n"] += 1
        if request.method == "GET":
            return httpx.Response(200, content=b"\xff\xd8fake", headers={"content-type": "image/jpeg"})
        return httpx.Response(401, json={"error": "bad key"})

    fresh = {"asins": {k: {} for k in ("A", "B", "C")}}
    auth_client = httpx.Client(transport=httpx.MockTransport(unauthorized))
    assert vision.tag_items(items, fresh, cfg, "key", client=auth_client) == 0
    assert calls["n"] == 2  # 第一张：取图 + 识别 401，之后不再继续


def test_risk_bullets_must_name_a_flagged_product():
    """风险提醒只能点名带异常信号、或标签有季节性 / 降价驱动的商品；模型自己推测的删掉，删完写“没有需要特别提醒的风险”。"""
    from radar import narrative

    facts = {"异常信号": [{"品牌": "COMHOMA", "标签": []}],
             "突然爆火": [{"品牌": "TUTOTAK", "标签": [], "异常信号": ["评论集中"]}, {"品牌": "VASAGLE", "标签": ["稳在高位"]}],
             "潜力": [{"品牌": "OOOK", "标签": ["季节性"]}]}
    text = ("### 开发方向\n- 开发书架：现代\n### 风险提醒\n- TUTOTAK 书架评论集中，疑似异常，需人工核实。\n"
            "- COMHOMA 梳妆台留评率异常，需人工核实。\n- 部分增长商品带充电/插座，需留意是否为短期促销。\n"
            "- VASAGLE 咖啡桌涨得太快。\n- OOOK 旋转书架有季节性，不要当成长期需求。")
    out = narrative.guard_risks(text, facts)
    assert out.splitlines() == ["### 开发方向", "- 开发书架：现代", "### 风险提醒",
                                "- TUTOTAK 书架评论集中，疑似异常，需人工核实。", "- COMHOMA 梳妆台留评率异常，需人工核实。",
                                "- OOOK 旋转书架有季节性，不要当成长期需求。"]
    only_guess = "### 风险提醒\n- 部分增长商品带 LED 灯，需留意季节性。"
    assert narrative.guard_risks(only_guess, facts).splitlines() == ["### 风险提醒", narrative.NO_RISK]


def test_summary_product_mentions_become_amazon_links():
    """本期要点里点名的商品写成 [品牌 简称](ASIN)，渲染成亚马逊商品链接（悬停看主图）；
    ASIN 不是本期商品、或方括号里没有它的品牌（写错了商品），只保留文字，不会链到别的商品。"""
    from radar import narrative
    from radar.report.render import markdown

    products = {"B0H2XKVQ5L": {"brand": "Huuger", "image": "https://m.media-amazon.com/images/I/x.jpg"}}
    html_text = markdown("### 开发建议\n- 参考 [Huuger 窄长玄关桌](B0H2XKVQ5L) 的插座\n"
                         "- 参考 [VASAGLE 床头柜](B0H2XKVQ5L)\n- 参考 [某品牌 书架](B0ZZZZZZZZ)", products)
    assert html_text.count('href="https://www.amazon.com/dp/B0H2XKVQ5L"') == 1
    assert 'class="peek" src="https://m.media-amazon.com/images/I/x.jpg"' in html_text
    assert "<li>参考 VASAGLE 床头柜</li>" in html_text and "<li>参考 某品牌 书架</li>" in html_text

    item = {"品牌": "Huuger", "品名": "Huuger 63.4-78.3'' Extendable Behind Couch Table", "ASIN": "B0H2XKVQ5L",
            "外观要点": "窄长条案", "标签": [], "可作参考": True, "异常信号": ["留评率高"]}
    facts = {"共性特点": {}, "突然爆火": [item], "潜力": [], "异常信号": [item],
             "开发方向": [{"画像": "玄关桌：窄小省空间", "参考商品": [{"品名": item["品名"], "ASIN": "B0H2XKVQ5L"}]},
                      {"画像": "衣帽架：实木，$24–$120", "子类目": "衣帽架", "增量": "$26 万",
                       "季节提醒": narrative.season_note([
                           "近 28 天季节变化不大：按往年规律，品类是之前 28 天的 1.1 倍",
                           "到货前品类会先冲高：按往年规律 12 月是现在的 2.5 倍，按开发周期 4 个月赶不上"]),
                       "参考商品": []}]}
    text = narrative.template_summary(facts)
    assert "[Huuger 63.4-78.3'' Extendable Behind…](B0H2XKVQ5L)" in text
    assert "- 开发玄关桌：窄小省空间，参考 [Huuger" in text
    assert ("- 开发衣帽架（月增量约 $26 万）：实木，$24–$120；到货前品类会先冲高：按往年规律 12 月是现在的 2.5 倍，"
            "按开发周期 4 个月赶不上\n") in text
    # 类目历史不够、没有固定季节这类结论不写进要点（实测大模型会照抄到每一条后面）
    assert narrative.season_note(["类目历史只有 0 个月，还看不出季节"]) == ""


def test_short_window_shrink_is_fitted_from_data():
    """28 天季节倍数的折扣 k 每期用数据现算：商品实际只跟到季节起伏的一半时（指数之比 e，商品变化 ≈ e^0.5），拟合出 k≈0.5；
    样本不够时用 ratio_shrink。"""
    import calendar
    from datetime import date, timedelta
    from radar.detect import seasonality as S
    from radar.series import Daily

    def history(y0=2023, m0=10, months=36):
        out, y, m = {}, 2026, 9
        for _ in range(months):
            out[f"{y}-{m:02d}"] = 100 * {11: 2.0, 12: 3.0, 1: 1.6}.get(m, 1.0) * calendar.monthrange(y, m)[1]
            m -= 1
            if m == 0:
                y, m = y - 1, 12
        return out

    hist = history()
    prof = S.profile(hist, SEASON_CFG)
    start = date(2025, 9, 1)
    days = 415
    level = [S.day_index(prof["index"], start + timedelta(days=k)) ** 0.5 * 20 for k in range(days)]
    items = [Daily(start, [1000.0] * days, list(level), [100.0] * days) for _ in range(5)]
    cal = S.calibrate({"N": hist}, {"N": items}, date(2026, 10, 19), {**SEASON_CFG, "calibrate_min_pairs": 10})
    assert cal["fitted"] and abs(cal["k"] - 0.5) <= 0.1 and cal["n"] >= 10
    few = S.calibrate({"N": hist}, {"N": items[:3]}, date(2026, 10, 19), {**SEASON_CFG, "ratio_shrink": 0.6})
    assert few == {"k": 0.6, "n": 0, "fitted": False}

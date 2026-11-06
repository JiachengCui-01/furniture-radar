"""外观与工艺：设计词库、主图识别、报告小窗、重新生成。"""
import json
from datetime import datetime, timedelta, timezone

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
    assert summary["dimensions"]["材质"][0] == {"label": "泰迪绒/羊羔绒", "focus": 2, "focus_share": 1.0, "brands": 0,
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
    for kind in ("surge", "potential", "hot", "ocean", "fake", "growth", "all"):
        assert f'data-pop="{kind}"' in plain and f'id="pop-{kind}"' in plain
    n_dirs = plain.count('class="dbox dir"')  # 每个开发方向一个小窗（点一览里的一行弹出）
    assert plain.count('class="pop-src"') == 7 + n_dirs and 'id="pop"' in plain
    # 季节判断已经去掉：没有季节性商品、季节日历，也没有品类季节性标签
    assert "季节" not in plain
    # 主体里不放完整的商品卡片（都在隐藏的小窗内容里），但商品要有图：KPI 缩略图、本期增长商品图墙
    body = plain.split('<div class="pop-src"')[0]
    assert '<article class="card"' not in body and '<article class="card"' in plain
    assert "外观与工艺依据" in body and "增长商品" in body and "本期要点" in body
    assert body.index('class="kpis"') < body.index('id="picks"') < body.index('class="summary"')
    assert 'class="tile"' in body and 'class="kpi-th"><span class="ph">' in body  # 演示数据没有图片地址，用首字母占位
    assert "判定方法与数据口径" not in plain and "名词说明" in body  # 报告上不放大段规则
    assert "凹槽竖纹" in body and "<h4>主材质</h4>" in body  # 演示数据里爆火商品标题都带 Fluted
    assert "Material 属性" in plain and f"每期最多查 {cfg['budget']['material_lookups']} 个" in plain
    # 本期要点是四块图标卡片（不是大段文字），四块都要有、按这个顺序，不是互相替换
    digest = body[body.index('class="summary"'):body.index('<section id="dirs"')]
    heads = ["外观与工艺趋势", "开发方向", "开发建议", "风险提醒"]
    at = [digest.index(f"</svg></span><span>{h}</span>") for h in heads]  # 小标题：图标徽章 + 名字
    assert at == sorted(at)
    assert digest.count('class="ib tn-') >= 4 and "<ul>" not in digest and "<li>" not in digest
    assert digest.count('class="tl"') >= 2 and 'class="fr' in digest and 'class="bl"' in digest  # 趋势卡：每个维度几行特征
    # 开发方向：主体里一行一个子类目（可排序的一览），完整画像在小窗里，每行点开对应的那个
    assert '<section id="dirs"' in body and 'class="dbox dir"' not in body and n_dirs > 0
    assert body.count('class="brow"') == n_dirs
    assert all(f'data-pop="dir{k}"' in body and f'id="pop-dir{k}"' in plain for k in range(n_dirs))
    # 有增长的子类目全部有画像；本期要点放月增量合计占大头的几个（个数随本期变，卡片点开是同一个小窗），标题写明
    n_cards = digest.count('class="dcard"')
    assert 1 <= n_cards <= min(n_dirs, cfg["thresholds"]["directions"]["highlight_max"])
    assert all(f'class="dcard" role="button" tabindex="0" data-pop="dir{k}"' in digest for k in range(n_cards))
    assert f"全部 {n_dirs} 个有增长的品类 ›" in digest and ("本期月增量的" in digest) == (n_cards < n_dirs)
    assert f"个子类目一览：{n_dirs} 个有画像（见上表）" in body and body.count("<td>有（见上表）</td>") == n_dirs
    assert 'class="cards" data-tools' in plain and "data-node=" in plain  # 小窗里能按子类目、类别筛，按月增量、售价排
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
    assert "与上期对比" in plain and 'href="2026-10-05.html"' in plain and "#k=" not in plain  # 往期链接不带密钥


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
                "label": label, "material": material, "price": price, "look": "",
                "recent_avg28": 10.0, "prev_avg28": 5.0}  # 有增长（月增量为正）的子类目才有画像

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
                                                           "share": 0.667, "head_share": None}]}  # 标题 1 个 + 主图 1 个
    assert [f["label"] for f in v["features"]["功能卖点"]["base"]] == ["LED 灯"]  # 充电/插座、抽屉各只有 1 个
    assert v["features"]["颜色"]["base"] == [{"label": "白色", "count": 2, "trend": False, "share": 0.667, "head_share": None}]
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
                "material": "板材", "price": 100.0, "look": "", "fake": {"score": score},
                "recent_avg28": 10.0, "prev_avg28": 5.0}

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


def test_all_qualifying_subcategories_get_a_profile_and_the_rest_say_why(cfg):
    """够格的子类目全部有画像（以前只取前 5 个：10-23 够格的 12 个、前 5 个只占增量的 70%）；一览表把监控范围里的
    每个子类目都列出来，没有画像的写原因；本期要点、钉钉只写增量最大的前 summary_max 个，标题写明还有多少。"""
    from radar.detect import directions
    from radar.report import render
    from radar.notify import dingtalk

    focus = [_dir_item(f"A{k}{j}", f"子类{k}", "Bookshelf", f"B{k}{j}", recent=10 + 10 * (7 - k), prev=5)
             for k in range(7) for j in range(2)]
    focus += [_dir_item("S1", "单品", "Bookshelf", "S", recent=500, prev=5),
              _dir_item("O1", "一家", "Bookshelf", "O", recent=400, prev=5),
              _dir_item("O2", "一家", "Bookshelf", "O", recent=400, prev=5)]
    flat = {**_dir_item("F1", "没增长", "Bookshelf", "F", label="hot"), "tide": 1.08}
    items = focus + [flat]
    disc = {"nodes": [{"name_cn": "子类0"}, {"name_cn": "没增长"}, {"name_cn": "空的"}], "products": {}}

    out = directions.compute(focus, {"asins": {}}, {}, cfg, disc=disc)
    assert [d["node"] for d in out] == [f"子类{k}" for k in range(7)]  # 7 个全列，不截到 5 个
    table = {r["node"]: r for r in directions.overview(items, focus, out, disc, cfg)}
    assert [r for r in table.values() if r["shown"]] == [table[f"子类{k}"] for k in range(7)]
    assert table["单品"]["status"] == "增长商品不到 2 个" and table["单品"]["increment"] == round(495 * 100 * 30)
    assert table["一家"]["status"] == "增长商品只来自 1 个品牌"
    assert table["没增长"] == {"node": "没增长", "n_tracked": 1, "n": 0, "n_brands": 0, "n_surge": 0, "monthly": 0,
                              "increment": 0, "tide": 1.08, "shown": False, "status": "没有增长商品"}
    assert table["空的"]["n_tracked"] == 0 and table["空的"]["status"] == "没有增长商品"
    # 设了 max 时，排在后面的写“增量排在前 N 个之后”
    capped = {**cfg, "thresholds": {**cfg["thresholds"], "directions": {**cfg["thresholds"]["directions"], "max": 3}}}
    few = directions.compute(focus, {"asins": {}}, {}, capped, disc=disc)
    rows = {r["node"]: r for r in directions.overview(items, focus, few, disc, capped)}
    assert len(few) == 3 and rows["子类3"]["status"] == "增量排在前 3 个之后"

    # 本期要点、钉钉放的开发方向个数随本期变：按月增量从大到小，取到累计占全部方向增量的 80%（这里 5 个占 87%）；
    # 卡片点开的是一览里同一个子类目的小窗
    assert [d["node"] for d in directions.highlight(out, cfg)] == [f"子类{k}" for k in range(5)]
    cards, sub = render._dir_cards({"directions": out}, cfg, {"growth": {"shown": []}}, focus, {})
    assert cards.count('class="dcard"') == 5 and 'data-pop="dir4"' in cards and 'data-pop="dir5"' not in cards
    assert "本期月增量的 87% 来自这 5 个品类 · " in sub and "全部 7 个有增长的品类 ›" in sub
    assert "class=\"dnote\"" not in cards  # 没给一览表时不写
    ctx = {"title": "雷达", "report_id": "2026-11-01", "as_of": "2026-10-31", "cfg": cfg, "diff": {"first": True},
           "traits": {"directions": out},
           "sections": {"surge": [], "potential": [], "fake": [], "counts": {"total": 1, "surge": 0, "potential": 0,
                                                                              "hot": 0, "fake": 0}}}
    lines = dingtalk.build_text(ctx).splitlines()
    k = lines.index("**🛠 开发方向**（本期月增量的 87% 来自这 5 个品类，共 7 个见报告）")
    assert [line.split("：")[0] for line in lines[k + 1:] if line.startswith("- 子类")] == [f"- 子类{i}" for i in range(5)]
    # 增量集中在一个品类时就只放它；月增量不是正数的子类目没有画像
    big = [{"node": "大", "sales": {"increment": 900_000}}, {"node": "小", "sales": {"increment": 50_000}},
           {"node": "零", "sales": {"increment": 0}}]
    assert [d["node"] for d in directions.highlight(big, cfg)] == ["大"]
    zero = [_dir_item(f"Z{j}", "零增量", "Bookshelf", f"Z{j}", recent=5, prev=5) for j in range(2)]
    assert "零增量" not in [d["node"] for d in directions.compute(focus + zero, {"asins": {}}, {}, cfg, disc=disc)]
    rows0 = {r["node"]: r for r in directions.overview(items + zero, focus + zero, out, disc, cfg)}
    assert rows0["零增量"]["status"] == "月增量不是正数"
    assert not any("没有画像" in line for line in lines)  # 没给一览表时不写

    # 增量排得进前 5、却画不出画像的子类目也写出来（以前小标题写“增量最大的 5 个”却悄悄跳过：11-06 彩排里增量第一的床架没出现）
    overview = directions.overview(items, focus, out, disc, cfg)
    skipped = directions.skipped_big(overview, out[:5])
    assert [r["node"] for r in skipped] == ["一家", "单品"]  # 增量都比第 5 个大；“没增长”“空的”没有增长商品，不算
    line = directions.skipped_text(skipped, 5)
    assert line == ("增量排得进前 5 个、但没有画像的子类目："
                    f"一家（月增量约 {directions.money_text(table['一家']['increment'])}，增长商品只来自 1 个品牌）；"
                    f"单品（月增量约 {directions.money_text(table['单品']['increment'])}，增长商品不到 2 个）")
    ctx["traits"]["direction_overview"] = overview
    lines = dingtalk.build_text(ctx).splitlines()
    k = lines.index("**🛠 开发方向**（本期月增量的 87% 来自这 5 个品类，共 7 个见报告）")
    assert lines[k + 6] == f"- {line}"
    cards, _sub = render._dir_cards({"directions": out, "direction_overview": overview}, cfg, {"growth": {"shown": []}}, focus, {})
    assert '<p class="dnote">' in cards and render.esc(line) in cards
    assert directions.skipped_big(overview, []) == []


def test_heads_exclude_growth_items_whole_family(cfg):
    """同子类目头部商品不算增长商品自己的父体：增长商品多是从全市场名单进来的（月度名单里没有它这个 ASIN），
    月度名单给的常是同一父体的另一个代表子体。以前只在月度名单里找增长商品的父体，兄弟子体被算成了头部
    （11-06 彩排 175 个增长商品里 16 个这样），“比头部多”的特征被冲淡。"""
    from radar.detect import directions

    grow = {**_dir_item("G1", "书架", "Fluted Bookshelf", "B1"), "parent": "P1"}
    sib = {**_head_row("S1", "书架", "Fluted Bookshelf", 5000), "parent": "P1"}   # 同一父体的另一个子体
    under = {**_head_row("U1", "书架", "Bookshelf", 4000), "parent": "G1"}        # 挂在增长商品这个 ASIN 下面的
    other = _head_row("H1", "书架", "Bookshelf", 3000)
    disc = {"products": {r["asin"]: r for r in (sib, under, other)}}
    assert [r["asin"] for r in directions.heads("书架", disc, [grow], cfg)] == ["H1"]


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
    assert d["compare"] == {"mode": "title", "n_head": 14, "n_head_all": 14, "n_head_brands": 14, "n_head_brands_all": 14}
    assert [f["label"] for f in d["features"]["工艺"]["more"]] == ["凹槽竖纹"]  # 4/4 品牌 vs 头部 1/14
    assert d["features"]["工艺"]["more"][0]["head_share"] == round(1 / 14, 3)
    assert [f["label"] for f in d["features"]["造型"]["more"]] == []
    assert "开放格" in [f["label"] for f in d["features"]["造型"]["base"]]  # 头部也都有
    assert directions.headline(d).startswith("书架：板材，凹槽竖纹")

    # 头部商品有主图识别（≥10 个）：两边都看标题 + 主图，头部只算识别过的
    state = {"asins": {f"G{k}": {"vision": {"tags": {"颜色": ["胡桃色"]}}} for k in range(4)},
             vision.EXTRA_KEY: {r["asin"]: {"vision": {"tags": {"颜色": ["黑色"]}}} for r in heads[:12]}}
    d = directions.compute(focus, state, {}, cfg, disc=disc)[0]
    assert d["compare"] == {"mode": "image", "n_head": 12, "n_head_all": 14, "n_head_brands": 12, "n_head_brands_all": 14}
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


def _ref_item(asin, brand, design=None, tags=(), signals=(), look="正面满布竖向凹槽，配金色拉手。", node="五斗橱", seen=None,
              label="surge"):
    return {"asin": asin, "brand": brand, "title": f"{brand} Dresser", "node_cn": node, "node_name": "", "image": "",
            "look": look, "tags": list(tags), "design": design or {}, "seen": design if seen is None else seen, "label": label,
            "fake": {"score": 4 if label == "fake" else (2 if signals else 0), "signals": [{"text": t} for t in signals]}}


def _tip_ctx(cfg, surge=(), potential=(), fake=(), hot=()):
    """本期要点用到的最少数据：增长商品更多的特征（凹槽竖纹主图 +18、原木色主图 +13；现代只在标题里多，主图 80% vs 80%；
    双色拼接只是常见）和各板块。"""
    def feat(label, focus, ref, title=None):
        out = {"label": label, "image": {"label": label, "focus": focus, "focus_share": focus / 50,
                                         "reference": ref, "reference_share": ref / 40}}
        if title:
            out["title"] = {"label": label, "count": 16, "share": title[0], "brands": 16, "baseline_share": title[1]}
        return out
    traits = {"vision": {"n_focus": 50, "n_reference": 40}, "appearance": {
        "风格": {"more": [feat("现代", 40, 32, (0.20, 0.14))]},
        "工艺": {"more": [feat("凹槽竖纹", 19, 8)], "common": [feat("双色拼接", 14, 10)]},
        "颜色": {"more": [feat("原木色", 14, 6)]}}}
    return {"traits": traits, "cfg": cfg,
            "sections": {"surge": list(surge), "potential": list(potential), "fake": list(fake), "hot": list(hot)}}


def test_risk_reminders_list_every_risk_factor_of_listed_products(cfg):
    """风险提醒按类列出报告里列出的全部商品身上的每一类风险因素（不只是降价驱动）：异常信号（和 KPI 同一批）、有可疑迹象
    （有信号但没到门槛）、降价驱动、数据可能有误、对比基准不足、老链接 / 断货恢复、大促 / 待修订、势头转弱、随大盘、只有月度数据；
    红色标签归不进这些的写在“其他需要留意”。一个商品可以在几类里；有风险的商品不当开发建议的参考。"""
    from radar import narrative
    from radar.report import render

    flagged = _ref_item("B0FAKE0001", "COMHOMA", signals=["留评率 22%，是子类目中位数的 7 倍"], label="fake")
    burst = _ref_item("B0SIG00001", "TUTOTAK", signals=["评论集中在 3 天"])
    cut = _ref_item("B0CUT00001", "OOOK", tags=["稳在高位", "降价驱动"])
    gap = _ref_item("B0GAP00001", "Huuger", tags=["最近 28 天有 6 天没有日数据（断货或数据缺失，判断可能不准）",
                                                   "对比期只有 3 天在卖（之前 28 天里其余日子没在卖）"], label="potential")
    monthly = _ref_item("B0MON00001", "VASAGLE", tags=["只有月度数据（不在追踪池：没有走势图，不判回落）"], label="hot")
    odd = _ref_item("B0ODD00001", "DUMOS", tags=["评分 3.9 低于 4.0"], label="hot")
    fine = _ref_item("B0FINE0001", "SONGMICS", tags=["稳在高位"], design={"工艺": ["凹槽竖纹"]})
    ctx = _tip_ctx(cfg, [burst, cut, fine], [gap], fake=[flagged], hot=[monthly, odd])
    groups = narrative.risks(ctx)
    assert [(g["kind"], [i["asin"] for i, _w, _t in g["items"]]) for g in groups] == [
        ("fake", ["B0FAKE0001"]), ("suspect", ["B0SIG00001"]), ("price", ["B0CUT00001"]), ("data", ["B0GAP00001"]),
        ("base", ["B0GAP00001"]), ("monthly", ["B0MON00001"]), ("other", ["B0ODD00001"])]
    assert groups[0]["label"] == "异常信号" and groups[1]["label"] == "有可疑迹象"  # 没到门槛的不叫异常信号（KPI 是 0 时这里也是 0）
    assert groups[3]["items"][0][2].startswith("最近 28 天有 6 天没有日数据（") and groups[3]["items"][0][1] == "potential"
    assert [i["asin"] for i in narrative.reference_items(ctx)] == ["B0FINE0001"]
    assert narrative.risks(_tip_ctx(cfg, [fine])) == []
    for tag in ("老品换链接（上架 38 天已有 76 条评论）", "大促拉动（去掉大促日后不达标）", "最近回落（近 7 天 ×0.6）",
                "随大盘（所在子类目整体 ×1.12）", "数据陈旧"):
        assert narrative.risk_groups_of({"tags": [tag]}), tag

    html_text = render._risk_rows(groups)
    assert html_text.count('class="rkg"') == 7 and "COMHOMA 五斗橱" in html_text  # 一类商品少时写品名
    assert 'data-tip="Huuger 五斗橱 · 潜力 · 最近 28 天有 6 天没有日数据（断货或数据缺失，判断可能不准）"' in html_text
    assert "列出的商品都没有风险标签" in render._risk_rows([])


def test_development_tips_from_the_model_are_checked_line_by_line(cfg):
    """开发建议逐条核对：特征必须是本期增长商品更多、而且主图上也多出来的（tip_labels，“现代”只在标题里多，不用），
    参考商品的主图上要看得出（item["seen"]，标题里有、图上看不出的不算）；商品必须是列出的、没有风险标签的爆火 / 潜力，
    方括号里写着它的品牌；同一个商品只用一次。外观细节不用模型写的，取这张主图的外观要点里说到这些特征的句子（配图和说明对得上）。
    一条都不剩、或没有大模型时按数据选（template_tips）。"""
    from radar import narrative
    from radar.report import render

    a = _ref_item("B0AAAAAAA1", "TEENFON", design={"工艺": ["凹槽竖纹"], "颜色": ["原木色"]})
    b = _ref_item("B0BBBBBBB1", "Wayshome", design={"颜色": ["原木色"], "工艺": ["凹槽竖纹"], "风格": ["现代"]},
                  seen={"颜色": ["原木色"], "风格": ["现代"]}, look="外八锥形腿，原木纹理清爽简洁。")
    cut = _ref_item("B0CUT00001", "OOOK", tags=["降价驱动"], design={"工艺": ["凹槽竖纹"]})
    ctx = _tip_ctx(cfg, [a, b, cut])
    assert narrative.tip_labels(ctx) == ["凹槽竖纹", "原木色"]
    text = "\n".join([
        "- 凹槽竖纹 + 原木色｜[TEENFON 六抽柜](B0AAAAAAA1)｜模型写的细节不用",
        "- 原木色｜[TEENFON 六抽柜](B0AAAAAAA1)",                       # 同一个商品第二次
        "- 原木色｜[VASAGLE 五斗柜](B0BBBBBBB1)",                       # 方括号里不是它的品牌
        "- 现代｜[Wayshome 五斗柜](B0BBBBBBB1)",                        # 只在标题里多的特征，不用
        "- 凹槽竖纹 + 原木色｜[Wayshome 五斗柜](B0BBBBBBB1)",          # 主图上看不出凹槽竖纹：只留原木色
        "- 凹槽竖纹｜[OOOK 书柜](B0CUT00001)",                          # 降价驱动：不能当参考
        "- 凹槽竖纹｜[某品牌 书柜](B0ZZZZZZZZ)",                         # 不是本期商品
        "开发建议：多做凹槽竖纹",                                        # 格式不对
    ])
    tips = narrative.parse_tips(text, ctx)
    assert tips == [
        {"features": ["凹槽竖纹", "原木色"], "asin": "B0AAAAAAA1", "name": "TEENFON 六抽柜", "detail": "正面满布竖向凹槽"},
        {"features": ["原木色"], "asin": "B0BBBBBBB1", "name": "Wayshome 五斗柜", "detail": "原木纹理清爽简洁"}]
    assert narrative.parse_tips("无", ctx) == []

    tips, source = narrative.tips(ctx, cfg, "")  # 没有大模型：同样的规则从数据里选，品名 = 品牌 + 子类目
    assert source == "template" and [(t["asin"], t["features"], t["name"]) for t in tips] == [
        ("B0AAAAAAA1", ["凹槽竖纹", "原木色"], "TEENFON 五斗橱"), ("B0BBBBBBB1", ["原木色"], "Wayshome 五斗橱")]
    html_text = render._ideas(tips, {})
    assert html_text.count('class="idea"') == 2 and 'href="https://www.amazon.com/dp/B0AAAAAAA1"' in html_text
    assert "<span>凹槽竖纹</span><i>+</i><span>原木色</span>" in html_text


def test_product_short_names_use_checked_subcategory_names_and_room(cfg):
    """子类目中文名按 scope.node_names 改掉卖家精灵的翻译（Storage Cabinets 不是“门厅收纳柜”）；用途不固定的子类目
    （收纳柜）短称后面写标题里最先说到的房间，图是浴室柜、字就写浴室。改名不算换监控范围。"""
    from radar import discovery, narrative

    path = "1055398:1063306:16543322011:681151011"
    state = {"discovery": {"nodes": [{"path": path, "name": "Storage Cabinets", "name_cn": "门厅收纳柜"}]},
             "asins": {"B0DHZTH4D6": {"node": path, "node_cn": "门厅收纳柜", "title": "x"}},
             "universe": {"rows": {"B0X": {"node": path, "node_cn": "门厅收纳柜"}}}}
    key = discovery.scope_key(cfg)
    assert discovery.rename_nodes(state, cfg) == 3 and discovery.scope_key(cfg) == key
    assert state["discovery"]["nodes"][0]["name_cn"] == "收纳柜" and state["universe"]["rows"]["B0X"]["node_cn"] == "收纳柜"
    title = "Bathroom Wall Cabinet Medicine Cabinet Farmhouse Sliding Barn Door for Toilet Kitchen Living Room"
    assert discovery.room_node(path, cfg) and narrative.room_of(title) == "浴室"
    item = {"asin": "B0DHZTH4D6", "brand": "Minifeel", "node_cn": "收纳柜", "room": narrative.room_of(title)}
    assert narrative.cn_name(item) == "Minifeel 收纳柜 · 浴室"
    names = cfg["scope"]["node_names"]
    assert names["3733641"] == "边几、角几" and names["3733631"] == "茶几" and names["490876011"] == "图纸柜"


def test_trend_tiles_cover_every_dimension_with_market_value(cfg):
    """趋势卡：主材质和每个维度一张，每张列几个特征（▲ 比对照明显多 / ▼ 明显少 / 常见的也列），写增长商品 vs 对照的占比
    和带这个特征的增长商品月增量；主图上看得出这个特征的商品才配图、这一行才点得开（小窗只看这些商品）。不写“未注明”。"""
    from radar.report import render

    tr = _tip_ctx(cfg)["traits"]
    tr["materials"] = {"n_focus": 79, "n_reference": 40, "rows": [
        {"label": "铁木（金属+木）", "count": 28, "share": 0.354, "reference": 15, "reference_share": 0.375},
        {"label": "实木", "count": 23, "share": 0.291, "reference": 3, "reference_share": 0.075},
        {"label": "金属", "count": 7, "share": 0.089, "reference": 8, "reference_share": 0.2},
        {"label": "未注明", "count": 1, "share": 0.013, "reference": 0, "reference_share": 0.0}]}
    f1 = {**_dir_item("F1", "五斗橱", "Fluted Dresser", "A", recent=15, prev=5), "material": "实木",
          "design": {"工艺": ["凹槽竖纹"]}, "seen": {"工艺": ["凹槽竖纹"]}}
    f2 = {**_dir_item("F2", "五斗橱", "Oak Dresser", "B", recent=25, prev=5), "material": "铁木（金属+木）",
          "design": {"颜色": ["原木色"]}}  # 原木色只在标题里：不配图、不可点
    html_text = render._trend_tiles(tr, cfg, [f1, f2], [f1])
    assert html_text.count('class="tl"') == 4  # 主材质 + 风格 + 工艺 + 颜色
    assert '<span class="fl">实木<i class="up">▲</i></span>' in html_text and '<span class="fl">金属<i class="dn">▼</i></span>' in html_text
    assert '<span class="fl">铁木</span>' in html_text and "未注明" not in html_text
    assert "带「实木」的增长商品，月增量合计 $3 万" in html_text and "带「原木色」的增长商品，月增量合计 $6 万" in html_text
    assert 'data-pop="growth" data-filter="凹槽竖纹"' in html_text and 'data-filter="原木色"' not in html_text
    assert 'data-filter="材质:实木"' in html_text and "<em>凹槽竖纹</em>" in html_text  # 配图下面写是哪个特征
    assert '<span class="fl">双色拼接</span>' in html_text  # 常见的也列（不标 ▲）
    assert "暂不总结" in render._trend_tiles({"note": "本期增长商品太少（少于 3 个），暂不总结共性特点"}, cfg, [], [])
    assert 'data-tags="凹槽竖纹|材质:实木"' in render.card({**f1, "tags": [], "fake": {"score": 0, "signals": []},
                                                          "monthly": [], "spark": []}, "rising")


def test_colors_count_only_the_main_color(cfg):
    """颜色只按主色归类（一件家具只有一个主体颜色）：识别结果写了“主色”就用它；以前的结果没有这一项，取外观要点里最先说到的颜色。
    原木色柜子配黑色滑轨不算黑色——统计、配图、点“黑色”看商品都按这个（11-06 用户反馈）。造型、工艺这些照样可以有几个。"""
    st = {"asins": {
        "M": {"vision": {"image": "x", "tags": {"颜色": ["原木色", "黑色"], "工艺": ["凹槽竖纹", "黑色金属件"]},
                         "summary": "原木色马厩门配黑色金属滑轨，跨马桶置物设计。"}},
        "D": {"vision": {"image": "y", "tags": {"颜色": ["黑色", "棕色"]}, "summary": "黑色金属框架配深色木纹台面",
                         "main_color": "棕色"}}}}
    assert vision.of(st, "M")["tags"]["颜色"] == ["原木色"] and vision.of(st, "M")["tags"]["工艺"] == ["凹槽竖纹", "黑色金属件"]
    assert vision.of(st, "D")["tags"]["颜色"] == ["棕色"]
    assert st["asins"]["M"]["vision"]["tags"]["颜色"] == ["原木色", "黑色"]  # 存下的识别结果原样不动
    rows = vision.summarize([{"asin": "M"}, {"asin": "D"}], [], st)["dimensions"]["颜色"]
    assert "黑色" not in [r["label"] for r in rows]
    assert "“主色”只写一个" in vision.PROMPT
    assert vision._parse('{"颜色":["白色","黑色"],"主色":"白色","外观要点":"白色柜"}')["main_color"] == "白色"

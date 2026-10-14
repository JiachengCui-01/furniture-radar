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
    for kind in ("surge", "potential", "hot", "fake", "growth", "all"):
        assert f'data-pop="{kind}"' in plain and f'id="pop-{kind}"' in plain
    assert plain.count('class="pop-src"') == 6 and 'id="pop"' in plain
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
    vision = {"n_focus": 20, "n_reference": 10, "dimensions": {
        "风格": [i("现代", 16, 9), i("农舍风", 3, 0)],
        "工艺": [i("锥形/外八腿", 6, 1)],
        "颜色": [i("黑色", 11, 8)]}}
    out = traits.merge_appearance(tr, vision, cfg)
    assert [f["label"] for f in out["工艺"]["more"]] == ["锥形/外八腿", "凹槽竖纹"]  # 30% − 10% 正好 20 个百分点
    assert [f["label"] for f in out["风格"]["more"]] == ["农舍风"]  # 现代：标题说更多、主图说更少 → 冲突
    assert [f["label"] for f in out["风格"]["common"]] == ["现代"]
    assert out["颜色"]["more"] == [] and [f["label"] for f in out["颜色"]["common"]] == ["黑色"]



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
    assert v["features"]["工艺"] == [{"label": "玻璃门", "count": 2, "trend": True}]  # 标题 1 个 + 主图 1 个
    assert [f["label"] for f in v["features"]["功能卖点"]] == ["LED 灯"]  # 充电/插座、抽屉各只有 1 个
    assert v["features"]["颜色"] == [{"label": "白色", "count": 2, "trend": False}]
    assert v["price"] == {"min": 160, "max": 228, "median": 170}
    assert [r["asin"] for r in v["refs"]] == ["V1", "V2"]
    assert directions.headline(v) == "梳妆台：板材，玻璃门，白色，LED 灯，$160–$228"
    assert [f["label"] for f in out[1]["features"]["造型"]] == ["窄小省空间"] and out[1]["materials"] == [
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
    assert v["features"]["颜色"] == [] and v["features"]["功能卖点"] == []  # 白色、LED 只有 FIONESO 一家有
    assert v["materials"] == [{"label": "板材", "count": 2}]
    shelf = out[1]
    assert [f["label"] for f in shelf["features"]["造型"]] == ["窄小省空间"]
    assert [r["asin"] for r in shelf["refs"]] == ["S2"]  # S1 有异常信号；S3 和 S2 同一品牌


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

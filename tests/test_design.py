"""外观与工艺：设计词库、主图识别、报告小窗、重新生成。"""
import json
from datetime import datetime, timedelta, timezone

import httpx

from radar import crypto, pipeline, store, vision
from radar.cli import _decrypt_page
from radar.config import Secrets
from radar.demo import SyntheticWorld
from radar.detect import design


def test_extract_reads_appearance_and_craft_from_titles():
    f = design.extract("Mid-Century Modern Boucle Curved Sofa with Fluted Base, Cream, Channel Tufted Back, Storage")
    assert {"中古风", "现代"} <= f["风格"]
    assert f["材质"] == {"泰迪绒/羊羔绒"}
    assert f["造型"] == {"弧形曲线"}
    assert {"凹槽竖纹", "竖条拉扣"} <= f["工艺"] and "拉扣软包" not in f["工艺"]
    assert f["颜色"] == {"奶油/米色"}
    assert f["功能卖点"] == {"带储物/抽屉"}


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
    assert summary["dimensions"]["材质"][0] == {"label": "泰迪绒/羊羔绒", "focus": 2, "focus_share": 1.0,
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
    cfg["pool"]["explore_per_run"] = 400
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
    for kind in ("surge", "potential", "hot", "fake"):
        assert f'data-pop="{kind}"' in plain
    assert plain.count('class="pop-src"') == 4 and 'id="pop"' in plain
    assert "爆火产品的外观与工艺特征" in plain and "外观与工艺（来自商品标题）" in plain
    assert "凹槽竖纹" in plain and "主材质构成" in plain  # 演示数据里爆火商品标题都带 Fluted


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

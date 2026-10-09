"""端到端：用模拟世界连续跑两期，检查报告、加密、对比、预算、日志脱敏和钉钉推送。"""
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from radar import crypto, pipeline, store
from radar.cli import TRACK_ALL, _decrypt_page
from radar.config import Secrets
from radar.demo import SyntheticWorld

START = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)


@pytest.fixture
def world_run(cfg, master_key, tmp_path):
    cfg["budget"].update(stable_refresh_every=1, per_run=400, bootstrap_run=400)
    cfg["pool"].update(TRACK_ALL)
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    site = tmp_path / "site"
    first = pipeline.run(cfg, secrets, site, transport=world, now=START)
    world.advance(3)
    second = pipeline.run(cfg, secrets, site, transport=world, now=START + timedelta(days=3))
    return world, secrets, site, first, second


def _labels(site, master_key, world):
    state = store.load(site, crypto.parse_master_key(master_key))
    kinds = {p.asin: p.kind for p in world.products}
    by_kind = {}
    for asin, (label, _score) in state["runs"][-1]["labels"].items():
        by_kind.setdefault(kinds[asin], set()).add(label)
    return state, by_kind


def test_two_runs_classify_each_archetype(world_run, master_key):
    world, _secrets, site, first, second = world_run
    assert first.published and second.published and second.report_id == "2026-10-08"
    state, by_kind = _labels(site, master_key, world)
    assert by_kind["surge"] == {"surge"}
    assert by_kind["potential"] == {"potential"}
    assert by_kind["fake_new"] == {"fake"}
    assert by_kind["review_burst"] == {"fake"}           # 第二期才暴露的刷评
    assert "fake" not in by_kind["merge"]                # 变体合并不算刷评
    assert by_kind["steady"] <= {"hot", "watch"} and "hot" in by_kind["steady"]
    assert "surge" not in by_kind.get("noise", set())
    assert "upholstered" not in by_kind                  # 软包床：商品级剔除
    out_of_scope = {p.asin for p in world.products if p.cn in ("沙发", "办公椅")}
    assert not out_of_scope & set(state["runs"][-1]["labels"])  # 沙发、办公椅不在范围内
    merge = next(r for a, r in state["asins"].items() if world.by_asin[a].kind == "merge")
    assert merge["checks"]["detail"]["parent"] == "B0MERGED01"


def test_every_listed_item_matches_its_definition(world_run, master_key, cfg):
    """每个上榜商品都必须符合报告里写的定义，概念之间不能互相矛盾。"""
    from radar import analyze, clock, tracking
    from radar.detect.rules import low_rating, momentum
    from radar.series import Daily

    _world, _secrets, site, _first, _second = world_run
    state = store.load(site, crypto.parse_master_key(master_key))
    last = state["runs"][-1]
    items = analyze.analyze(state, state["discovery"], list(last["labels"]), cfg, clock.parse_day(last["date"]),
                            tracking.fresh_rows(state))["items"]
    th, s = cfg["thresholds"], cfg["thresholds"]["surge"]
    checked = set()
    for item in items:
        if item["label"] not in ("surge", "potential", "hot"):
            continue
        checked.add(item["label"])
        assert item["fake"]["score"] < th["fake"]["suspect_score"]
        assert not low_rating(item["rating"], item["ratings"], th)
        d = Daily.from_record(state["asins"][item["asin"]]["series"])
        mom = momentum(d, d.index_of(d.as_of()), th["momentum"])
        if item["label"] in ("surge", "potential"):
            assert not mom["fading"], item["asin"]
        if item["label"] == "surge":
            g = item["surge"]
            assert g["recent_avg"] >= s["min_daily_sales"] and (g["from_zero"] or g["sales_ratio"] >= s["sales_ratio"])
            kind = g["kind"]
            if kind == "持续型":
                assert g["days_elevated"] >= s["sustained_days"] and abs(g["last3_ratio"] - 1) <= s["steady_band"]
            elif kind == "爬升型":
                assert g["week_ratio"] >= s["climb_ratio"] and g["last3_ratio"] > 1
            elif kind == "脉冲型":
                assert g["top2_share"] >= s["pulse_share"]
            else:
                assert kind == "新品爆发" and g["from_zero"]
            assert "持续热销" not in item["tags"]  # 同时符合热销条件的写“长期头部”，不和“突然爆火”矛盾
        if item["label"] == "potential":
            p = item["potential"]
            assert th["potential"]["min_age_days"] <= p["age_days"] <= th["potential"]["max_age_days"]
            assert p["recent_ratio"] >= th["potential"]["min_recent_ratio"]
        if item["label"] == "hot":
            h = item["hot"]
            assert h["months_hit"] >= th["hot"]["months_required"] and h["cv"] <= th["hot"]["max_cv"]
    assert checked == {"surge", "potential", "hot"}


def test_report_is_encrypted_and_decryptable(world_run, master_key):
    _world, _secrets, site, _first, second = world_run
    page = (site / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8")
    assert "B0DEMO" not in page and "突然爆火" not in page   # 公开文件里只有密文
    plain = _decrypt_page(page, crypto.parse_master_key(master_key))
    assert "突然爆火" in plain and "B0DEMO" in plain and "与上期对比" in plain
    assert "2026-10-05.html#k=" in plain                    # 往期报告链接自带密钥
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "2026-10-08" in index and "B0DEMO" not in index
    assert b"B0DEMO" not in (site / "data" / "state.enc").read_bytes()
    assert second.url.startswith("https://demo.github.io/radar/reports/2026-10-08.html#k=")


def test_not_due_is_skipped(cfg, master_key, tmp_path):
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START)
    calls = len(world.calls)
    result = pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=1))
    assert not result.published and "未满" in result.skipped_reason and len(world.calls) == calls


def test_budget_is_respected(cfg, master_key, tmp_path):
    world = SyntheticWorld(START.date())
    result = pipeline.run(cfg, Secrets(report_key=master_key), tmp_path, transport=world, now=START, budget=25)
    assert result.published and result.calls <= 25 and len(world.calls) <= 25


def test_unfinished_discovery_resumes_next_run(cfg, master_key, tmp_path):
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START, budget=4)
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["discovery"]["complete"] is False
    world.advance(3)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=3), budget=60)
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["discovery"]["complete"] is True and state["discovery"]["period"] == "202609"


def test_ci_logs_never_contain_product_data(cfg, master_key, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    world = SyntheticWorld(START.date())
    result = pipeline.run(cfg, Secrets(report_key=master_key), tmp_path, transport=world, now=START)
    out = capsys.readouterr()
    text = out.out + out.err
    key = result.url.split("#k=")[1]
    assert "B0DEMO" not in text and "Fluted" not in text
    assert f"::add-mask::{key}" in text and text.count(key) == 1  # 只出现在 mask 指令里


def test_send_last_posts_action_card(world_run, cfg):
    _world, secrets, site, _first, second = world_run
    secrets.dingtalk_webhooks = ["https://oapi.dingtalk.com/robot/send?access_token=t"]
    secrets.dingtalk_secrets = ["SECabc"]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    results = pipeline.send_last(cfg, secrets, site, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert results == [True]
    card = sent[0]["actionCard"]
    assert "突然爆火" in card["text"] and "2026-10-08" in card["title"]
    assert "%23k%3D" in card["singleURL"]  # 密钥片段被完整编码进按钮链接


def test_recent_lists_and_similar_feed_the_pool(cfg, master_key, tmp_path):
    """默认名额下：近 30 天榜单每期刷新并带进追踪池；第二期用上期爆款查相似款。"""
    cfg["discovery"]["risers_min_units"] = 100
    cfg["pool"]["opportunity_min_units"] = 50  # 模拟世界里的商品销量偏小
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START)
    master = crypto.parse_master_key(master_key)
    state = store.load(tmp_path, master)
    risers = state["risers"]["items"]
    assert risers and state["risers"]["run"] == 1
    assert all(r["node"] != "1055398:1063306:1063318:3733551" for r in risers)  # 被排除的沙发不会混进来
    assert set(state["runs"][-1]["labels"]) & {r["asin"] for r in risers}
    assert "asin_competitor" not in world.calls  # 第一期还没有爆款作种子

    world.advance(3)
    before = len(world.calls)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=3))
    state = store.load(tmp_path, master)
    assert state["risers"]["run"] == 2 and "asin_competitor" in world.calls[before:]
    similar = {p["asin"] for p in state["similar"]["items"]}
    assert similar and any(state["asins"][a].get("pool_reason") == "similar" for a in similar if a in state["asins"])
    assert len(state["runs"][-1]["labels"]) <= cfg["pool"]["max_size"]


def test_scope_change_rediscovers_without_cross_scope_diff(cfg, master_key, tmp_path):
    """换了监控范围：本期立即重新发现（不等下个月），也不和旧范围的上一期做对比。"""
    import copy

    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START)
    master = crypto.parse_master_key(master_key)
    old_key = store.load(tmp_path, master)["discovery"]["scope_key"]

    narrower = copy.deepcopy(cfg)
    narrower["scope"]["max_nodes"] = 3
    assert pipeline.needs_discovery(store.load(tmp_path, master), narrower, START.date())
    world.advance(3)
    before = len(world.calls)
    second = pipeline.run(narrower, secrets, tmp_path, transport=world, now=START + timedelta(days=3))

    state = store.load(tmp_path, master)
    assert state["discovery"]["scope_key"] != old_key and len(state["discovery"]["nodes"]) == 3
    assert "market_research" in world.calls[before:]
    plain = _decrypt_page((tmp_path / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8"), master)
    assert "监控范围刚调整" in plain

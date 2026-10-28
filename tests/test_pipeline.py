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
    from radar.series import Daily, closed_months

    _world, _secrets, site, _first, _second = world_run
    state = store.load(site, crypto.parse_master_key(master_key))
    last = state["runs"][-1]
    result = analyze.analyze(state, state["discovery"], list(last["labels"]), cfg, clock.parse_day(last["date"]),
                             tracking.fresh_rows(state))
    items, event_days = result["items"], result["events"]["days"]
    th, s = cfg["thresholds"], cfg["thresholds"]["surge"]
    checked = set()
    for item in items:
        if item["label"] not in ("surge", "potential", "hot") or not item.get("daily", True):
            continue  # 月度名单补判的另有测试（test_untracked_heads_are_judged_from_monthly_lists）
        checked.add(item["label"])
        assert item["fake"]["score"] < th["fake"]["suspect_score"]
        assert not low_rating(item["rating"], item["ratings"], th)
        full = Daily.from_record(state["asins"][item["asin"]]["series"])
        d = full.without(event_days)  # 判定用的是去掉大促日的日数据
        mom = momentum(d, full.index_of(full.as_of()), th["momentum"])
        if item["label"] in ("surge", "potential"):
            assert not mom["fading"], item["asin"]
        if item["label"] == "surge":
            g = item["surge"]
            assert g["recent_avg"] >= s["min_daily_sales"] and (g["from_zero"] or g["sales_ratio"] >= s["sales_ratio"])
            kind = g["kind"]
            assert kind == "新品爆发" or not g["pulse"]  # 短时脉冲不算爆火
            if kind == "稳在高位":
                assert g["days_elevated"] >= s["sustained_days"] and abs(g["last3_ratio"] - 1) <= s["steady_band"]
            elif kind == "仍在上涨":
                assert g["week_ratio"] >= s["climb_ratio"] and g["last3_ratio"] > 1
            else:
                assert kind in ("新品爆发", "老链接重新起量") and g["from_zero"] and not item["relist"]
                old = item["age_days"] is None or item["age_days"] > s["new_listing_max_age_days"]
                assert (kind == "老链接重新起量") == old  # 上架 ≤180 天的才叫新品
            assert "持续热销" not in item["tags"]  # 同时符合热销条件的写“长期头部”，不和“突然爆火”矛盾
        shown = item["surge"] if item["label"] == "surge" else item["potential"] if item["label"] == "potential" else None
        if shown is not None:  # 对比期在卖不到 14 天的写明天数（新品爆发本来就是之前没卖，不写）
            short = not shown.get("from_zero") and shown["base_in"] < th["short_base_days"]
            assert short == any(t.startswith("对比期只有") for t in item["tags"]), item["asin"]
        if item["label"] == "potential":
            p = item["potential"]
            assert not item["relist"]  # 老品换链接不算潜力
            assert th["potential"]["min_age_days"] <= p["age_days"] <= th["potential"]["max_age_days"]
            assert p["recent_ratio"] >= th["potential"]["min_recent_ratio"]
        if item["label"] == "hot":
            h = item["hot"]
            assert h["months_hit"] >= th["hot"]["months_required"] and h["cv"] <= th["hot"]["max_cv"]
            months = state["asins"][item["asin"]].get("months") or []
            # 达标的月份：≥ 子类目第 20 名，且量够（≥150 件或 ≥2 万美元）
            look = closed_months(months, full.as_of())[-th["hot"]["lookback_months"]:]
            money = {m[0]: m[2] for m in months}
            hits = sum(1 for k, u in look if u >= h["threshold"] - 0.05
                       and (u >= th["hot"]["min_units"] or (money.get(k) or 0) >= th["hot"]["min_revenue"]))
            assert hits == h["months_hit"], item["asin"]
            # 持续热销正在回落的标“最近回落”（不影响上榜）
            assert mom["fading"] == any(t.startswith("最近回落") for t in item["tags"]), item["asin"]
    assert checked == {"surge", "potential", "hot"}
    # 季节判断已经去掉：任何商品都没有季节标签
    assert not any(t.startswith(("季节性", "品类季节性")) for item in items for t in item["tags"])
    for item in items:  # 评论/销量比不除以变体数（评论和销量都是父体的）；老品换链接不算新品评论/销量比
        codes = {sig["code"] for sig in item["fake"]["signals"]}
        assert not any("÷" in sig["text"] for sig in item["fake"]["signals"]), item["asin"]
        if item["relist"]:
            assert "new_listing_reviews" not in codes and not any(t.startswith("新品起量") for t in item["tags"])


def test_directions_match_their_definition(world_run, master_key, cfg):
    """开发方向：按增量销售额排序；“比头部多”“基本款”都符合报告里写的门槛，头部占比可以从头部商品的标题重算；
    包装是增长商品包装数据的中位数。"""
    import statistics

    from radar import analyze, clock, materials, shipping, tracking
    from radar.detect import design, directions

    _world, _secrets, site, _first, second = world_run
    state = store.load(site, crypto.parse_master_key(master_key))
    last, disc = state["runs"][-1], state["discovery"]
    result = analyze.analyze(state, disc, list(last["labels"]), cfg, clock.parse_day(last["date"]),
                             tracking.fresh_rows(state))
    items = result["items"]
    for item in items:
        item["material"], _ = materials.best(state["asins"].get(item["asin"]), item["title"])
    growth = analyze.focus_items(items)
    out = directions.compute(growth, state, {}, cfg, disc=disc)
    c = cfg["thresholds"]["directions"]
    assert out and (not c["max"] or len(out) <= c["max"])
    # max = 0：够格的（≥min_items 个增长商品、≥min_brands 个品牌）全部列出，没列的只能是画不出画像的
    qualified = [node for node, _rows in directions.ranked(growth, cfg)]
    listed = [d["node"] for d in out]
    assert listed == [n for n in qualified if n in listed]
    table = {r["node"]: r for r in directions.overview(items, growth, out, disc, cfg)}
    assert all(table[n]["status"] == "没有共同的主材质或特征" for n in qualified if n not in listed)
    assert all(table[n]["shown"] and table[n]["increment"] == d["sales"]["increment"] for n, d in zip(listed, out))
    incs = [d["sales"]["increment"] for d in out]
    assert incs == sorted(incs, reverse=True)
    for d in out:
        rows = [i for i in growth if directions.node_of(i) == d["node"]]
        assert d["sales"]["increment"] == round(sum(directions.increment(i) for i in rows))
        heads = directions.heads(d["node"], disc, growth, cfg)
        assert d["compare"] == {"mode": "title", "n_head": len(heads), "n_head_all": len(heads)}  # 测试里没有主图识别
        assert not {h["asin"] for h in heads} & {i["asin"] for i in growth}
        nb = d["n_brands"]
        for dim, tiers in d["features"].items():
            for f in tiers["more"]:
                head = sum(1 for h in heads if f["label"] in design.extract(h["title"])[dim]) / len(heads)
                assert f["head_share"] == round(head, 3)
                assert f["count"] >= 2 and f["count"] / nb - head >= c["head_min_gap"] - 1e-9
                assert f["count"] / nb >= c["head_min_lift"] * head - 1e-9
            for f in tiers["base"]:
                assert f["count"] >= d["need"]
                more = f["count"] / nb - f["head_share"] >= c["head_min_gap"] - 1e-9 \
                    and f["count"] / nb >= c["head_min_lift"] * f["head_share"] - 1e-9
                assert not more, (d["node"], f)
        volumes = [shipping.cubic_feet(i["pkg"]["dims"]) for i in rows if i.get("pkg")]
        assert d["pkg"]["n"] == len(rows) and d["pkg"]["ft3"] == round(statistics.median(volumes), 1)
    assert not any("season" in d for d in out)
    plain = _decrypt_page((site / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8"),
                          crypto.parse_master_key(master_key))
    assert "比之前 28 天多约" in plain and "立方英尺" in plain and "季节" not in plain
    assert "（月增量约 $" in plain  # 本期要点（模板）里的开发方向


def test_report_is_encrypted_and_decryptable(world_run, master_key):
    _world, _secrets, site, _first, second = world_run
    page = (site / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8")
    assert "B0DEMO" not in page and "突然爆火" not in page   # 公开文件里只有密文
    plain = _decrypt_page(page, crypto.parse_master_key(master_key))
    assert "突然爆火" in plain and "B0DEMO" in plain and "与上期对比" in plain
    assert 'href="2026-10-05.html"' in plain and "2026-10-05.html#k=" not in plain  # 往期链接不带密钥（不能一期期往回连）
    index = (site / "index.html").read_text(encoding="utf-8")
    assert "2026-10-08" in index and "B0DEMO" not in index
    assert b"B0DEMO" not in (site / "data" / "state.enc").read_bytes()
    assert second.url.startswith("https://demo.github.io/radar/reports/2026-10-08.html#k=")


def test_old_reports_point_to_latest(world_run, master_key):
    """出了新一期后，往期报告的外壳改记最新一期 ID（打开后显示“回到最新报告”），密文不变、照样能解开。"""
    from radar.report import shell
    _world, _secrets, site, first, second = world_run
    old_page = (site / "reports" / f"{first.report_id}.html").read_text(encoding="utf-8")
    old, new = (shell.read_payload((site / "reports" / f"{r}.html").read_text(encoding="utf-8"))
                for r in (first.report_id, second.report_id))
    assert old["id"] == first.report_id and old["latest"] == second.report_id
    assert new["latest"] == second.report_id                       # 最新一期不显示这一条
    assert "回到最新报告" in old_page
    assert "突然爆火" in _decrypt_page(old_page, crypto.parse_master_key(master_key))


def test_not_due_is_skipped(cfg, master_key, tmp_path):
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START)
    calls = len(world.calls)
    result = pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=1))
    assert not result.published and "未满" in result.skipped_reason and len(world.calls) == calls


def test_budget_is_respected(cfg, master_key, tmp_path):
    """预算是硬上限；预算太少、一个商品的日数据都没拉到时不出报告（空报告保护），拉到的名单和调用次数照样保存。"""
    world = SyntheticWorld(START.date())
    with pytest.raises(pipeline.RunAborted) as aborted:
        pipeline.run(cfg, Secrets(report_key=master_key), tmp_path, transport=world, now=START, budget=25)
    assert aborted.value.calls <= 25 and len(world.calls) <= 25
    assert not (tmp_path / "reports").exists()
    assert store.load(tmp_path, crypto.parse_master_key(master_key))["calls"]["2026-10"] == aborted.value.calls


def test_unfinished_discovery_resumes_next_run(cfg, master_key, tmp_path):
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    with pytest.raises(pipeline.RunAborted):  # 预算只够发现的一部分：不出报告，发现的进度照样保存
        pipeline.run(cfg, secrets, tmp_path, transport=world, now=START, budget=4)
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["discovery"]["complete"] is False
    world.advance(3)
    try:
        pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=3), budget=60)
    except pipeline.RunAborted:
        pass  # 60 次补完发现以后日数据可能还不够，这里只看发现接着补上了
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["discovery"]["complete"] is True and state["discovery"]["period"] == "202609"


def test_failure_alert_says_whether_the_report_was_updated():
    """失败告警按哪一步出错说清楚报告更新了没有：钉钉推送失败时报告其实已经更新了。"""
    from radar.notify import dingtalk

    assert "报告已更新" in dingtalk.failure_text("雷达", "钉钉推送")
    assert "rerender" in dingtalk.failure_text("雷达", "网站部署")
    assert "已拉到的数据和调用次数已保存" in dingtalk.failure_text("雷达", "数据采集/分析", saved=True)
    assert "没能保存到 site 分支" in dingtalk.failure_text("雷达", "数据采集/分析", published=True)
    assert "没有生成报告" in dingtalk.failure_text("雷达", "数据采集/分析")
    quota = dingtalk.failure_text("雷达", "数据采集/分析", saved=True, reason="quota")
    assert "接口调用次数用完了" in quota and "不是程序出错" in quota
    crash = dingtalk.failure_text("雷达", "数据采集/分析", saved=True, reason="crash")
    assert "程序出错" in crash and "已拉到的数据和调用次数已保存" in crash


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


def test_whole_market_list_feeds_the_pool(cfg, master_key, tmp_path):
    """默认：每期把全部子类目里近 30 天量够的商品翻一遍（全市场名单），机会候选从它初筛；根类目的近 30 天榜单、相似款不再查。
    头一个月还没有 30 天前自己存的快照：拉一次结束于 21~45 天前的那个月的全市场月度名单当基准，下一期不再拉。"""
    from radar import clock

    cfg["pool"]["opportunity_min_units"] = 50  # 模拟世界里的商品销量偏小
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START)
    master = crypto.parse_master_key(master_key)
    state = store.load(tmp_path, master)
    uni = state["universe"]
    today = clock.parse_day(state["runs"][-1]["date"])
    assert uni["rows"] and uni["date"] == today.isoformat() and len(uni["snaps"]) == 1
    assert all(r["units"] >= 50 or r["revenue"] >= 40000 for r in uni["rows"].values())
    assert all(r["node"] != "1055398:1063306:1063318:3733551" for r in uni["rows"].values())  # 被排除的沙发不会混进来
    assert uni["baseline"]["period"] == "202608"  # 10-04 跑：9 月才结束 4 天，用 8 月（结束 34 天）
    reasons = {state["asins"][a]["pool_reason"] for a in state["runs"][-1]["labels"] if a in state["asins"]}
    assert "screen" in reasons and "similar" not in reasons
    assert "asin_competitor" not in world.calls
    assert state["runs"][-1]["coverage"]["universe"] == len(uni["rows"])

    world.advance(3)
    before = len(world.calls)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=3))
    state = store.load(tmp_path, master)
    assert len(state["universe"]["snaps"]) == 2 and state["universe"]["baseline"]["period"] == "202608"
    assert "asin_competitor" not in world.calls[before:]  # 基准月已经有了，第二期不再拉（period 没变）


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


def test_send_last_refuses_a_link_that_cannot_open(world_run, cfg, monkeypatch):
    """推不出报告网址时，按钮只有相对路径、在钉钉里点不开：不发送。"""
    from radar import config as config_mod

    _world, secrets, site, _first, _second = world_run
    secrets.report_base_url = ""
    secrets.dingtalk_webhooks = ["https://oapi.dingtalk.com/robot/send?access_token=t"]
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(config_mod, "github_repo_from_remote", lambda: "")
    sent = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200, json={})))
    assert pipeline.send_last(cfg, secrets, site, client=client) == [] and sent == []


def test_local_runs_derive_pages_url_from_git_remote(cfg, monkeypatch):
    import subprocess
    from types import SimpleNamespace

    from radar import config as config_mod

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    for remote in ("https://github.com/JiachengCui-01/furniture-radar.git", "git@github.com:JiachengCui-01/furniture-radar"):
        monkeypatch.setattr(subprocess, "run", lambda *a, remote=remote, **k: SimpleNamespace(stdout=remote + "\n"))
        assert config_mod.report_base_url(cfg, Secrets()) == "https://jiachengcui-01.github.io/furniture-radar/"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="https://gitee.com/a/b.git"))
    assert config_mod.report_base_url(cfg, Secrets()) == ""


def test_untracked_heads_are_judged_from_monthly_lists(cfg, master_key, tmp_path):
    """追踪池装不下的头部商品，用往月名单 + 近 30 天名单补判持续热销：第一期补齐往月名单后接着查近 30 天名单，当期就判。
    补判的商品只在持续热销里（标“只有月度数据”），不算追踪商品、不进“全部追踪商品”，每个都符合写出来的条件。"""
    from radar import analyze, clock, monthly_lists, tracking
    from radar.series import closed_months, cv, log_growth

    cfg["budget"].update(per_run=400, bootstrap_run=400, monthly_list_calls=200)
    cfg["pool"].update(max_size=12, core_max=12)
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    site = tmp_path / "site"
    pipeline.run(cfg, secrets, site, transport=world, now=START)
    state = store.load(site, crypto.parse_master_key(master_key))
    assert monthly_lists.complete(state, state["discovery"]["nodes"], state["discovery"]["period"], 6)
    assert state["monthly_lists"]["rolling"]["rows"] and state["runs"][-1]["monthly_judged"]  # 补齐的那一期就判
    world.advance(3)
    pipeline.run(cfg, secrets, site, transport=world, now=START + timedelta(days=3))
    state = store.load(site, crypto.parse_master_key(master_key))
    last, disc = state["runs"][-1], state["discovery"]
    result = analyze.analyze(state, disc, list(last["labels"]), cfg, clock.parse_day(last["date"]), tracking.fresh_rows(state))
    monthly = [i for i in result["items"] if not i.get("daily", True)]
    tracked = {a for a, r in state["asins"].items() if r.get("series")}
    assert monthly and last["monthly_judged"] and not {i["asin"] for i in monthly} & tracked
    th = cfg["thresholds"]["hot"]
    keys = monthly_lists.month_keys(disc["period"], th["list_months"])
    for item in monthly:
        assert item["label"] == "hot" and item["tags"][0].startswith("只有月度数据")
        months = monthly_lists.months_of(state, item["node"], item["asin"], None, keys)
        closed = closed_months(months, clock.parse_day(last["date"]))
        threshold = result["node_stats"][item["node"]]["threshold"]
        hits = sum(1 for k, u, rev, _p in months[-th["lookback_months"]:] if u >= threshold
                   and (u >= th["min_units"] or rev >= th["min_revenue"]))
        assert hits >= th["months_required"] and cv([u for _, u in closed]) <= th["max_cv"]
        assert (log_growth([u for _, u in closed[-3:]]) or 0) >= th["min_trend"]
        recent, _covered = monthly_lists.recent_rows(state, disc["nodes"], clock.parse_day(last["date"]))
        row = recent[item["asin"]]
        six_daily = sum(u / clock.days_in_month(k) for k, u in closed) / len(closed)
        assert row["units"] / 30 >= th["min_recent_ratio"] * six_daily - 1e-6
    sec = analyze.sections(result["items"], 0)
    assert sec["counts"]["total"] == len(tracked & set(last["labels"])) and sec["counts"]["hot_monthly"] == len(monthly)
    page = _decrypt_page((site / "reports" / f"{last['id']}.html").read_text(encoding="utf-8"),
                         crypto.parse_master_key(master_key))
    assert f"持续热销里 {len(monthly)} 个来自月度名单补判" in page and "只有月度数据" in page
    # 不是当期的近 30 天销量不用：换一天（全市场名单、近 30 天名单都不是那天的）就不补判，数据说明写明
    later = clock.parse_day(last["date"]) + timedelta(days=5)
    stale = analyze.analyze(state, disc, list(last["labels"]), cfg, later, tracking.fresh_rows(state))
    assert not [i for i in stale["items"] if not i.get("daily", True)] and stale["monthly_status"]["rolling"] == 0


def test_recent_sales_for_monthly_hot_come_from_the_same_run_even_when_the_pool_fills_the_budget(cfg, master_key, tmp_path):
    """持续热销补判的近 30 天销量：当期全市场名单 + 只给全市场名单覆盖不到头部的小子类目查近 30 天名单，在拉日数据之前查。
    以前近 30 天名单放在最后用剩下的预算查，初筛把预算占满时整期不查、补判用旧名单。"""
    from radar import monthly_lists

    cfg["budget"].update(per_run=400, bootstrap_run=400, monthly_list_calls=200)
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    site = tmp_path / "site"
    pipeline.run(cfg, secrets, site, transport=world, now=START)  # 第一期补齐往月名单
    world.advance(7)
    state = store.load(site, crypto.parse_master_key(master_key))
    thin = monthly_lists.thin_nodes(state["discovery"], cfg)
    cfg["budget"].update(per_run=200, bootstrap_run=200)  # 预算紧：池子会占满预算
    out = pipeline.run(cfg, secrets, site, transport=world, now=START + timedelta(days=7))
    state = store.load(site, crypto.parse_master_key(master_key))
    rolling = state["monthly_lists"]["rolling"]
    assert out.published and rolling["date"] == state["runs"][-1]["date"]
    assert set(rolling["nodes"]) == thin  # 翻了全市场名单的期，只查覆盖不到头部的小子类目
    assert state["universe"]["date"] == state["runs"][-1]["date"]
    # 按件数的往月名单（便宜、走量的头部判持续热销用）也在拉日数据之前补齐，预算紧也补得上
    disc = state["discovery"]
    assert monthly_lists.complete(state, disc["nodes"], disc["period"], 6, "units")


def test_early_monthly_list_is_fetched_again_once_settled(cfg, master_key, tmp_path):
    """卖家精灵的上月名单月初还没更新完（实测 10-09 拉的 9 月名单约两成商品还是 8 月的数）：
    月份结束后不到 20 天拉的，过了 20 天重拉一次，只重拉一次；已经到下个月就直接拉新月份。"""
    from datetime import date

    cur = {"complete": True, "period": "202609", "fetched_at": "2026-10-09T02:12:25+00:00"}
    assert not pipeline.needs_refresh(cur, cfg, date(2026, 10, 15))
    assert pipeline.needs_refresh(cur, cfg, date(2026, 10, 22))
    assert not pipeline.needs_refresh({**cur, "settled": True}, cfg, date(2026, 10, 22))
    assert not pipeline.needs_refresh({**cur, "fetched_at": "2026-10-21T00:00:00+00:00"}, cfg, date(2026, 10, 25))
    assert not pipeline.needs_refresh(cur, cfg, date(2026, 11, 3))

    cfg["budget"].update(per_run=400, bootstrap_run=400)
    world = SyntheticWorld(START.date())
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    site = tmp_path / "site"
    pipeline.run(cfg, secrets, site, transport=world, now=START)
    first = store.load(site, crypto.parse_master_key(master_key))["discovery"]
    world.advance(16)
    pipeline.run(cfg, secrets, site, transport=world, now=START + timedelta(days=16))  # 10-21：9 月结束 21 天
    state = store.load(site, crypto.parse_master_key(master_key))
    assert first["fetched_on"] == "2026-10-04" and not first.get("settled")
    assert state["discovery"]["settled"] and state["discovery"]["fetched_on"] == "2026-10-20"
    assert state["discovery"]["period"] == first["period"] == "202609"
    sep = state["monthly_lists"]["months"]["2026-09"]
    assert {n["path"] for n in state["discovery"]["nodes"]} <= set(sep)


class _AccountLimit:
    """卖家精灵账户额度用完：这些工具（或第 after 次以后的全部调用）返回“接口访问次数已达上限”。"""

    def __init__(self, world, tools=(), after=None):
        self.world, self.tools, self.after, self.n = world, set(tools), after, 0

    def __call__(self, tool, arguments):
        self.n += 1
        if tool in self.tools or (self.after is not None and self.n > self.after):
            return json.dumps({"code": "ERROR", "message": "接口访问次数已达上限"})
        return self.world(tool, arguments)


def test_account_limit_skips_the_run_or_only_the_extras(cfg, master_key, tmp_path):
    """名单和日数据还没拉完账户额度就用完了：不出这一期（不拿旧数据凑报告），抛给命令行让工作流失败、发钉钉告警，
    但已经拉到的数据和花掉的调用次数照样存下来（本月调用数不少记，下期接着用）；
    核心数据拉完后才用完（材质、核查、往年月销量这些补充查询）：跳过补充查询，照常出报告。"""
    cfg["budget"].update(per_run=400, bootstrap_run=400)
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    world = SyntheticWorld(START.date())
    with pytest.raises(pipeline.RunAborted) as aborted:
        pipeline.run(cfg, secrets, tmp_path / "a", transport=_AccountLimit(world, after=5), now=START)
    assert not (tmp_path / "a" / "reports").exists()
    saved = store.load(tmp_path / "a", crypto.parse_master_key(master_key))
    assert saved["calls"]["2026-10"] == aborted.value.calls == 6 and not saved.get("last_success")
    assert aborted.value.kind == "quota" and not aborted.value.repeat and saved["blocked"]["kind"] == "quota"
    # 第二天还被挡住：同一个原因 7 天内已经提醒过，标 repeat（命令行不再让工作流失败、不再发一样的告警）；7 天后再提醒一次
    for days, repeat in ((1, True), (3, True), (8, False)):
        with pytest.raises(pipeline.RunAborted) as again:
            pipeline.run(cfg, secrets, tmp_path / "a", transport=_AccountLimit(world, after=0), now=START + timedelta(days=days))
        assert again.value.repeat is repeat
    blocked = store.load(tmp_path / "a", crypto.parse_master_key(master_key))["blocked"]
    assert blocked["since"] == "2026-10-04" and blocked["alerted_on"] == "2026-10-12"
    out = pipeline.run(cfg, secrets, tmp_path / "b", now=START,
                       transport=_AccountLimit(world, tools=("asin_detail", "review", "asin_sales_trend")))
    assert out.published and (tmp_path / "b" / "reports" / f"{out.report_id}.html").exists()


def test_reseal_changes_ciphertext_but_not_content(cfg, master_key, tmp_path):
    """推送被 GitHub 误拦时重新加密：密文全变（换了随机 IV），解密出来的数据、报告和链接里的密钥都不变。"""
    from radar.report import shell

    cfg["budget"].update(per_run=400, bootstrap_run=400)
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    out = pipeline.run(cfg, secrets, tmp_path, transport=SyntheticWorld(START.date()), now=START)
    master = crypto.parse_master_key(master_key)
    page_path = tmp_path / "reports" / f"{out.report_id}.html"
    before_state, before_page = store.state_path(tmp_path).read_bytes(), page_path.read_text(encoding="utf-8")
    assert pipeline.reseal(cfg, secrets, tmp_path) == 2
    after_page = page_path.read_text(encoding="utf-8")
    assert store.state_path(tmp_path).read_bytes() != before_state and after_page != before_page
    assert store.load(tmp_path, master)["run_seq"] == 1
    assert _decrypt_page(after_page, master) == _decrypt_page(before_page, master)
    assert shell.read_payload(after_page)["latest"] == out.report_id


class _Flaky:
    """卖家精灵偶尔连不上：第 fail_at 次调用（或从 down_from 次起全部）抛 McpUnavailable。"""

    def __init__(self, world, fail_at=(), down_from=None):
        self.world, self.fail_at, self.down_from, self.n = world, set(fail_at), down_from, 0

    def __call__(self, tool, arguments):
        from radar.mcp_client import McpUnavailable

        self.n += 1
        if self.n in self.fail_at or (self.down_from is not None and self.n >= self.down_from):
            raise McpUnavailable("Could not reach the MCP server")
        return self.world(tool, arguments)


def test_network_blips_skip_one_call_and_an_outage_saves_what_was_fetched(cfg, master_key, tmp_path, monkeypatch):
    """一期要调用 2000 多次：偶尔一次连不上只跳过那一个（下期再查），照常出报告；
    一直连不上才中止，中止前拉到的数据和调用次数照样存下来。"""
    from radar import vendor as vendor_mod

    monkeypatch.setattr(vendor_mod.time, "sleep", lambda s: None)
    cfg["budget"].update(per_run=400, bootstrap_run=400)
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    world = SyntheticWorld(START.date())
    out = pipeline.run(cfg, secrets, tmp_path / "a", transport=_Flaky(world, fail_at={12, 13, 14, 40, 41, 42}), now=START)
    assert out.published
    with pytest.raises(pipeline.RunAborted):
        pipeline.run(cfg, secrets, tmp_path / "b", transport=_Flaky(world, down_from=30), now=START)
    saved = store.load(tmp_path / "b", crypto.parse_master_key(master_key))
    assert saved["calls"]["2026-10"] > 0 and not saved.get("runs")


def test_old_reports_lose_their_embedded_history_keys(world_run, master_key, cfg):
    """以前的报告里“往期”链接带着前几期的密钥：出新一期时解密、去掉密钥再加密回去，内容其余不变。"""
    from radar.report import shell

    _world, secrets, site, first, second = world_run
    master = crypto.parse_master_key(master_key)
    path = site / "reports" / f"{second.report_id}.html"
    payload = shell.read_payload(path.read_text(encoding="utf-8"))
    key, aad = crypto.report_key(master, payload["id"]), crypto.report_aad(payload["id"])
    plain = _decrypt_page(path.read_text(encoding="utf-8"), master)
    leaky = plain.replace(f'href="{first.report_id}.html"', f'href="{first.report_id}.html#k=SECRETKEY_abc-123"')
    assert leaky != plain
    # 以前的报告：没压缩、带往期密钥
    pipeline._write_shell(cfg, site, payload["id"], crypto.encrypt(key, leaky.encode("utf-8"), aad), payload["latest"])
    assert pipeline.scrub_history_keys(cfg, site, master, [second.report_id]) == 1
    assert _decrypt_page(path.read_text(encoding="utf-8"), master) == plain
    assert pipeline.scrub_history_keys(cfg, site, master, [second.report_id]) == 0


def test_previous_run_is_replayed_for_items_not_tracked_last_time(world_run, master_key, cfg):
    """回看判定：把日数据截到本期那天再判一次，结果必须和本期的判定一样（同一套规则、同一份数据）；
    截到更早的日子时，那之后的日数据、评论观测、月销量都不能用上。"""
    from radar import analyze, clock, tracking

    _world, _secrets, site, _first, _second = world_run
    state = store.load(site, crypto.parse_master_key(master_key))
    last = state["runs"][-1]
    today = clock.parse_day(last["date"])
    pool = [a for a in last["labels"] if (state["asins"].get(a) or {}).get("series")]
    result = analyze.analyze(state, state["discovery"], pool, cfg, today, tracking.fresh_rows(state))
    items = [i for i in result["items"] if i.get("daily", True)]
    same = analyze.retro_labels(state, state["discovery"], [i["asin"] for i in items], cfg, today, result["data_date"],
                                tracking.fresh_rows(state), result["events"]["days"])
    assert same == {i["asin"]: i["label"] for i in items}
    rec = state["asins"][items[0]["asin"]]
    day = result["data_date"] - timedelta(days=5)
    cut = analyze.truncated_rec(rec, day)
    assert cut["series"]["sales"] == rec["series"]["sales"][: len(rec["series"]["sales"]) - 5]
    assert all(m[0] <= day.isoformat()[:7] for m in cut["months"])


def test_a_crash_after_fetching_saves_the_data_and_the_next_run_reuses_it(cfg, master_key, tmp_path, monkeypatch, capsys):
    """拉完数据以后程序出错（判定、写报告……）：不出报告，但已拉到的日数据和花掉的调用次数照样存下来；
    这期写了一半的报告删掉、往期的期数和报告列表不变；告警每次都发（不按 7 天去重）；
    日志里只有出错的类型和位置，没有异常信息（可能含 ASIN）。第二天接着用已拉到的日数据，不整期重拉。"""
    from radar import analyze

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    cfg["budget"].update(per_run=400, bootstrap_run=400)
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    world = SyntheticWorld(START.date())
    real = analyze.sections

    def boom(*a, **k):
        raise KeyError("B0DEMO0001")

    monkeypatch.setattr(analyze, "sections", boom)
    for days in (0, 1):
        with pytest.raises(pipeline.RunAborted) as crashed:
            pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=days))
        assert crashed.value.kind == "crash" and not crashed.value.repeat
    assert "B0DEMO" not in capsys.readouterr().err and "KeyError" in str(crashed.value)
    saved = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert saved["calls"]["2026-10"] > 0 and saved["blocked"]["kind"] == "crash"
    assert not saved.get("runs") and not saved.get("reports") and not saved.get("last_success")
    assert not list((tmp_path / "reports").glob("*.html"))
    fetched = sum(1 for r in saved["asins"].values() if r.get("series"))
    assert fetched > 50
    monkeypatch.setattr(analyze, "sections", real)
    before = sum(1 for c in world.calls if c == "asin_prediction")
    out = pipeline.run(cfg, secrets, tmp_path, transport=world, now=START + timedelta(days=2))
    assert out.published
    again = sum(1 for c in world.calls if c == "asin_prediction") - before
    assert again < fetched / 2  # 只补拉上次没拉到的（和往期上榜的），不是整期重拉
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["run_seq"] == 1 and "blocked" not in state and state["calls"]["2026-10"] == saved["calls"]["2026-10"] + out.calls


def test_cli_stays_quiet_when_the_same_block_was_already_reported(monkeypatch, tmp_path):
    """同一个原因 7 天内已经提醒过：命令行返回 0（工作流不失败、不再发钉钉告警），但照样写出 saved=true 让工作流保存数据。"""
    from radar import cli

    out = tmp_path / "gh_out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("REPORT_KEY", crypto.generate_master_key())
    for repeat, code in ((False, 1), (True, 0)):
        def boom(*a, **k):
            raise pipeline.RunAborted("QuotaExhausted: 接口访问次数已达上限", 1, "quota", repeat)
        monkeypatch.setattr(pipeline, "run", boom)
        assert cli.main(["run", "--site-dir", str(tmp_path / "site"), "--github-output"]) == code
    assert out.read_text(encoding="utf-8").count("saved=true\nreason=quota") == 2


def test_budget_is_split_evenly_over_the_runs_left(cfg, master_key, tmp_path):
    """每期预算 = 本月剩下的额度 ÷ 本月还剩几期（含这一期，按北京时间的月份、每 7 天一期）：11 月 5 期，最后一期不会被挤；
    12 月 4 期，每期多分一些（以前固定 1,300 / 1,500，12 月有 1,800 次用不上）。"""
    from datetime import datetime, timezone

    from radar.config import load_config

    live = load_config()
    assert live["budget"]["split"] == "even"
    utc = lambda m, d: datetime(2026, m, d, 4, 40, tzinfo=timezone.utc)  # noqa: E731
    assert [pipeline.runs_left(utc(11, d), live) for d in (1, 8, 15, 22, 29, 30)] == [5, 4, 3, 2, 1, 1]
    assert pipeline.runs_left(utc(12, 6), live) == 4 and pipeline.runs_left(datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc), live) == 5
    cfg["budget"].update(split="even", monthly_cap=2000, max_per_run=450)
    cfg["schedule"]["min_days_between_runs"] = 7
    world = SyntheticWorld(utc(11, 1).date())
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=utc(11, 1))
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["runs"][-1]["coverage"]["budget"] == 400  # 2,000 ÷ 5 期
    state["calls"]["2026-11"] = 1700  # 假设前面几期（和 AI工作台 以外的手动运行）已经花了 1,700
    store.save(tmp_path, crypto.parse_master_key(master_key), state)
    world.advance(28)
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=utc(11, 29), force=True)
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert state["runs"][-1]["coverage"]["budget"] == 300  # 最后一期：剩下的全给它（不超过 max_per_run）


def test_weekly_schedule_counts_calls_in_the_quota_month(cfg, master_key, tmp_path):
    """每周一期：满 7 天（留 3 小时余量）才运行；调用次数按卖家精灵套餐的月份（北京时间的自然月）记——
    北京时间 11-01 12:40 跑的这一期（美国时间还是 10-31）算 11 月；要翻上个月全市场月度名单当基准的那一期给多一点预算。"""
    from datetime import datetime, timezone

    from radar.config import load_config

    assert load_config()["schedule"]["min_days_between_runs"] == 7  # 线上每周一期
    cfg["schedule"]["min_days_between_runs"] = 7
    state = {"last_success": "2026-10-23T04:44:28+00:00"}
    assert pipeline.is_due(state, cfg, datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc))[0]          # 6 天 21 小时
    assert not pipeline.is_due(state, cfg, datetime(2026, 10, 29, 4, 40, tzinfo=timezone.utc))[0]     # 6 天
    when = datetime(2026, 11, 1, 4, 40, tzinfo=timezone.utc)
    world = SyntheticWorld(when.date())
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    pipeline.run(cfg, secrets, tmp_path, transport=world, now=when)
    state = store.load(tmp_path, crypto.parse_master_key(master_key))
    assert set(state["calls"]) == {"2026-11"} and state["runs"][-1]["date"] == "2026-10-31"
    assert state["runs"][-1]["coverage"]["budget"] == cfg["budget"]["bootstrap_run"]

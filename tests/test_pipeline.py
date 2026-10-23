"""端到端：用模拟世界连续跑两期，检查报告、加密、对比、预算、日志脱敏和钉钉推送。"""
import itertools
import json
import statistics
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
    from radar.detect import seasonality
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
            if not h["adjusted"]:  # 达标的月份：≥ 子类目第 20 名，且量够（≥150 件或 ≥2 万美元）
                look = closed_months(months, full.as_of())[-th["hot"]["lookback_months"]:]
                money = {m[0]: m[2] for m in months}
                hits = sum(1 for k, u in look if u >= h["threshold"] - 0.05
                           and (u >= th["hot"]["min_units"] or (money.get(k) or 0) >= th["hot"]["min_revenue"]))
                assert hits == h["months_hit"], item["asin"]
            # 持续热销正在回落的标“最近回落”（不影响上榜）
            assert mom["fading"] == any(t.startswith("最近回落") for t in item["tags"]), item["asin"]
    assert checked == {"surge", "potential", "hot"}
    # 季节性商品：小窗里写的四条，从每个商品的每月日均重新核对；符合的（异常信号、评分不达标除外）都列出来
    sc = th["seasonality"]
    listed = {i["asin"] for i in analyze.sections(items, 0)["seasonal"]}
    for item in items:
        s_ = item["item_season"]
        tagged = any(t.startswith("季节性（") for t in item["tags"])
        assert tagged == bool(s_ and s_["seasonal"])
        assert (item["asin"] in listed) == (tagged and item["label"] not in ("fake", "low"))
        if not tagged:
            continue
        prof = result["profiles"][item["node"]]
        assert prof["seasonal"] and s_["peak_months"] == prof["peak_months"]  # ① 品类每年同样的月份高
        keys = [k for k, _, _ in s_["months"]]
        assert all(seasonality._add_months(a, 1) == b for a, b in zip(keys, keys[1:]))  # ② 连续每月都有销量
        runs = [(peak, [v for _, v, _ in grp]) for peak, grp in itertools.groupby(s_["months"], key=lambda m: m[2])]
        assert sum(1 for j, (peak, _) in enumerate(runs) if peak and len(runs) > 1) >= sc["item_min_cycles"]  # 至少 2 年的旺季
        assert state["asins"][item["asin"]]["sales_trend"]["points"]  # 用的是商品自己往年的月销量
        peak_levels = [statistics.mean(v) for peak, v in runs if peak]
        sides = set()
        for j, (peak, values) in enumerate(runs):  # ③ 每段旺季 ≥ 前后淡季的 2 倍，涨上去、掉下来都看得到
            for nb in (j - 1, j + 1):
                if peak and 0 <= nb < len(runs):
                    assert statistics.mean(values) >= sc["item_ratio"] * statistics.mean(runs[nb][1]) - 1e-6
                    sides.add(nb > j)
        assert sides == {True, False}
        assert all(v <= sc["item_off_max"] * min(peak_levels) + 1e-6 for _, v, peak in s_["months"] if not peak)  # ④
    seasonal_demo = {p.asin for p in _world.products if p.kind == "seasonal"}
    assert seasonal_demo and seasonal_demo <= listed  # 演示数据：餐边柜有一个每年 11~12 月卖得特别好的商品
    max_vars = th["fake"]["max_variations_for_ratio"]
    for item in items:  # 变体多的不算留评率；老品换链接不算新品评论/销量比
        codes = {sig["code"] for sig in item["fake"]["signals"]}
        if (item.get("variations") or 0) > max_vars:
            assert "review_rate" not in codes, item["asin"]
        if item["relist"]:
            assert "new_listing_reviews" not in codes and not any(t.startswith("新品起量") for t in item["tags"])


def test_directions_match_their_definition(world_run, master_key, cfg):
    """开发方向：按增量销售额排序；“比头部多”“基本款”都符合报告里写的门槛，头部占比可以从头部商品的标题重算；
    包装是增长商品包装数据的中位数；季节判断来自老链接（模拟世界里餐边柜 12 月是旺季）。"""
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
    out = directions.compute(growth, state, {}, cfg, disc=disc, seasons=result["seasons"])
    c = cfg["thresholds"]["directions"]
    assert out and len(out) <= c["max"]
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
    sideboard = next(d for d in out if d["node"] == "餐边柜")
    s = sideboard["season"]  # 演示数据：餐边柜的类目销量每年 11~12 月是旺季
    assert s["seasonal"] and s["peak_before"] and s["peak"]["month"].endswith("-12") and s["peak_months"] == [11, 12]
    shelf = result["seasons"][next(n["path"] for n in disc["nodes"] if n["name_cn"] == "书架")]
    assert shelf["ready"] and not shelf["seasonal"]  # 书架的小高峰每年在不同月份：不算季节性品类
    plain = _decrypt_page((site / "reports" / f"{second.report_id}.html").read_text(encoding="utf-8"),
                          crypto.parse_master_key(master_key))
    assert "比之前 28 天多约" in plain and "到货前品类会先冲高" in plain and "立方英尺" in plain
    assert 'id="pop-season"' in plain and "季节性品类：旺季 11~12 月" in plain
    assert "（月增量约 $" in plain  # 本期要点（模板）里的开发方向


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
        row = state["monthly_lists"]["rolling"]["rows"][item["asin"]]
        six_daily = sum(u / clock.days_in_month(k) for k, u in closed) / len(closed)
        assert row["units"] / 30 >= th["min_recent_ratio"] * six_daily - 1e-6
    sec = analyze.sections(result["items"], 0)
    assert sec["counts"]["total"] == len(tracked & set(last["labels"])) and sec["counts"]["hot_monthly"] == len(monthly)
    page = _decrypt_page((site / "reports" / f"{last['id']}.html").read_text(encoding="utf-8"),
                         crypto.parse_master_key(master_key))
    assert f"持续热销里 {len(monthly)} 个来自月度名单补判" in page and "只有月度数据" in page


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
    """名单和日数据还没拉完账户额度就用完了：不出这一期（不拿旧数据凑报告），抛给命令行让工作流失败、发钉钉告警；
    核心数据拉完后才用完（材质、核查、往年月销量这些补充查询）：跳过补充查询，照常出报告。"""
    from radar.vendor import QuotaExhausted

    cfg["budget"].update(per_run=400, bootstrap_run=400)
    secrets = Secrets(report_key=master_key, report_base_url="https://demo.github.io/radar/")
    world = SyntheticWorld(START.date())
    with pytest.raises(QuotaExhausted):
        pipeline.run(cfg, secrets, tmp_path / "a", transport=_AccountLimit(world, after=5), now=START)
    assert not (tmp_path / "a" / "reports").exists() and not store.state_path(tmp_path / "a").exists()
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

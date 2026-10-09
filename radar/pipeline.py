"""一次完整运行：发现 → 近 30 天榜单 / 相似款 → 选池 → 刷新日数据 → 判定 → 核查 → 报告 → 加密发布 →（可选）钉钉。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import analyze, clock, crypto, discovery, log, materials, narrative, store, tracking, verify, vision
from .config import Secrets, report_base_url
from .detect import diff as diffing
from .detect.traits import merge_appearance
from .notify import dingtalk
from .report import render as report_render
from .report import shell
from .vendor import Budget, BudgetExhausted, Transport, Vendor, mcp_transport


FORGET_RUNS = 20  # 连续这么多期没进池的 ASIN 从状态里删除


@dataclass
class RunResult:
    published: bool = False
    skipped_reason: str = ""
    report_id: str = ""
    url: str = ""
    calls: int = 0
    counts: dict = field(default_factory=dict)
    notified: list = field(default_factory=list)


def is_due(state: dict, cfg: dict, now_utc: datetime) -> tuple[bool, str]:
    last = state.get("last_success")
    if not last:
        return True, "首次运行"
    elapsed = now_utc - datetime.fromisoformat(last)
    need = timedelta(days=float(cfg["schedule"]["min_days_between_runs"])) - timedelta(hours=3)
    if elapsed >= need:
        return True, f"距上次运行 {elapsed.days} 天"
    return False, f"距上次运行仅 {elapsed.total_seconds() / 86400:.1f} 天，未满 {cfg['schedule']['min_days_between_runs']} 天"


def needs_discovery(state: dict, cfg: dict, today) -> bool:
    """本期要不要重新发现（首期、换了监控范围、新的月份数据）：要的话给更多预算。"""
    current = state.get("discovery")
    return (not state.get("asins") or not current or not current.get("complete")
            or current.get("scope_key") != discovery.scope_key(cfg)
            or current.get("period", "") < clock.closed_period(today))


def ensure_discovery(vendor: Vendor, cfg: dict, state: dict, today) -> dict | None:
    target = clock.closed_period(today)
    current, pending = state.get("discovery"), state.get("discovery_pending")
    key = discovery.scope_key(cfg)
    if current and current.get("scope_key") != key:
        log.info("监控范围有变化，本期重新发现子类目和头部商品")
        state["discovery"] = current = None
        state["risers"] = None
    if pending and pending.get("scope_key") != key:
        state["discovery_pending"] = pending = None
    if current and current.get("complete") and current["period"] >= target:
        return current
    for period in (target, clock.step_period(target, -1)):
        if current and current.get("complete") and current["period"] >= period:
            break
        resume = None
        if pending and pending.get("period") == period:
            resume = pending
        elif current and current.get("period") == period and not current.get("complete"):
            resume = current
        result = discovery.discover(vendor, cfg, period, resume)
        if result is None:
            log.info(f"发现：{period} 的月度数据尚未发布")
            continue
        if result["complete"]:
            state["discovery"], state["discovery_pending"] = result, None
        else:
            state["discovery_pending"] = result
            if not current or not current.get("complete"):
                state["discovery"] = result
        break
    return state.get("discovery")


def _report_id(day_text: str, existing: list[str]) -> str:
    if day_text not in existing:
        return day_text
    n = 2
    while f"{day_text}-{n}" in existing:
        n += 1
    return f"{day_text}-{n}"


def _period_text(period: str | None) -> str:
    if not period:
        return "—"
    return f"{period[:4]}年{int(period[4:6])}月"


def build_url(cfg: dict, secrets: Secrets, report_id: str, key_text: str) -> str:
    base = report_base_url(cfg, secrets)
    return f"{base}reports/{report_id}.html#k={key_text}"


def run(cfg: dict, secrets: Secrets, site_dir: str | os.PathLike, *,
        transport: Transport | None = None, schema_loader: Callable | None = None,
        now: datetime | None = None, force: bool = False, budget: int | None = None,
        notify: bool = False) -> RunResult:
    master = crypto.parse_master_key(secrets.report_key)
    site = Path(site_dir)
    state = store.load(site, master)
    now_utc = now or datetime.now(timezone.utc)

    due, why = is_due(state, cfg, now_utc)
    if not due and not force:
        log.info(f"跳过：{why}")
        return RunResult(skipped_reason=why)
    log.info(f"开始运行（{why}{'，强制' if force and not due else ''}）")

    today = clock.to_tz(now_utc, cfg["vendor_timezone"]).date()
    run_seq = int(state.get("run_seq") or 0) + 1
    month = clock.month_key(today)
    used_this_month = int(state["calls"].get(month, 0))
    bcfg = cfg["budget"]
    if budget is not None:
        limit = budget
    else:
        base = bcfg["bootstrap_run"] if needs_discovery(state, cfg, today) else bcfg["per_run"]
        limit = min(base, max(0, bcfg["monthly_cap"] - used_this_month))

    live = transport is None
    if live:
        if not secrets.sellersprite_key:
            raise RuntimeError("缺少 SELLERSPRITE_SECRET_KEY")
        transport, client = mcp_transport(secrets.sellersprite_url, secrets.sellersprite_key)
        schema_loader = client.list_tools
    vendor = Vendor(transport, Budget(limit), live=live, min_interval=float(bcfg["min_interval_seconds"]) if live else 0,
                    rate_limit_wait=float(bcfg.get("rate_limit_wait_seconds", 60)) if live else 0,
                    schema_loader=schema_loader)
    log.info(f"本期调用预算 {limit} 次（本月已用 {used_this_month}/{bcfg['monthly_cap']}）")

    # 1. 发现（月度基线）+ 近 30 天榜单（每期刷新）+ 上期爆款的相似款
    disc = ensure_discovery(vendor, cfg, state, today)
    if disc and disc.get("nodes"):
        try:
            state["risers"] = {"run": run_seq, "date": today.isoformat(),
                               "items": discovery.find_risers(vendor, cfg, disc["nodes"])}
        except BudgetExhausted:
            log.warn("近 30 天榜单：预算不足，跳过")
        refresh_similar(vendor, cfg, state, disc, run_seq)

    # 2. 追踪池：大小不超过本期还能刷新的个数（给核查和材质查询留出预算）
    reserve = min(int(bcfg["review_checks"]) + int(bcfg.get("material_lookups", 0)), vendor.budget.remaining // 4)
    pool, reasons = tracking.select_pool(disc, state, cfg, today, limit=vendor.budget.remaining - reserve)
    for asin in pool:
        rec = state["asins"].setdefault(asin, {"asin": asin, "first_seen_run": run_seq, "obs": []})
        rec["last_pool_run"] = run_seq
        rec["pool_reason"] = reasons[asin]
    n_cands = len(tracking.opportunity_candidates(disc, state, cfg, today))
    mix = {k: sum(1 for r in reasons.values() if r == k) for k in tracking.REASON_CN}
    log.info(f"追踪池 {len(pool)} 个 ASIN（" + " / ".join(f"{tracking.REASON_CN[k]} {v}" for k, v in mix.items())
             + f"；机会候选共 {n_cands} 个）")

    # 3. 刷新日数据
    plan = tracking.plan_fetch(pool, state, run_seq, cfg, vendor.budget.remaining - reserve)
    refreshed = 0
    try:
        refreshed = tracking.refresh(vendor, plan, state, disc, cfg, run_seq)
    except BudgetExhausted:
        log.warn("追踪阶段预算用完")
    log.info(f"刷新日数据 {refreshed}/{len(plan)} 个")

    # 4. 判定 + 核查 + 再判定
    extra_rows = tracking.fresh_rows(state)
    result = analyze.analyze(state, disc, pool, cfg, today, extra_rows)
    jobs = verify.select(result["items"], state, cfg, today)
    if jobs and vendor.budget.remaining > 0:
        checked = verify.run_checks(vendor, jobs, state, cfg, today)
        log.info(f"核查 {checked} 个可疑商品")
        if checked:
            result = analyze.analyze(state, disc, pool, cfg, today, extra_rows)
    items = result["items"]
    labels = {i["asin"]: [i["label"], i["fake"]["score"]] for i in items}
    prev_run = state["runs"][-1] if state.get("runs") else None

    # 主材质：增长商品 + 持续热销对照组，查亚马逊商品详情的 Material（查过的永久缓存）
    focus, reference = analysis_groups(items, cfg)
    looked = materials.lookup(vendor, focus + reference, state, cfg, today,
                              int(bcfg.get("material_lookups", 15)))
    if looked:
        log.info(f"材质查询 {looked} 个")

    # 5. 报告
    display_now = clock.to_tz(now_utc, cfg["display_timezone"])
    report_id = _report_id(display_now.strftime("%Y-%m-%d"), state.get("reports") or [])
    coverage = {"refreshed": refreshed, "calls": vendor.billable_calls, "budget": limit, "candidates": n_cands,
                "opportunity": mix["opportunity"] + mix["rotate"] + mix["similar"]}
    ctx = build_report(cfg, secrets, master, site, state, disc, items, report_id=report_id, run_seq=run_seq,
                       generated_at=display_now.strftime("%Y-%m-%d %H:%M"), prev_run=prev_run,
                       coverage=coverage, today=today)
    key_text = ctx["key_text"]
    sec = ctx["sections"]

    state.setdefault("reports", []).append(report_id)
    keep = int(cfg["report"]["keep_reports"])
    reports_dir = site / "reports"
    for old_id in state["reports"][:-keep]:
        old = reports_dir / f"{old_id}.html"
        if old.exists():
            old.unlink()
    state["reports"] = state["reports"][-keep:]
    (site / "index.html").write_text(shell.index_page(cfg["report"]["title"], state["reports"]), encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")

    # 6. 状态
    state.setdefault("runs", []).append(_run_record(cfg, ctx, today.isoformat(), labels))
    state["runs"] = state["runs"][-20:]
    state["run_seq"] = run_seq
    state["last_success"] = now_utc.isoformat(timespec="seconds")
    state["calls"][month] = used_this_month + vendor.billable_calls
    # 不在本期池子里的丢掉大块日数据（下次进池会重新拉）；保留“上次查过”的记号、评论观测和材质缓存，
    # 轮查靠它排先后。很久没进池的整条删除。
    for asin, rec in list(state["asins"].items()):
        last = int(rec.get("last_pool_run", 0))
        if last < run_seq - FORGET_RUNS:
            del state["asins"][asin]
        elif last < run_seq:
            rec.pop("series", None)
    store.save(site, master, state)

    url = build_url(cfg, secrets, report_id, key_text)
    log.info(f"报告已生成：第 {run_seq} 期 {report_id}，调用 {vendor.billable_calls} 次；"
             f"爆火 {sec['counts']['surge']} / 潜力 {sec['counts']['potential']} / "
             f"热销 {sec['counts']['hot']} / 异常 {sec['counts']['fake']}")
    log.detail(f"报告链接：{url}")
    out = RunResult(published=True, report_id=report_id, url=url, calls=vendor.billable_calls,
                    counts=sec["counts"])
    if notify:
        out.notified = send_last(cfg, secrets, site)
    return out


def refresh_similar(vendor: Vendor, cfg: dict, state: dict, disc: dict, run_seq: int) -> None:
    """上期爆火（其次潜力）的商品作种子查相似款；同一种子 similar_cooldown_runs 期内不重复查。
    相似款保留 similar_cooldown_runs 期，期间都可以作为候选。"""
    dcfg = cfg["discovery"]
    cooldown = int(dcfg.get("similar_cooldown_runs", 3))
    labels = (state["runs"][-1].get("labels") or {}) if state.get("runs") else {}
    seeds = [a for want in ("surge", "potential") for a, (label, *_r) in labels.items() if label == want
             and int((state["asins"].get(a) or {}).get("similar_run", -10_000)) < run_seq - cooldown + 1]
    seeds = seeds[: int(dcfg.get("similar_seeds", 0))]
    kept = [p for p in (state.get("similar") or {}).get("items") or [] if p.get("run", 0) > run_seq - cooldown]
    found: list[dict] = []
    try:
        for seed in seeds:
            rows = discovery.find_similar(vendor, cfg, [seed], disc["nodes"])
            state["asins"].setdefault(seed, {"asin": seed, "obs": []})["similar_run"] = run_seq
            found.extend(dict(p, run=run_seq) for p in rows)
    except BudgetExhausted:
        log.warn("相似款：预算不足，跳过")
    if seeds:
        log.info(f"相似款：{len(seeds)} 个上期爆款带出 {len(found)} 个同类商品")
    new = {p["asin"] for p in found}
    state["similar"] = {"run": run_seq, "items": found + [p for p in kept if p["asin"] not in new]}


def analysis_groups(items: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """外观/材质分析的两组：增长商品（按势头）和持续热销对照组（按月销量）。"""
    llm = cfg["llm"]
    focus = analyze.focus_items(items)[: int(llm.get("vision_focus", 20))]
    reference = analyze.sections(items, 10_000)["hot"][: int(llm.get("vision_reference", 10))]
    return focus, reference


def build_report(cfg: dict, secrets: Secrets, master: bytes, site: Path, state: dict, disc: dict | None,
                 items: list[dict], *, report_id: str, run_seq: int, generated_at: str,
                 prev_run: dict | None, coverage: dict, today) -> dict:
    """板块、外观与工艺特征、与上期对比、文字简报 → 渲染 → 加密写入 reports/<id>.html。返回报告上下文。"""
    sec = analyze.sections(items, int(cfg["report"]["top_n"]))
    trait = analyze.compute_traits(items, disc, state, today)

    # 主图外观识别 + 主材质：增长商品，另取持续热销对照组
    focus, reference = analysis_groups(items, cfg)
    tagged = vision.tag_items(focus + reference, state, cfg, secrets.llm_api_key)
    if tagged:
        log.info(f"主图识别 {tagged} 张")
    trait["vision"] = vision.summarize(focus, reference, state)
    trait["materials"] = materials.compare(analyze.focus_items(items), reference, state)
    trait["appearance"] = merge_appearance(trait.get("design") or {}, trait["vision"])
    for item in items:
        rec = state["asins"].get(item["asin"]) or {}
        item["material"], item["material_source"] = materials.best(rec, item["title"])
        item["look"] = (rec.get("vision") or {}).get("summary") or ""

    labels = {i["asin"]: i["label"] for i in items}
    as_of_values = [i["as_of"] for i in items if i.get("as_of")]
    key_text = crypto.report_key_text(master, report_id)
    log.mask(key_text)
    ctx = {
        "title": cfg["report"]["title"],
        "report_id": report_id,
        "key_text": key_text,
        "run_seq": run_seq,
        "generated_at": generated_at,
        "period": (disc or {}).get("period"),
        "period_text": _period_text((disc or {}).get("period")),
        "as_of": max(as_of_values) if as_of_values else "—",
        "items": items,
        "focus": analyze.focus_items(items),
        "sections": sec,
        "traits": trait,
        "diff": diffing.compare(prev_run if prev_run and prev_run.get("scope_key") == discovery.scope_key(cfg)
                                else None, labels),
        "coverage": {**coverage, "stale": sum(1 for i in items if i["stale"])},
        "cfg": cfg,
        "top_n": int(cfg["report"]["top_n"]),
    }
    ctx["summary"], ctx["summary_source"] = narrative.summary(ctx, cfg, secrets.llm_api_key)
    ctx["history"] = [{"id": old_id, "href": f"{old_id}.html#k={crypto.report_key_text(master, old_id)}"}
                      for old_id in list(reversed(state.get("reports") or [])) if old_id != report_id][:6]

    page = report_render.render(ctx)
    envelope = crypto.encrypt(crypto.report_key(master, report_id), page.encode("utf-8"),
                              crypto.report_aad(report_id))
    reports_dir = site / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{report_id}.html").write_text(
        shell.encrypted_page(f"{cfg['report']['title']} {report_id}", envelope, report_id), encoding="utf-8")
    return ctx


def _run_record(cfg: dict, ctx: dict, date_text: str, labels: dict) -> dict:
    return {
        "id": ctx["report_id"], "seq": ctx["run_seq"], "date": date_text, "labels": labels,
        "scope_key": discovery.scope_key(cfg),
        "counts": ctx["sections"]["counts"], "generated_at": ctx["generated_at"],
        "coverage": {k: ctx["coverage"].get(k) for k in ("refreshed", "calls", "budget", "candidates", "opportunity")},
        "notify": {"title": f"{cfg['report']['title']} {ctx['report_id']}",
                   "text": dingtalk.build_text(ctx, int(cfg["notify"]["max_items"]))},
    }


def rerender(cfg: dict, secrets: Secrets, site_dir: str | os.PathLike) -> RunResult:
    """用已保存的数据重新生成最近一期报告（不调用卖家精灵）。报告 ID 和链接不变，
    钉钉里已经发出的链接打开就是新版本。用于改进报告样式或分析方法后刷新。"""
    master = crypto.parse_master_key(secrets.report_key)
    site = Path(site_dir)
    state = store.load(site, master)
    if not state.get("runs"):
        log.warn("还没有任何报告，无法重新生成")
        return RunResult(skipped_reason="还没有报告")
    last = state["runs"][-1]
    disc = state.get("discovery")
    today = clock.parse_day(last.get("date")) or clock.now_in(cfg["vendor_timezone"]).date()
    extra_rows = tracking.fresh_rows(state)
    items = analyze.analyze(state, disc, list(last.get("labels") or {}), cfg, today, extra_rows)["items"]
    coverage = last.get("coverage") or {}
    if not coverage:
        month = (last.get("date") or "")[:7]
        same_month = [r for r in state["runs"] if (r.get("date") or "")[:7] == month]
        coverage = {"refreshed": sum(1 for r in state["asins"].values() if r.get("fetched_run") == last["seq"]),
                    "calls": state["calls"].get(month) if len(same_month) == 1 else None, "budget": None}
    ctx = build_report(cfg, secrets, master, site, state, disc, items, report_id=last["id"], run_seq=last["seq"],
                       generated_at=last.get("generated_at") or clock.to_tz(datetime.now(timezone.utc),
                                                                            cfg["display_timezone"]).strftime("%Y-%m-%d %H:%M"),
                       prev_run=state["runs"][-2] if len(state["runs"]) >= 2 else None, coverage=coverage,
                       today=today)
    labels = {i["asin"]: [i["label"], i["fake"]["score"]] for i in items}
    state["runs"][-1] = _run_record(cfg, ctx, last.get("date") or today.isoformat(), labels)
    store.save(site, master, state)
    sec = ctx["sections"]
    log.info(f"已重新生成第 {last['seq']} 期报告 {last['id']}（未调用卖家精灵）；爆火 {sec['counts']['surge']} / "
             f"潜力 {sec['counts']['potential']} / 热销 {sec['counts']['hot']} / 异常 {sec['counts']['fake']}")
    return RunResult(published=True, report_id=last["id"], url=build_url(cfg, secrets, last["id"], ctx["key_text"]),
                     counts=sec["counts"])


def send_last(cfg: dict, secrets: Secrets, site_dir: str | os.PathLike, *, client=None) -> list[bool]:
    """把最近一期的卡片消息发到钉钉（工作流里在网站部署完成后再调用）。"""
    if not secrets.dingtalk_webhooks:
        log.warn("未配置 DINGTALK_WEBHOOK，跳过钉钉推送")
        return []
    master = crypto.parse_master_key(secrets.report_key)
    state = store.load(site_dir, master)
    if not state.get("runs"):
        log.warn("还没有任何报告，跳过钉钉推送")
        return []
    last = state["runs"][-1]
    key_text = crypto.report_key_text(master, last["id"])
    log.mask(key_text)
    url = build_url(cfg, secrets, last["id"], key_text)
    if not url.startswith("http"):
        log.warn("无法确定报告网址：请设置 REPORT_BASE_URL 或在 GitHub Actions 中运行")
    payload = dingtalk.action_card(last["notify"]["title"], last["notify"]["text"], url,
                                   open_in_browser=bool(cfg["notify"]["open_in_browser"]))
    results = dingtalk.send(secrets.dingtalk_webhooks, secrets.dingtalk_secrets, payload, client=client)
    log.info(f"钉钉推送：成功 {sum(results)}/{len(results)}")
    return results

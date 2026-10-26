"""一次完整运行：发现 → 近 30 天榜单 / 相似款 → 选池 → 刷新日数据 → 判定 → 核查 → 报告 → 加密发布 →（可选）钉钉。"""
from __future__ import annotations

import itertools
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import (analyze, category_history, clock, crypto, discovery, log, materials, monthly_lists, narrative, sales_trend,
               store, tracking, universe, verify, vision)
from .config import Secrets, report_base_url
from .detect import diff as diffing
from .detect import directions, seasonality
from .detect.traits import merge_appearance
from .notify import dingtalk
from .report import render as report_render
from .report import shell
from .mcp_client import McpUnavailable
from .vendor import Budget, BudgetExhausted, QuotaExhausted, Transport, Vendor, mcp_transport


FORGET_RUNS = 20  # 连续这么多期没进池的 ASIN 从状态里删除


class RunAborted(RuntimeError):
    """核心数据没拉完就中止（账户额度用完 / 卖家精灵一直连不上）：不出报告，但已拉到的数据和调用次数已经存进 site。"""

    def __init__(self, reason: str, calls: int):
        super().__init__(reason)
        self.calls = calls


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
    """本期要不要重新发现（首期、换了监控范围、新的月份数据、月初拉的名单该重拉了）：要的话给更多预算。"""
    current = state.get("discovery")
    return (not state.get("asins") or not current or not current.get("complete")
            or current.get("scope_key") != discovery.scope_key(cfg)
            or current.get("period", "") < clock.closed_period(today)
            or needs_refresh(current, cfg, today))


def needs_refresh(current: dict | None, cfg: dict, today) -> bool:
    """月度名单是月份结束后不到 discovery.settle_days 天拉的、现在已经过了这么多天：重新拉一次。
    卖家精灵的上月名单在月初还没更新完：实测 10-09 拉的 9 月名单里，衣帽架 31 个商品的 9 月销量还是 8 月的数，
    10-22 再拉全部更新了、和日数据接口一致（9 月名单整体约两成商品 8、9 月销量一模一样，其他相邻月份只有零销量的这样）。"""
    settle = int(cfg["discovery"].get("settle_days") or 0)
    if not settle or not current or not current.get("complete") or current.get("settled"):
        return False
    period = str(current.get("period") or "")
    fetched = clock.parse_day(current.get("fetched_on") or current.get("fetched_at"))  # 拉名单那天（美国时间）
    if len(period) != 6 or period != clock.closed_period(today) or fetched is None:
        return False
    end = date(int(period[:4]), int(period[4:]), clock.days_in_month(f"{period[:4]}-{period[4:]}"))
    return (fetched - end).days < settle <= (today - end).days


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
    if needs_refresh(current, cfg, today):
        log.info(f"发现：{current['period']} 的名单是月初拉的（卖家精灵那时还没更新完），重新拉一次")
        result = discovery.discover(vendor, cfg, current["period"], None)
        if result and result["complete"]:
            result["settled"], result["fetched_on"] = True, today.isoformat()
            state["discovery"] = current = result
        else:
            log.warn("发现：重新拉名单时预算不够，先用原来的，下期再拉")
    if current and current.get("complete") and current["period"] >= target:
        if len(current.get("done_units") or []) < len(current.get("nodes") or []):
            try:  # 以前发现的名单还没有“按件数的头部”（持续热销门槛按件数排名用）：补上，每个子类目 1 次
                calls = discovery.fill_units(vendor, cfg, current)
                log.info(f"发现：补了按件数排的头部名单（{calls} 次调用）")
            except BudgetExhausted:
                log.warn("发现：补按件数排的头部名单时预算不够，下期再补")
        return discovery.apply_product_filter(current, cfg)
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
        result["fetched_on"] = today.isoformat()
        if result["complete"]:
            state["discovery"], state["discovery_pending"] = result, None
        else:
            state["discovery_pending"] = result
            if not current or not current.get("complete"):
                state["discovery"] = result
        break
    return discovery.apply_product_filter(state.get("discovery"), cfg)


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

    # 核心数据（名单、日数据）拉到一半，账户额度用完或卖家精灵一直连不上：不出这一期，但已经拉到的数据和花掉的调用次数照样存下来
    #（工作流照样保存到 site 分支），下次运行接着用，本月调用数也不会少记
    try:
        # 1. 发现（月度基线）+ 全市场名单（每期翻一遍，机会候选从这里初筛）+ 近 30 天榜单 / 上期爆款的相似款（默认关闭：全市场名单已经包括）
        disc = ensure_discovery(vendor, cfg, state, today)
        category_history.record_discovery(state, disc)  # 月度发现查的就是上个月的类目销量，顺手记进季节历史
        monthly_lists.record_discovery(state, disc)  # 月度发现的头部名单也是持续热销补判的当月名单
        if disc and disc.get("nodes"):
            try:
                universe.run(vendor, cfg, state, disc["nodes"], today)
            except BudgetExhausted:
                log.warn("全市场名单：预算不够，没翻完，机会候选退回月度名单和近 30 天榜单")
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
        uni = state.get("universe") or {}
        screened = universe.screen(state, uni["rows"], today, cfg) if uni.get("date") == today.isoformat() else None
        n_cands = len(screened) if screened is not None else len(tracking.opportunity_candidates(disc, state, cfg, today))
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
        revision = tracking.summarize_revisions(state, run_seq)
        if revision:  # 卖家精灵回头改了多少：每期记下来，看规律稳不稳定（判定还没用它）
            state["revisions"] = ((state.get("revisions") or []) + [{**revision, "date": today.isoformat()}])[-12:]
            log.info(f"日数据修订：上期最后 {revision['window']} 天，{revision['changed']:.0%} 的商品被改过，中位数 ×{revision['median']}")
    except (QuotaExhausted, McpUnavailable) as exc:
        state["calls"][month] = used_this_month + vendor.billable_calls
        store.save(site, master, state)
        raise RunAborted(f"{type(exc).__name__}: {exc}", vendor.billable_calls) from exc
    # 空报告保护：该拉的日数据只拉到很少（一个个都连不上、预算给得太少……）时不出这一期，免得发一份看起来正常、其实是旧数据的报告。
    # 回测：以往 8 期每期都刷新了计划里 ≥98% 的商品
    share = float(cfg["report"].get("min_refresh_share", 0.6))
    if refreshed < max(1, share * len(plan)):
        state["calls"][month] = used_this_month + vendor.billable_calls
        store.save(site, master, state)
        raise RunAborted(f"日数据只刷新了 {refreshed}/{len(plan)} 个（要 ≥{share:.0%}），数据不够，不出这一期", vendor.billable_calls)

    # 核心刷新做完后剩下的预算：补充数据（backfill_extras）。账户额度用完时跳过，照常出报告
    if disc and disc.get("nodes"):
        try:
            backfill_extras(vendor, cfg, state, disc, pool, today, reserve)
        except (QuotaExhausted, McpUnavailable) as exc:
            log.warn(f"补充查询：{exc}，本期跳过，下期再补")

    # 4. 判定 + 核查 + 再判定
    extra_rows = tracking.fresh_rows(state)
    result = analyze.analyze(state, disc, pool, cfg, today, extra_rows, revision)
    jobs = verify.select(result["items"], state, cfg, today)
    checked = 0
    if jobs and vendor.budget.remaining > 0:
        try:
            checked = verify.run_checks(vendor, jobs, state, cfg, today)
        except (QuotaExhausted, McpUnavailable) as exc:
            log.warn(f"核查：{exc}，本期跳过")
        log.info(f"核查 {checked} 个可疑商品")
        if checked:
            result = analyze.analyze(state, disc, pool, cfg, today, extra_rows, revision)
    items = result["items"]
    remember_monthly(state, items, disc, run_seq)
    labels = {i["asin"]: [i["label"], i["fake"]["score"]] for i in items}
    prev_run = state["runs"][-1] if state.get("runs") else None

    # 主材质：增长商品 + 持续热销对照组，查亚马逊商品详情的 Material（查过的永久缓存）
    focus, reference = analysis_groups(items, cfg)
    # 两组交替排：名额不够时两组都有一部分查到亚马逊 Material，主材质对比的口径不会一边倒；
    # 名额还有剩，再查其余增长商品（开发方向的主材质用全部增长商品）和其余上榜商品（卡片上的主材质）
    both = [i for pair in itertools.zip_longest(focus, reference) for i in pair if i is not None]
    listed = analyze.focus_items(items) + [i for i in items if i["label"] in ("surge", "potential", "hot") or analyze.is_seasonal(i)]
    seen = {i["asin"] for i in both}
    for item in listed:
        if item["asin"] not in seen:
            seen.add(item["asin"])
            both.append(item)
    try:
        looked = materials.lookup(vendor, both, state, cfg, today, int(bcfg.get("material_lookups", 15)))
    except (QuotaExhausted, McpUnavailable) as exc:
        looked = 0
        log.warn(f"材质查询：{exc}，本期跳过")
    if looked:
        log.info(f"材质查询 {looked} 个")

    # 5. 报告
    display_now = clock.to_tz(now_utc, cfg["display_timezone"])
    report_id = _report_id(display_now.strftime("%Y-%m-%d"), state.get("reports") or [])
    coverage = {"refreshed": refreshed, "calls": vendor.billable_calls, "budget": limit, "candidates": n_cands,
                "opportunity": mix["opportunity"] + mix["rotate"] + mix["similar"] + mix["screen"], "revision": revision,
                "young": [len(uni.get("young") or {}), mix["young"]] if screened is not None and uni.get("young") else None,
                "universe": len(uni.get("rows") or {}) if screened is not None else None,
                "universe_pages": uni.get("pages") if screened is not None else None,
                "universe_failed": len(uni.get("failed") or []) if screened is not None else None,
                "tides": result.get("tides") or {}}
    ctx = build_report(cfg, secrets, master, site, state, disc, items, report_id=report_id, run_seq=run_seq,
                       generated_at=display_now.strftime("%Y-%m-%d %H:%M"), prev_run=prev_run,
                       coverage=coverage, today=today, events=result.get("events"),
                       seasons=result.get("seasons"), calibration=result.get("season_calibration"),
                       monthly=result.get("monthly_status"), monthly_judged=result.get("monthly_judged"),
                       retro=previous_view(state, disc, items, prev_run, cfg, today, result, extra_rows))
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
    point_to_latest(cfg, site, state["reports"])
    scrub_history_keys(cfg, site, master, state["reports"][:-1])
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
    # 月度名单补判的商品（monthly_run）只留缓存，不留日数据：以前进过池子的旧日数据留着，重新生成报告时会被当成追踪商品
    for asin, rec in list(state["asins"].items()):
        last = max(int(rec.get("last_pool_run", 0)), int(rec.get("monthly_run", 0)))
        if last < run_seq - FORGET_RUNS:
            del state["asins"][asin]
        elif int(rec.get("last_pool_run", 0)) < run_seq:
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


def backfill_extras(vendor: Vendor, cfg: dict, state: dict, disc: dict, pool: list[str], today, reserve: int) -> None:
    """核心刷新做完后剩下的预算（给核查和材质查询留出 reserve），依次给：
    持续热销补判的近 30 天名单（往月名单补齐后每期都查，每个子类目 1 次）→ 类目月度历史（季节模型）→ 往月名单（一次性补）
    → 往月名单在这一期刚补齐的，接着查近 30 天名单，这一期就能补判 → 季节性品类里商品自己往年的月销量。
    账户额度用完（QuotaExhausted）由调用方接住：这些都是补充数据，跳过照常出报告。"""
    bcfg = cfg["budget"]

    def spare(cap: int) -> int:
        return min(cap, max(0, vendor.budget.remaining - reserve))

    months = int(cfg["thresholds"]["hot"].get("list_months", 6))
    lists_ready = monthly_lists.complete(state, disc["nodes"], str(disc["period"]), months)
    if lists_ready and spare(len(disc["nodes"])):
        monthly_lists.fetch_rolling(vendor, cfg, state, disc, today, spare(len(disc["nodes"])))
    if spare(int(bcfg.get("history_calls", 0))):
        category_history.backfill(vendor, cfg, state, disc["nodes"], today, spare(int(bcfg.get("history_calls", 0))))
    if not lists_ready and spare(int(bcfg.get("monthly_list_calls", 0))):
        monthly_lists.backfill(vendor, cfg, state, disc, spare(int(bcfg.get("monthly_list_calls", 0))))
        if monthly_lists.complete(state, disc["nodes"], str(disc["period"]), months) and spare(len(disc["nodes"])):
            monthly_lists.fetch_rolling(vendor, cfg, state, disc, today, spare(len(disc["nodes"])))
    # 季节性商品要看商品自己往年的月销量（sales_trend.py）：季节性品类（类目历史够了才认得出）里上架满一年的追踪商品
    sc = cfg["thresholds"].get("seasonality")
    if sc and spare(int(bcfg.get("trend_calls", 0))):
        profiles = {n["path"]: seasonality.profile(category_history.series(state, n["path"]), sc) for n in disc["nodes"]}
        asins = sales_trend.candidates(state, pool, profiles, today, cfg)
        if asins:
            sales_trend.fetch(vendor, cfg, state, asins, today, spare(int(bcfg.get("trend_calls", 0))))


def remember_monthly(state: dict, items: list[dict], disc: dict | None, run_seq: int) -> None:
    """月度名单补判出的持续热销也记进 state["asins"]（不拉日数据），主材质查询和主图识别的结果才能缓存下来、下期接着用。"""
    products = (disc or {}).get("products") or {}
    for item in items:
        if item.get("daily", True):
            continue
        rec = state["asins"].setdefault(item["asin"], {"asin": item["asin"], "obs": []})
        tracking.remember_meta(rec, products.get(item["asin"]))
        rec["monthly_run"] = run_seq


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
    """外观/材质分析的两组：增长商品（按势头）和持续热销对照组（按月销量）。
    持续热销里带“上升中”的也是增长商品，不进对照组：同一个商品两边都算，会把两组的差距抹小。"""
    llm = cfg["llm"]
    growth = analyze.focus_items(items)
    focus = growth[: int(llm.get("vision_focus", 20))]
    taken = {i["asin"] for i in growth}
    reference = [i for i in analyze.sections(items, 10_000)["hot"]
                 if i["asin"] not in taken][: int(llm.get("vision_reference", 10))]
    return focus, reference


def build_report(cfg: dict, secrets: Secrets, master: bytes, site: Path, state: dict, disc: dict | None,
                 items: list[dict], *, report_id: str, run_seq: int, generated_at: str,
                 prev_run: dict | None, coverage: dict, today, events: dict | None = None,
                 seasons: dict | None = None, calibration: dict | None = None, monthly: dict | None = None,
                 monthly_judged: list[str] | None = None, retro: dict | None = None) -> dict:
    """板块、外观与工艺特征、与上期对比、文字简报 → 渲染 → 加密写入 reports/<id>.html。返回报告上下文。
    events：本期识别出的大促日（analyze.analyze 的结果），写进“数据说明”；seasons：各子类目的品类季节（同上）；
    retro：上期没追踪的商品回看上期那天的判定（previous_view），“与上期对比”用。"""
    sec = analyze.sections(items, int(cfg["report"]["top_n"]))
    trait = analyze.compute_traits(items, disc, state, today, cfg)

    # 主图外观识别 + 主材质：增长商品，另取持续热销对照组
    focus, reference = analysis_groups(items, cfg)
    tagged = vision.tag_items(focus + reference, state, cfg, secrets.llm_api_key)
    if tagged:
        log.info(f"主图识别 {tagged} 张")
    # 开发方向：排在前面的几个子类目，增长商品和同子类目头部商品都看主图，两边特征的来源才一致
    growth = analyze.focus_items(items)
    tagged = vision.tag_items(directions.vision_targets(growth, disc, cfg), state, cfg, secrets.llm_api_key,
                              untracked=True)
    if tagged:
        log.info(f"开发方向主图识别 {tagged} 张")
    vision.prune_extra(state, set(((disc or {}).get("products") or {})))
    trait["vision"] = vision.summarize(focus, reference, state,
                                       min_gap=float(cfg["thresholds"]["appearance"]["image_min_gap"]))
    trait["materials"] = materials.compare(analyze.focus_items(items), reference, state)
    trait["appearance"] = merge_appearance(trait, trait["vision"], cfg)
    for item in items:
        rec = state["asins"].get(item["asin"]) or {}
        item["material"], item["material_source"] = materials.best(rec, item["title"])
        item["look"] = vision.of(state, item["asin"]).get("summary") or ""
    trait["directions"] = directions.compute(growth, state, trait["appearance"], cfg, disc=disc, seasons=seasons)

    labels = {i["asin"]: i["label"] for i in items}
    as_of_values = [i["as_of"] for i in items if i.get("as_of") and i.get("daily", True)]
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
                                else None, labels, monthly={i["asin"] for i in items if not i.get("daily", True)},
                                retro=retro),
        "monthly": monthly or {},
        "monthly_judged": monthly_judged or [],
        "coverage": {**coverage, "stale": sum(1 for i in items if i["stale"])},
        "cfg": cfg,
        "top_n": int(cfg["report"]["top_n"]),
        "events": events or {},
        "seasons": seasons or {},
        "season_calibration": calibration,
        "season_nodes": (disc or {}).get("nodes") or [],
    }
    ctx["summary"], ctx["summary_source"] = narrative.summary(ctx, cfg, secrets.llm_api_key)
    # 往期链接不带密钥：带的话一份报告里有前 6 期的密钥、一期期往回连，泄露一个钉钉链接就能打开全部往期。
    # 往期报告从钉钉里那一期的消息打开（同一个浏览器标签页里打开过的，记着密钥，点这里也能直接打开）
    ctx["history"] = [{"id": old_id, "href": f"{old_id}.html"}
                      for old_id in list(reversed(state.get("reports") or [])) if old_id != report_id][:6]

    page = report_render.render(ctx)
    _write_shell(cfg, site, report_id, shell.seal(master, report_id, page), latest_id=report_id)
    return ctx


def previous_view(state: dict, disc: dict | None, items: list[dict], prev_run: dict | None, cfg: dict, today,
                  result: dict, extra_rows: dict | None = None) -> dict[str, str]:
    """本期有日数据、上期没追踪（也没被月度名单补判过）的商品，回看上期那天会被判成什么（analyze.retro_labels）。
    上期那天的日数据截止日 = 本期的 − 两期相隔的天数（日数据的日期和运行日期差得一样）。"""
    prev_day = clock.parse_day((prev_run or {}).get("date"))
    data_date = result.get("data_date")
    if not prev_run or prev_day is None or data_date is None or prev_day >= today:
        return {}
    seen = set(prev_run.get("labels") or {}) | set(prev_run.get("monthly_judged") or [])
    asins = [i["asin"] for i in items if i.get("daily", True) and i["asin"] not in seen]
    if not asins:
        return {}
    data_then = data_date - timedelta(days=(today - prev_day).days)
    return analyze.retro_labels(state, disc, asins, cfg, prev_day, data_then, extra_rows,
                                (result.get("events") or {}).get("days") or frozenset(), result.get("profiles"))


def _write_shell(cfg: dict, site: Path, report_id: str, envelope: dict, latest_id: str) -> None:
    reports_dir = site / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{report_id}.html").write_text(
        shell.encrypted_page(f"{cfg['report']['title']} {report_id}", envelope, report_id, latest_id), encoding="utf-8")


def reseal(cfg: dict, secrets: Secrets, site_dir: str | os.PathLike) -> int:
    """把 site 里的历史数据和全部报告重新加密一遍：换一个随机 IV，内容和钉钉链接里的密钥都不变。
    GitHub 的推送保护会扫描推上去的文件，密文是随机字符，偶尔有一段长得像某种访问密钥（实测 2026-10-23 data/state.enc
    被认成火山引擎 Access Key ID，推送被拒）；换了 IV 密文就全变了。工作流推送被拒时调用它再推。返回重新加密的文件数。"""
    master = crypto.parse_master_key(secrets.report_key)
    site = Path(site_dir)
    count = 0
    if store.state_path(site).exists():
        store.save(site, master, store.load(site, master))
        count += 1
    for path in sorted((site / "reports").glob("*.html")):
        payload = shell.read_payload(path.read_text(encoding="utf-8"))
        rid = payload["id"]
        page = HISTORY_KEY_RE.sub(r'href="\1"', shell.unseal(master, payload))
        _write_shell(cfg, site, rid, shell.seal(master, rid, page), payload.get("latest") or rid)
        count += 1
    log.info(f"已重新加密 {count} 个文件")
    return count


HISTORY_KEY_RE = re.compile(r'href="(\d{4}-\d{2}-\d{2}(?:-\d+)?\.html)#k=[A-Za-z0-9_-]+"')


def scrub_history_keys(cfg: dict, site: Path, master: bytes, report_ids: list[str]) -> int:
    """以前生成的报告里，“往期”链接带着前几期的密钥（一份报告就能连着打开全部往期）：解密后去掉这些密钥再加密回去。
    只改有这种链接的，改过的不会再改。返回改了几份。"""
    count = 0
    for rid in report_ids:
        path = site / "reports" / f"{rid}.html"
        if not path.exists():
            continue
        payload = shell.read_payload(path.read_text(encoding="utf-8"))
        page = shell.unseal(master, payload)
        clean = HISTORY_KEY_RE.sub(r'href="\1"', page)
        if clean != page:  # 顺带压缩
            _write_shell(cfg, site, payload["id"], shell.seal(master, payload["id"], clean), payload.get("latest") or payload["id"])
            count += 1
    if count:
        log.info(f"往期报告里的密钥链接已去掉：{count} 份")
    return count


def point_to_latest(cfg: dict, site: Path, report_ids: list[str]) -> None:
    """往期报告的外壳改记最新一期的 ID，打开后顶部显示“回到最新报告”。只换外壳，密文原样保留。"""
    latest = report_ids[-1]
    for old_id in report_ids[:-1]:
        path = site / "reports" / f"{old_id}.html"
        if not path.exists():
            continue
        payload = shell.read_payload(path.read_text(encoding="utf-8"))
        if payload.get("latest") != latest:
            _write_shell(cfg, site, old_id, shell.envelope_of(payload), latest)


def _run_record(cfg: dict, ctx: dict, date_text: str, labels: dict) -> dict:
    return {
        "id": ctx["report_id"], "seq": ctx["run_seq"], "date": date_text, "labels": labels,
        "monthly_judged": ctx.get("monthly_judged") or [],
        "scope_key": discovery.scope_key(cfg),
        "counts": ctx["sections"]["counts"], "generated_at": ctx["generated_at"],
        "coverage": {k: ctx["coverage"].get(k) for k in ("refreshed", "calls", "budget", "candidates", "opportunity", "revision",
                                                         "universe", "universe_pages", "universe_failed", "young")},
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
    disc = discovery.apply_product_filter(state.get("discovery"), cfg)
    today = clock.parse_day(last.get("date")) or clock.now_in(cfg["vendor_timezone"]).date()
    extra_rows = tracking.fresh_rows(state)
    # 只用那一期真正拉了日数据的商品（labels 里还有月度名单补判的，它们可能留着更早进池时的旧日数据）；
    # 之后有一期中途中止（RunAborted）时，它进池的商品记的是下一期的期数，也算
    # 商品级范围条件改了的，按新条件去掉（和 run 的选池一样）
    pool = [a for a in last.get("labels") or {} if int((state["asins"].get(a) or {}).get("last_pool_run", -1)) >= last["seq"]
            and discovery.keep_product(state["asins"].get(a), cfg)]
    result = analyze.analyze(state, disc, pool, cfg, today, extra_rows, (last.get("coverage") or {}).get("revision"))
    items = result["items"]
    remember_monthly(state, items, disc, last["seq"])
    coverage = last.get("coverage") or {}
    if not coverage:
        month = (last.get("date") or "")[:7]
        same_month = [r for r in state["runs"] if (r.get("date") or "")[:7] == month]
        coverage = {"refreshed": sum(1 for r in state["asins"].values() if r.get("fetched_run") == last["seq"]),
                    "calls": state["calls"].get(month) if len(same_month) == 1 else None, "budget": None}
    coverage = {**coverage, "tides": result.get("tides") or {}}
    prev_run = state["runs"][-2] if len(state["runs"]) >= 2 else None
    ctx = build_report(cfg, secrets, master, site, state, disc, items, report_id=last["id"], run_seq=last["seq"],
                       generated_at=last.get("generated_at") or clock.to_tz(datetime.now(timezone.utc),
                                                                            cfg["display_timezone"]).strftime("%Y-%m-%d %H:%M"),
                       prev_run=prev_run, coverage=coverage,
                       today=today, events=result.get("events"), seasons=result.get("seasons"),
                       calibration=result.get("season_calibration"), monthly=result.get("monthly_status"),
                       monthly_judged=result.get("monthly_judged"),
                       retro=previous_view(state, disc, items, prev_run, cfg, today, result, extra_rows))
    point_to_latest(cfg, site, state.get("reports") or [last["id"]])
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
    if not url.startswith("http"):  # 只有相对路径的按钮在钉钉里点不开，宁可不发
        log.warn("无法确定报告网址，未推送钉钉：请设置 REPORT_BASE_URL 或 config.yaml 的 report.base_url")
        return []
    payload = dingtalk.action_card(last["notify"]["title"], last["notify"]["text"], url,
                                   open_in_browser=bool(cfg["notify"]["open_in_browser"]))
    results = dingtalk.send(secrets.dingtalk_webhooks, secrets.dingtalk_secrets, payload, client=client)
    log.info(f"钉钉推送：成功 {sum(results)}/{len(results)}")
    return results

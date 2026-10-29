"""一次完整运行：发现 → 近 30 天榜单 / 相似款 → 选池 → 刷新日数据 → 判定 → 核查 → 报告 → 加密发布 →（可选）钉钉。"""
from __future__ import annotations

import itertools
import os
import re
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import (analyze, clock, crypto, discovery, log, materials, monthly_lists, narrative,
               store, tracking, universe, verify, vision)
from .config import Secrets, report_base_url
from .detect import diff as diffing
from .detect import directions
from .detect.traits import merge_appearance
from .notify import dingtalk
from .report import render as report_render
from .report import shell
from .mcp_client import McpUnavailable
from .vendor import Budget, BudgetExhausted, QuotaExhausted, Transport, Vendor, mcp_transport


FORGET_RUNS = 20  # 连续这么多期没进池的 ASIN 从状态里删除


REMIND_DAYS = 7  # 一直被同一个原因挡住时，隔这么多天再提醒一次


class RunAborted(RuntimeError):
    """核心数据没拉完就中止（账户额度用完 / 卖家精灵一直连不上 / 拉到的日数据太少），或拉完以后程序出错：不出报告，
    但已拉到的数据和调用次数已经存进 site。
    kind：quota / outage / thin / crash；repeat：同一个原因上次已经提醒过、还不到 REMIND_DAYS 天（这次不必再发钉钉告警；
    程序出错每次都提醒，每次重跑都要花调用）。"""

    def __init__(self, reason: str, calls: int, kind: str = "outage", repeat: bool = False):
        super().__init__(reason)
        self.calls, self.kind, self.repeat = calls, kind, repeat


def _abort(site: Path, master: bytes, state: dict, month: str, calls_before: int, calls: int, today, reason: str,
           kind: str) -> RunAborted:
    """记下调用次数和“被挡住”的原因（state["blocked"]），存盘，返回要抛出的 RunAborted。
    同一个原因上次已经提醒过、还不到 REMIND_DAYS 天的，标 repeat（命令行不再让工作流失败，免得每天一条一样的告警）。"""
    state["calls"][month] = calls_before + calls
    prev = state.get("blocked") or {}
    last = clock.parse_day(prev.get("alerted_on"))
    repeat = bool(kind != "crash" and prev.get("kind") == kind and last and (today - last).days < REMIND_DAYS)
    state["blocked"] = {"kind": kind, "since": prev.get("since") if prev.get("kind") == kind else today.isoformat(),
                        "alerted_on": prev.get("alerted_on") if repeat else today.isoformat(), "reason": reason[:300]}
    store.save(site, master, state)
    return RunAborted(reason, calls, kind, repeat)


def crash_reason(exc: BaseException) -> str:
    """程序出错的原因：异常类型 + 出错的位置（最里面 3 层的 文件:行 函数）。不带异常信息：可能含 ASIN、标题，
    公开仓库的 Actions 日志谁都能看（完整的报错只在本地打印）。"""
    frames = traceback.extract_tb(exc.__traceback__)[-3:]
    where = " ← ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in reversed(frames))
    return f"程序出错 {type(exc).__name__}（{where}）"


def _discard_unpublished(cfg: dict, site: Path, report_id: str | None, report_ids: list[str]) -> None:
    """出错中止时：这期已经写出的报告删掉，往期报告的“回到最新报告”和目录页改回原来的最新一期。"""
    if not report_id or report_id in report_ids:
        return
    path = site / "reports" / f"{report_id}.html"
    if path.exists():
        path.unlink()
    if report_ids:
        point_to_latest(cfg, site, report_ids)
        (site / "index.html").write_text(shell.index_page(cfg["report"]["title"], report_ids), encoding="utf-8")


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


def bcfg_tz(cfg: dict) -> str:
    """调用次数按哪个时区的月份记：budget.quota_timezone（卖家精灵套餐按自然月、北京时间），没配时用美国时间。"""
    return cfg["budget"].get("quota_timezone") or cfg["vendor_timezone"]


def runs_left(now_utc: datetime, cfg: dict) -> int:
    """本月（套餐的月份，见 bcfg_tz）还剩几期，含这一期：每 min_days_between_runs 天一期，今天之后还放得下几期。
    例：两周一期，11-01 → 3 期（1、15、29 号），11-29 → 1 期，12-13 → 2 期。"""
    day = clock.to_tz(now_utc, bcfg_tz(cfg)).date()
    step = max(1, int(float(cfg["schedule"]["min_days_between_runs"])))
    return 1 + (clock.days_in_month(clock.month_key(day)) - day.day) // step


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
    discovery.migrate_scope_keys(state, cfg)
    now_utc = now or datetime.now(timezone.utc)

    due, why = is_due(state, cfg, now_utc)
    if not due and not force:
        log.info(f"跳过：{why}")
        return RunResult(skipped_reason=why)
    log.info(f"开始运行（{why}{'，强制' if force and not due else ''}）")

    today = clock.to_tz(now_utc, cfg["vendor_timezone"]).date()
    run_seq = int(state.get("run_seq") or 0) + 1
    # 调用次数按卖家精灵套餐的月份记（套餐次数按自然月分配、每月 1 号 0 点重置：按北京时间算，和数据的美国时间月份不一样）
    month = clock.month_key(clock.to_tz(now_utc, bcfg_tz(cfg)).date())
    used_this_month = int(state["calls"].get(month, 0))
    bcfg = cfg["budget"]
    runs = runs_left(now_utc, cfg)
    if budget is not None:
        limit = budget
    elif bcfg.get("split") == "even":
        # 本月剩下的额度按本月还剩几期（含这一期）平均分：以前每期固定 1,300 / 1,500，11 月头几期都花满，
        # 最后一期（11-29，黑五那周）只剩约 1,200 次；12 月只有 4 期、又有 1,800 次用不上
        left = max(0, int(bcfg["monthly_cap"]) - used_this_month)
        limit = min(int(bcfg.get("max_per_run") or left), left // runs)
    else:
        # 要重新发现子类目、或者头一个月要拉上个月的全市场月度名单当基准的那一期，给多一点预算
        busy = needs_discovery(state, cfg, today) or universe.baseline_period(state, today) is not None
        base = bcfg["bootstrap_run"] if busy else bcfg["per_run"]
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
    log.info(f"本期调用预算 {limit} 次（本月已用 {used_this_month}/{bcfg['monthly_cap']}，本月还有 {runs} 期）")

    # 核心数据（名单、日数据）拉到一半，账户额度用完或卖家精灵一直连不上：不出这一期，但已经拉到的数据和花掉的调用次数照样存下来
    #（工作流照样保存到 site 分支），下次运行接着用，本月调用数也不会少记
    try:
        # 1. 发现（月度基线）+ 全市场名单（每期翻一遍，机会候选从这里初筛）+ 近 30 天榜单 / 上期爆款的相似款（默认关闭：全市场名单已经包括）
        disc = ensure_discovery(vendor, cfg, state, today)
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
            # 持续热销补判要的当期近 30 天销量：全市场名单覆盖不到头部的子类目另查近 30 天名单（全市场名单没翻成时全部查）。
            # 放在拉日数据之前：以前放在最后用剩下的预算，初筛把预算占满时整期不查，补判用的是好几天前的旧名单
            months = int(cfg["thresholds"]["hot"].get("list_months", 6))
            # 按件数的往月名单（便宜、走量的头部判持续热销用）：一次性补约 145 次。也放在拉日数据之前，否则初筛把预算占满时永远补不上
            if not monthly_lists.complete(state, disc["nodes"], str(disc["period"]), months, "units"):
                monthly_lists.backfill(vendor, cfg, state, disc, int(bcfg.get("monthly_list_calls", 0)), "units")
            if monthly_lists.complete(state, disc["nodes"], str(disc["period"]), months):
                calls = monthly_lists.fetch_rolling(vendor, cfg, state, disc, today, len(disc["nodes"]),
                                                    rolling_nodes(state, disc, cfg, today))
                log.info(f"持续热销补判：查了 {len(state['monthly_lists']['rolling']['nodes'])} 个子类目的近 30 天名单（{calls} 次调用）")

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
        # 这一期上次中止前已经拉过的（中止后第二天接着跑）：不重拉，也算这一期刷新过的
        planned = set(plan)
        already = sum(1 for a in pool if a not in planned and (state["asins"].get(a) or {}).get("fetched_run") == run_seq)
        refreshed = 0
        try:
            refreshed = tracking.refresh(vendor, plan, state, disc, cfg, run_seq)
        except BudgetExhausted:
            log.warn("追踪阶段预算用完")
        log.info(f"刷新日数据 {refreshed}/{len(plan)} 个" + (f"（另有 {already} 个是这一期中止前已经拉过的）" if already else ""))
        revision = tracking.summarize_revisions(state, run_seq)
        if revision:  # 卖家精灵回头改了多少：每期记下来，看规律稳不稳定（判定还没用它）
            state["revisions"] = ((state.get("revisions") or []) + [{**revision, "date": today.isoformat()}])[-12:]
            log.info(f"日数据修订：上期最后 {revision['window']} 天，{revision['changed']:.0%} 的商品被改过，中位数 ×{revision['median']}")
    except (QuotaExhausted, McpUnavailable) as exc:
        raise _abort(site, master, state, month, used_this_month, vendor.billable_calls, today,
                     f"{type(exc).__name__}: {exc}", "quota" if isinstance(exc, QuotaExhausted) else "outage") from exc
    except Exception as exc:  # noqa: BLE001 — 程序出错：同样存下已拉到的数据和调用次数（见下面 _publish 那段）
        log.detail(traceback.format_exc())
        raise _abort(site, master, state, month, used_this_month, vendor.billable_calls, today,
                     crash_reason(exc), "crash") from exc
    # 空报告保护：该拉的日数据只拉到很少（一个个都连不上、预算给得太少……）时不出这一期，免得发一份看起来正常、其实是旧数据的报告。
    # 回测：以往 8 期每期都刷新了计划里 ≥98% 的商品
    # 按这一期一共拉到的算（中止后接着跑时，上次已经拉过的也算），否则接着跑的那次“没有要拉的”会被当成数据太少
    share = float(cfg["report"].get("min_refresh_share", 0.6))
    refreshed += already
    if refreshed < max(1, share * (len(plan) + already)):
        raise _abort(site, master, state, month, used_this_month, vendor.billable_calls, today,
                     f"日数据只刷新了 {refreshed}/{len(plan) + already} 个（要 ≥{share:.0%}），数据不够，不出这一期", "thin")

    # 拉完数据以后（补充查询、判定、核查、报告、存状态）出错：已拉到的数据和花掉的调用次数照样存下来（工作流照样保存），
    # 下次运行接着用、不重拉日数据，本月调用数也不少记。以前只有“中止”才保存，别的报错整期白跑：调用次数不记账，
    # 第二天整期重来、每天重复，几天就能把当月额度烧光
    keep = {"reports": list(state.get("reports") or []), "runs": list(state.get("runs") or []),
            "run_seq": state.get("run_seq"), "last_success": state.get("last_success")}
    progress: dict = {}
    try:
        out = _publish(cfg, secrets, site, master, state, vendor, progress, disc=disc, pool=pool, today=today,
                       reserve=reserve, revision=revision, run_seq=run_seq, now_utc=now_utc, limit=limit,
                       refreshed=refreshed, mix=mix, n_cands=n_cands, screened=screened, uni=uni, month=month,
                       used_this_month=used_this_month)
    except Exception as exc:  # noqa: BLE001
        state.update(keep)
        _discard_unpublished(cfg, site, progress.get("report_id"), keep["reports"])
        log.detail(traceback.format_exc())
        raise _abort(site, master, state, month, used_this_month, vendor.billable_calls, today,
                     crash_reason(exc), "crash") from exc
    if notify:
        out.notified = send_last(cfg, secrets, site)
    return out


def _publish(cfg: dict, secrets: Secrets, site: Path, master: bytes, state: dict, vendor: Vendor, progress: dict, *,
             disc, pool, today, reserve, revision, run_seq, now_utc, limit, refreshed, mix, n_cands, screened, uni,
             month, used_this_month) -> RunResult:
    """核心数据拉完以后：补充查询 → 判定 → 核查 → 材质 → 报告 → 存状态。出错由 run 接住（存下数据和调用次数）。
    progress["report_id"]：已经写出的这一期报告（出错时删掉）。"""
    bcfg = cfg["budget"]

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
    listed = analyze.focus_items(items) + [i for i in items if i["label"] in ("surge", "potential", "hot")]
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
    report_id = progress["report_id"] = _report_id(display_now.strftime("%Y-%m-%d"), state.get("reports") or [])
    coverage = {"refreshed": refreshed, "calls": vendor.billable_calls, "budget": limit, "candidates": n_cands,
                "opportunity": mix["opportunity"] + mix["rotate"] + mix["similar"] + mix["screen"], "revision": revision,
                "young": [len(uni.get("young") or {}), mix["young"]] if screened is not None and uni.get("young") else None,
                "universe": len(uni.get("rows") or {}) if screened is not None else None,
                "universe_pages": uni.get("pages") if screened is not None else None,
                "universe_failed": len(uni.get("failed") or []) if screened is not None else None,
                "tides": result.get("tides") or {}, "revision_check": result.get("revision_check")}
    ctx = build_report(cfg, secrets, master, site, state, disc, items, report_id=report_id, run_seq=run_seq,
                       generated_at=display_now.strftime("%Y-%m-%d %H:%M"), prev_run=prev_run,
                       coverage=coverage, today=today, events=result.get("events"),
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
    state.pop("blocked", None)
    drop_seasonal(state)
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
    return out


def drop_seasonal(state: dict) -> None:
    """季节判断已经去掉（2026-10-26）：以前存下的子类目 3 年月度历史、商品往年月销量不再用，丢掉免得数据越存越大。"""
    state.pop("category_history", None)
    for rec in (state.get("asins") or {}).values():
        rec.pop("sales_trend", None)


def backfill_extras(vendor: Vendor, cfg: dict, state: dict, disc: dict, pool: list[str], today, reserve: int) -> None:
    """核心刷新做完后剩下的预算（给核查和材质查询留出 reserve）：往月名单还没补齐时一次性补，
    这一期刚补齐的，接着查近 30 天名单（rolling_nodes），这一期就能补判。往月名单已经齐了的，近 30 天名单在 run 的核心阶段查过。
    账户额度用完（QuotaExhausted）由调用方接住：这些都是补充数据，跳过照常出报告。"""
    bcfg = cfg["budget"]

    def spare(cap: int) -> int:
        return min(cap, max(0, vendor.budget.remaining - reserve))

    months = int(cfg["thresholds"]["hot"].get("list_months", 6))
    if monthly_lists.complete(state, disc["nodes"], str(disc["period"]), months):
        return
    if spare(int(bcfg.get("monthly_list_calls", 0))):
        monthly_lists.backfill(vendor, cfg, state, disc, spare(int(bcfg.get("monthly_list_calls", 0))))
        if monthly_lists.complete(state, disc["nodes"], str(disc["period"]), months) and spare(len(disc["nodes"])):
            monthly_lists.fetch_rolling(vendor, cfg, state, disc, today, spare(len(disc["nodes"])),
                                        rolling_nodes(state, disc, cfg, today))


def rolling_nodes(state: dict, disc: dict, cfg: dict, today) -> set[str] | None:
    """要查近 30 天名单的子类目：这期翻了全市场名单的，只查它覆盖不到头部的（monthly_lists.thin_nodes）；没翻成的全部查（None）。"""
    uni = state.get("universe") or {}
    if uni.get("date") != today.isoformat() or not uni.get("rows"):
        return None
    return monthly_lists.thin_nodes(disc, cfg, uni.get("failed"))


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
                 monthly: dict | None = None, monthly_judged: list[str] | None = None, retro: dict | None = None) -> dict:
    """板块、外观与工艺特征、与上期对比、文字简报 → 渲染 → 加密写入 reports/<id>.html。返回报告上下文。
    events：本期识别出的大促日（analyze.analyze 的结果），写进“数据说明”；
    retro：上期没追踪的商品回看上期那天的判定（previous_view），“与上期对比”用。"""
    sec = analyze.sections(items, int(cfg["report"]["top_n"]))
    trait = analyze.compute_traits(items, disc, state, today, cfg)

    # 主图外观识别 + 主材质：增长商品，另取持续热销对照组
    focus, reference = analysis_groups(items, cfg)
    tagged = vision.tag_items(focus + reference, state, cfg, secrets.llm_api_key)
    if tagged:
        log.info(f"主图识别 {tagged} 张")
    # 开发方向：够格的子类目（directions.max > 0 时只看前几个），增长商品和同子类目头部商品都看主图，两边特征的来源才一致
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
    trait["directions"] = directions.compute(growth, state, trait["appearance"], cfg, disc=disc)
    trait["direction_overview"] = directions.overview(items, growth, trait["directions"], disc, cfg)

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
                                retro=retro, growth=growth_map(items)),
        "monthly": monthly or {},
        "monthly_judged": monthly_judged or [],
        "coverage": {**coverage, "stale": sum(1 for i in items if i["stale"])},
        "cfg": cfg,
        "top_n": int(cfg["report"]["top_n"]),
        "events": events or {},
        "nodes": (disc or {}).get("nodes") or [],
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
                                (result.get("events") or {}).get("days") or frozenset(),
                                (prev_run.get("coverage") or {}).get("tides"))


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


def growth_map(items: list[dict]) -> dict[str, str]:
    """本期的增长商品 {ASIN: surge / potential / rising}（analyze.growth_kind）。"""
    return {i["asin"]: kind for i in items if (kind := analyze.growth_kind(i))}


def _run_record(cfg: dict, ctx: dict, date_text: str, labels: dict) -> dict:
    return {
        "id": ctx["report_id"], "seq": ctx["run_seq"], "date": date_text, "labels": labels,
        # 下期“与上期对比”用：本期提示了大促后数据待修订（events.bsr_lift）时，下期写明这些增长商品哪些仍成立（diff.compare）
        "growth": growth_map(ctx["items"]), "lift": bool((ctx.get("events") or {}).get("lift")),
        "monthly_judged": ctx.get("monthly_judged") or [],
        "scope_key": discovery.scope_key(cfg),
        "counts": ctx["sections"]["counts"], "generated_at": ctx["generated_at"],
        # tides：下一期回看这期没追踪的商品时，按这期的品类大盘扣（previous_view）
        "coverage": {k: ctx["coverage"].get(k) for k in ("refreshed", "calls", "budget", "candidates", "opportunity", "revision",
                                                         "universe", "universe_pages", "universe_failed", "young", "tides")},
        "notify": {"title": f"{cfg['report']['title']} {ctx['report_id']}",
                   "text": dingtalk.build_text(ctx, int(cfg["notify"]["max_items"]))},
    }


def rerender(cfg: dict, secrets: Secrets, site_dir: str | os.PathLike) -> RunResult:
    """用已保存的数据重新生成最近一期报告（不调用卖家精灵）。报告 ID 和链接不变，
    钉钉里已经发出的链接打开就是新版本。用于改进报告样式或分析方法后刷新。"""
    master = crypto.parse_master_key(secrets.report_key)
    site = Path(site_dir)
    state = store.load(site, master)
    discovery.migrate_scope_keys(state, cfg)
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
    coverage = {**coverage, "tides": result.get("tides") or {}, "revision_check": result.get("revision_check")}
    prev_run = state["runs"][-2] if len(state["runs"]) >= 2 else None
    ctx = build_report(cfg, secrets, master, site, state, disc, items, report_id=last["id"], run_seq=last["seq"],
                       generated_at=last.get("generated_at") or clock.to_tz(datetime.now(timezone.utc),
                                                                            cfg["display_timezone"]).strftime("%Y-%m-%d %H:%M"),
                       prev_run=prev_run, coverage=coverage,
                       today=today, events=result.get("events"), monthly=result.get("monthly_status"),
                       monthly_judged=result.get("monthly_judged"),
                       retro=previous_view(state, disc, items, prev_run, cfg, today, result, extra_rows))
    point_to_latest(cfg, site, state.get("reports") or [last["id"]])
    scrub_history_keys(cfg, site, master, (state.get("reports") or [])[:-1])
    labels = {i["asin"]: [i["label"], i["fake"]["score"]] for i in items}
    state["runs"][-1] = _run_record(cfg, ctx, last.get("date") or today.isoformat(), labels)
    drop_seasonal(state)
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

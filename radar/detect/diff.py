"""与上期对比，每个商品只归入一类：
  依旧爆火 = 上期、本期都在突然爆火板块；
  新进入某板块 = 本期在该板块、上期不在（上期在别的板块的，记下上期板块）；
  不再爆火 = 上期爆火、本期留在追踪里但不在任何板块（和“回落”不是一个概念）；
  补判 = 月度名单补判出的持续热销（analyze.monthly_hot_items）里上期没判过的：不是新进入，是这期才开始看它。
上期没追踪的商品（追踪范围扩大后很多）：上期的板块用本期拉到的日数据回看上期那天的判定（analyze.retro_labels），
回看上期就已经是的不算新进入。
上期提示了“大促后数据待修订”（events.bsr_lift，那期新出现的增长请等下期确认）时，再列上期的增长商品（突然爆火 / 潜力 / 上升中）
本期还是不是增长商品（recheck）：只有“依旧爆火 / 不再爆火”时，潜力、上升中不再成立的哪里都看不到
（实测 10-14 那期提示后，10-20 那期上期 51 个增长商品里 20 个不再成立，“不再爆火”只能看到其中 5 个）。"""
from __future__ import annotations

LABELS = ("surge", "potential", "hot", "fake")


def compare(prev_run: dict | None, current: dict[str, str], monthly: set[str] = frozenset(),
            retro: dict[str, str] | None = None, growth: dict[str, str] | None = None) -> dict:
    """monthly：本期月度名单补判出的持续热销；retro：上期没追踪的商品回看上期那天的判定；
    growth：本期的增长商品 {ASIN: surge / potential / rising}（analyze.growth_kind）。
    结果的 retro = 上期板块来自回看的商品（只列出现在依旧 / 新进入 / 不再爆火里的）；
    recheck = 上期提示了大促后数据待修订时，上期的增长商品本期 仍是（held）/ 不再是（lost，值是上期是哪种）/ 本期没追踪（gone），
    其余时候为 None。"""
    if not prev_run:
        return {"first": True, "still": [], "new": {label: [] for label in LABELS}, "moved_from": {}, "cooled": [],
                "covered": [], "retro": [], "recheck": None}
    prev = {asin: value[0] for asin, value in (prev_run.get("labels") or {}).items()}
    seen = set(prev) | set(prev_run.get("monthly_judged") or [])
    looked = {a: label for a, label in (retro or {}).items() if a not in seen}
    covered = sorted(a for a in monthly if a not in seen)
    prev = {**looked, **prev}
    still = [a for a, l in current.items() if l == "surge" and prev.get(a) == "surge"]
    new = {label: [a for a, l in current.items() if l == label and prev.get(a) != label and a not in covered]
           for label in LABELS}
    moved_from = {a: prev[a] for asins in new.values() for a in asins if prev.get(a) in LABELS}
    cooled = [a for a, l in prev.items() if l == "surge" and current.get(a) in ("watch", "low")]
    shown = set(still) | set(cooled) | {a for asins in new.values() for a in asins}
    return {"first": False, "prev_id": prev_run.get("id"), "still": still, "new": new, "moved_from": moved_from,
            "cooled": cooled, "covered": covered, "retro": sorted(a for a in looked if a in shown),
            "recheck": recheck(prev_run, current, growth or {})}


def recheck(prev_run: dict, current: dict[str, str], growth: dict[str, str]) -> dict | None:
    """上期提示了大促后数据待修订（run["lift"]）、记了增长商品（run["growth"]）时：那些商品本期还是不是增长商品。"""
    before = prev_run.get("growth") or {}
    if not prev_run.get("lift") or not before:
        return None
    return {"total": len(before),
            "held": [a for a in before if a in growth],
            "lost": {a: kind for a, kind in before.items() if a not in growth and a in current},
            "gone": [a for a in before if a not in current]}

"""与上期对比，每个商品只归入一类：
  依旧爆火 = 上期、本期都在突然爆火板块；
  新进入某板块 = 本期在该板块、上期不在（上期在别的板块的，记下上期板块）；
  不再爆火 = 上期爆火、本期留在追踪里但不在任何板块（和“回落”不是一个概念）；
  补判 = 月度名单补判出的持续热销（analyze.monthly_hot_items）里上期没判过的：不是新进入，是这期才开始看它。"""
from __future__ import annotations

LABELS = ("surge", "potential", "hot", "fake")


def compare(prev_run: dict | None, current: dict[str, str], monthly: set[str] = frozenset()) -> dict:
    """monthly：本期月度名单补判出的持续热销。"""
    if not prev_run:
        return {"first": True, "still": [], "new": {label: [] for label in LABELS}, "moved_from": {}, "cooled": [],
                "covered": []}
    prev = {asin: value[0] for asin, value in (prev_run.get("labels") or {}).items()}
    seen = set(prev) | set(prev_run.get("monthly_judged") or [])
    covered = sorted(a for a in monthly if a not in seen)
    still = [a for a, l in current.items() if l == "surge" and prev.get(a) == "surge"]
    new = {label: [a for a, l in current.items() if l == label and prev.get(a) != label and a not in covered]
           for label in LABELS}
    moved_from = {a: prev[a] for asins in new.values() for a in asins if prev.get(a) in LABELS}
    cooled = [a for a, l in prev.items() if l == "surge" and current.get(a) in ("watch", "low")]
    return {"first": False, "prev_id": prev_run.get("id"), "still": still, "new": new, "moved_from": moved_from,
            "cooled": cooled, "covered": covered}

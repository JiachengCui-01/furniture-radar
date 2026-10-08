"""与上期对比：新进入各板块的、标签变化的、爆火回落的。"""
from __future__ import annotations

LABELS = ("surge", "potential", "hot", "fake")


def compare(prev_run: dict | None, current: dict[str, str]) -> dict:
    if not prev_run:
        return {"first": True, "new": {label: [] for label in LABELS}, "transitions": [], "cooled": []}
    prev = {asin: value[0] for asin, value in (prev_run.get("labels") or {}).items()}
    new = {label: [a for a, l in current.items() if l == label and prev.get(a) != label] for label in LABELS}
    transitions = [
        {"asin": a, "from": prev[a], "to": l}
        for a, l in current.items()
        if a in prev and prev[a] != l and (l in LABELS or prev[a] in LABELS) and l != "watch"
    ]
    cooled = [a for a, l in prev.items() if l == "surge" and current.get(a) in ("watch", "hot")]
    return {"first": False, "prev_id": prev_run.get("id"), "new": new, "transitions": transitions,
            "cooled": cooled}

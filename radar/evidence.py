"""核查证据的日期、商品身份与观测快照；不使用机器当前日期。"""
from __future__ import annotations

from datetime import date

from . import clock
from .series import Daily

RECHECK_DAYS = 14


def observations(rec: dict, upto: date | None = None) -> list[dict]:
    rows = rec.get("obs") or []
    if upto is None:
        return list(rows)
    return sorted((r for r in rows if (day := clock.parse_day(r.get("date"))) is not None and day <= upto),
                  key=lambda r: r["date"])


def latest_observation(rec: dict, upto: date | None = None) -> dict:
    rows = observations(rec, upto)
    return rows[-1] if rows else {}


def fresh(check: dict | None, today: date | None, data_date: date | None = None) -> bool:
    checked = clock.parse_day((check or {}).get("checked"))
    if today is None or checked is None or not 0 <= (today - checked).days < RECHECK_DAYS:
        return False
    end = clock.parse_day(check.get("as_of"))
    reference = max(today, data_date or today)
    return end is None or 0 <= (reference - end).days < RECHECK_DAYS


def family_changed(before: dict, after: dict) -> bool:
    a, b = before.get("parent"), after.get("parent")
    va, vb = before.get("variations"), after.get("variations")
    return bool((a and b and a != b) or (va and vb and abs(vb - va) >= max(2, 0.3 * va)))


def current_family(rec: dict, today: date | None) -> dict:
    detail = (rec.get("checks") or {}).get("detail") or {}
    return {key: (detail.get(key) if fresh(detail, today) and detail.get(key) is not None else rec.get(key))
            for key in ("parent", "variations")}


def usable_check(rec: dict, kind: str, today: date | None, data_date: date | None = None) -> dict | None:
    check = (rec.get("checks") or {}).get(kind)
    if data_date is None:
        d = Daily.from_record(rec.get("series"))
        data_date = d.as_of() if d else today
    if not fresh(check, today, data_date):
        return None
    if kind == "review":
        if not check.get("n") and check.get("total") != 0:
            return None  # 查询有结果却过滤为零、或缺少总数，不证明没有评论。
        family = current_family(rec, today)
        old_parent = check.get("parent")
        if not old_parent:
            old_parent = latest_observation(rec, clock.parse_day(check["checked"])).get("parent")
        if old_parent and family.get("parent") and old_parent != family["parent"]:
            return None
        if check.get("variations") and family_changed(check, family):
            return None
        # 旧缓存没有字段有效样本数，无法知道当时是否把缺失标志当作 False；
        # 保留可验证的评论条数/集中度，未知比例既不加分，也不用于 Vine 抵扣。
        if "verified_n" not in check or "vine_n" not in check:
            check = {**check}
            if "verified_n" not in check:
                check["unverified_share"] = None
            if "vine_n" not in check:
                check["vine_share"] = None
    return check


def at(rec: dict, today: date, *, data_date: date | None = None) -> dict:
    """只筛选内存视图；原始核查保留供审计，失效时不计分也不作豁免。"""
    return {**rec, "obs": observations(rec, data_date or today),
            "checks": {kind: check for kind in (rec.get("checks") or {})
                       if (check := usable_check(rec, kind, today, data_date or today)) is not None}}
